"""infer — re-infer category/subcategory/tags for already-named sprites via LLM.

Defaults to a CHEAP TEXT-ONLY model (no sprite image is sent). The sprites are
already named, so category/subcategory/most tags can be derived from the name +
taxonomy without vision. Visual-only tags (actual color, glow, material sheen)
are the only thing lost vs. vision — pass --vision to opt into a vision model.

Requires ORCAROUTER_API_KEY. Shares the OrcaRouter client with label.py and the
shared `run_llm_pass` (lib.core.llm_pass), which owns the progress file, resume,
cross-process lock, and cost tracking. This module only supplies the two
callbacks that differ from `label` (`_should_process` / `_apply_result`).
"""
import argparse
import os

from lib.core.llm_pass import run_llm_pass
from lib.core.quality import apply_llm_result, is_placeholder_name, llm_retry_allowed
from lib.domains import add_domain_argument, resolve_domain
from lib.llm.orcarouter import TEXT_MODEL, VISION_MODEL
from lib.util.paths import project_path

# Rough per-sprite cost estimates (off-peak) for the known models.
MODEL_COST = {
    TEXT_MODEL: (0.020, 0.060),    # ($/1M input, $/1M output) — cheap text model
    VISION_MODEL: (0.220, 0.660),  # vision model
}
EST_INPUT_TOKENS = 1600    # text prompt only (no image), incl. spell tag vocabulary
EST_OUTPUT_TOKENS = 120    # JSON response


def _cost_per_sprite(model):
    costs = MODEL_COST.get(model)
    if not costs:
        return None
    in_c, out_c = costs
    return (EST_INPUT_TOKENS / 1_000_000) * in_c + \
           (EST_OUTPUT_TOKENS / 1_000_000) * out_c


def _should_process(entry, force=False):
    """A named sprite still needs inference if any field is missing.

    ``force`` refreshes every named sprite on disk, which is what you want after
    a manual rename: the name stays, but category/subcategory/tags are redone.

    Placeholder names (``icon43``, ``unnamed``) are never processed: the name
    is the model's only evidence here.

    Renames are also auto-detected: when ``name`` no longer matches the model's
    previous ``vllm_name``, the category/tags may be stale, so the entry is
    refreshed once even though all fields are populated.
    """
    if is_placeholder_name(entry.get("name")):
        # `icon43` gives a text-only model nothing to work with; these need
        # the vision `label` pass or the manual labeling page.
        return False
    if force:
        return True
    if not llm_retry_allowed(entry):
        return False
    if entry.get("needs_review"):
        return True
    has_category = bool(entry.get("category"))
    has_subcategory = bool(entry.get("subcategory"))
    has_tags = bool(entry.get("tags"))
    if not (has_category and has_subcategory and has_tags):
        return True
    vllm_name = entry.get("vllm_name")
    return bool(vllm_name) and vllm_name != entry.get("name")


def _apply_result(entry, result, domain):
    """Refine the entry's category/subcategory/tags. Does NOT overwrite `name`.

    ``vllm_name`` is set to the current name so a renamed entry is refreshed
    once, not on every run. Incomplete responses leave the entry pending.
    """
    return apply_llm_result(entry, result, domain, take_name=False)


def cmd_infer(args):
    domain = resolve_domain(args)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    sprites_dir = project_path(getattr(args, "sprites", None) or domain.source_dir)
    recursive = getattr(args, "recursive", None)
    if recursive is None:
        recursive = domain.recursive
    api_key = os.environ.get("ORCAROUTER_API_KEY")

    # Default to a cheap TEXT-ONLY model. --vision opts into the vision model
    # (and auto-selects VISION_MODEL if --model was left at its default).
    model = args.model
    use_image = args.vision
    if use_image and model == TEXT_MODEL:
        model = VISION_MODEL
    print(f"Mode: model={model} ({'vision' if use_image else 'text-only (cheap)'})")

    # Default fallback: the vision model is currently responsive, so escalate to
    # it if the cheap text model is slow/erroring. The fallback inherits the same
    # use_image setting (text-only unless --vision was passed).
    fallback_models = args.fallback_model
    if fallback_models is None:
        fallback_models = [VISION_MODEL]
    print(f"  fallback models: {', '.join(fallback_models)} "
          f"(timeout={args.timeout}s, deadline={args.deadline}s/sprite)")

    if args.local:
        print("--local requested but local inference not yet implemented; falling back to LLM.")

    cost_per = _cost_per_sprite(model)
    force = getattr(args, "force", False)

    run_llm_pass(
        meta_path, sprites_dir,
        api_key=api_key,
        model=model,
        use_image=use_image,
        cost_per_sprite=cost_per,
        should_process=lambda entry: _should_process(entry, force=force),
        apply_result=lambda entry, result: _apply_result(entry, result, domain),
        timeout=args.timeout,
        deadline=args.deadline,
        fallback_models=fallback_models,
        prompts=domain.prompts,
        recursive=recursive,
        extensions=domain.extensions,
    )


def register(subparsers):
    """Add the `infer` subcommand to an argparse subparsers collection."""
    p = subparsers.add_parser(
        "infer",
        help="re-infer category/subcategory/tags for named sprites via LLM",
    )
    add_domain_argument(p)
    p.add_argument("--sprites", default=None,
                   help="image folder to infer (default: domain source folder)")
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.add_argument("--local", action="store_true",
                   help="use local inference instead of LLM (placeholder, future drop-in)")
    p.add_argument("--force", action="store_true",
                   help="re-infer every named sprite on disk, even if category/tags are "
                        "already present (use after renaming)")
    p.add_argument("--model", default=TEXT_MODEL,
                   help=f"OrcaRouter model (default: {TEXT_MODEL}). Use a vision model with --vision for visual tags.")
    p.add_argument("--vision", action="store_true",
                   help="send the SPRITE IMAGE to a vision model (more accurate visual tags, costs more). "
                        "Default is text-only.")
    p.add_argument("--timeout", type=int, default=20,
                   help="per-request socket timeout in seconds (default 20). "
                        "Capped by the remaining --deadline budget.")
    p.add_argument("--deadline", type=int, default=60,
                   help="max wall-clock seconds to spend per sprite across all models "
                        "+ retries (default 60). On exceeding it the sprite is skipped "
                        "instead of stalling the run.")
    p.add_argument("--fallback-model", action="append", default=None, metavar="MODEL",
                   help=f"model to try if the primary is slow/errors (repeatable). "
                        f"Default: {VISION_MODEL} (currently responsive).")
    p.set_defaults(func=cmd_infer)
