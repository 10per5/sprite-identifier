"""label — AI auto-tagging via OrcaRouter (deepseek-v4-flash-vision-exp).

Sends each sprite image to a vision model and writes structured
name/category/tags back into the unified metadata store. Skips sprites that
already have VLLM tags. Catalogue assets keep their name+category; VLLM
improves vague ones and adds tags.

Requires ORCAROUTER_API_KEY. The key check, API probe, progress file, resume,
cross-process lock, and cost tracking all live in the shared `run_llm_pass`
(lib.core.llm_pass). This module only supplies the two callbacks that differ
from `infer` (`_should_process` / `_apply_result`) plus the one label-only
convenience: moving already-tagged sprites out of the working folder.
"""
import argparse
import hashlib
import os

from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import load as load_metadata, entry_for
from lib.core.llm_pass import run_llm_pass
from lib.domains import add_domain_argument, resolve_domain
from lib.llm.orcarouter import VISION_MODEL
from lib.util.paths import project_path, iter_images

# OrcaRouter deepseek-v4-flash-vision-exp pricing (off-peak)
INPUT_COST_PER_1M = 0.220
OUTPUT_COST_PER_1M = 0.660
EST_INPUT_TOKENS = 2500   # image base64 + text prompt
EST_OUTPUT_TOKENS = 150   # JSON response
COST_PER_SPRITE = (EST_INPUT_TOKENS / 1_000_000) * INPUT_COST_PER_1M + \
                  (EST_OUTPUT_TOKENS / 1_000_000) * OUTPUT_COST_PER_1M


def _should_process(entry):
    """A sprite still needs labeling if it has no VLLM tags yet."""
    return not entry.get("vllm_tags")


def _apply_result(entry, result):
    """Merge a successful VLLM response into the entry."""
    entry["name"] = result["name"]
    entry["category"] = result["category"]
    entry["subcategory"] = result.get("subcategory", "")
    entry["tags"] = result.get("tags", [])
    entry["vllm_name"] = result["name"]
    entry["vllm_category"] = result["category"]
    entry["vllm_tags"] = result.get("tags", [])
    entry["vllm_confidence"] = result.get("confidence")
    entry["vllm_notes"] = result.get("notes", "")
    entry["manual"] = entry.get("manual", False)


def cmd_label(args):
    domain = resolve_domain(args)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    sprites_dir = project_path(getattr(args, "sprites", None) or domain.source_dir)
    recursive = getattr(args, "recursive", None)
    if recursive is None:
        recursive = domain.recursive
    api_key = os.environ.get("ORCAROUTER_API_KEY")

    # Move already-tagged sprites out of sprites/ so the folder only holds
    # untagged ones for another pass or manual labeling.
    if args.move_tagged_to:
        meta = load_metadata(meta_path)
        done_dir = project_path(args.move_tagged_to)
        done_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for p in sorted(iter_images(sprites_dir, recursive=recursive,
                                    extensions=domain.extensions)):
            ph = pixel_hash(p)
            entry = entry_for(meta, ph)
            if entry.get("vllm_tags"):
                dest = done_dir / p.name
                if dest.exists():
                    dest = done_dir / f"{p.stem}_{hashlib.md5(p.name.encode()).hexdigest()[:8]}{p.suffix}"
                p.replace(dest)
                moved += 1
        print(f"Moved {moved} already-tagged sprite(s) to {done_dir}")

    run_llm_pass(
        meta_path, sprites_dir,
        api_key=api_key,
        model=VISION_MODEL,
        use_image=True,
        cost_per_sprite=COST_PER_SPRITE,
        should_process=_should_process,
        apply_result=_apply_result,
        debug=args.debug,
        prompts=domain.prompts,
        recursive=recursive,
        extensions=domain.extensions,
    )


def register(subparsers):
    p = subparsers.add_parser(
        "label",
        help="AI tag labeling for sprites via OrcaRouter (requires ORCAROUTER_API_KEY)",
    )
    add_domain_argument(p)
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--sprites", default=None,
                   help="image folder to label (default: domain source folder)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.add_argument("--move-tagged-to", default=None,
                   help="move already-tagged sprites into this folder after tagging")
    p.add_argument("--debug", action="store_true",
                   help="print debug info for failed VLLM responses")
    p.set_defaults(func=cmd_label)
