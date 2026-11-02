"""Guided "0-to-hero" wizard for the sprite pipeline.

Running ``sprite.py`` with no arguments (or ``sprite.py guided``) launches this
interactive assistant instead of forcing you to memorise every subcommand. It
detects the current project state, shows what still needs doing, and walks you
through each step with safe defaults — you only type when you want to change a
path or skip a step.
"""
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace as _NS

from lib.cli import (
    normalize as _normalize_mod,
    split as _split_mod,
    dedup as _dedup_mod,
    catalog as _catalog_mod,
    label as _label_mod,
    infer as _infer_mod,
    index as _index_mod,
    review as _review_mod,
    view as _view_mod,
)
from lib.core.sprite_metadata import load as load_metadata
from lib.core.pixel_hash import pixel_hash
from lib.domains import get_domain
from lib.domains.base import Domain
from lib.llm.orcarouter import TEXT_MODEL
from lib.util.paths import project_path, iter_images

_SPRITE_NAME_RE = re.compile(r"^sprite_\d{4}\.png$")


# ---------------------------------------------------------------------------
# Environment snapshot
# ---------------------------------------------------------------------------
def _snapshot(domain: Domain):
    api_key = bool(os.environ.get("ORCAROUTER_API_KEY"))
    # The wizard works one source at a time: `domain.source_dir` is what the
    # header advertises and what `index` scans. Domains with a second pack
    # (spells' already-named icons) get it indexed only when the step that
    # needs it runs.
    primary_dir = project_path(domain.source_dir)
    images = (iter_images(primary_dir, recursive=domain.recursive,
                          extensions=domain.extensions)
              if primary_dir.is_dir() else [])
    build_sources = [project_path(path) for path in domain.build_sources]

    meta_path = project_path(domain.metadata)
    meta = load_metadata(meta_path) if meta_path.exists() else {}
    entries = meta.get("entries", {})

    primary_source = Path(domain.source_dir).name
    ref_sources = {e.get("source") for e in entries.values() if isinstance(e, dict)}
    reference_sources = set(domain.reference_sources)
    if reference_sources:
        indexed = bool(ref_sources & reference_sources)
    else:
        # Only the advertised source counts; indexing the named pack later
        # must not make this step look done.
        indexed = primary_source in ref_sources

    if reference_sources:
        named_nonref = [
            e for e in entries.values()
            if isinstance(e, dict) and e.get("source") not in reference_sources
        ]
        label_entries = named_nonref
        infer_entries = named_nonref
        infer_dir_path = primary_dir
    else:
        # Spells: the two packs are handled independently. Match disk images to
        # metadata by pixel hash so entries created by `infer` (which may lack
        # a `source` field) still count once they exist.
        named_nonref = [e for e in entries.values() if isinstance(e, dict)]
        label_dir = primary_dir
        infer_dir_path = project_path(domain.named_source_dir or domain.source_dir)
        if not infer_dir_path.is_dir():
            # The named pack may have been renamed/moved; fall back to the
            # advertised source rather than silently reporting zero icons.
            infer_dir_path = primary_dir
        label_images = iter_images(label_dir, recursive=domain.recursive,
                                   extensions=domain.extensions)
        infer_images = iter_images(infer_dir_path, recursive=domain.recursive,
                                   extensions=domain.extensions)
        label_entries = [entries.get(pixel_hash(p), {}) for p in label_images]
        infer_entries = [entries.get(pixel_hash(p), {}) for p in infer_images]

    unnamed = [e for e in named_nonref if not e.get("name")]
    labeled = bool(label_entries) and all(
        e.get("vllm_tags") for e in label_entries
    )
    infer_done = bool(infer_entries) and all(
        e.get("category") and e.get("subcategory") and e.get("tags")
        for e in infer_entries
    )

    catalog_toml = project_path(f"{domain.catalog}/catalog.toml")
    built = catalog_toml.exists()

    if domain.key == "items":
        normalized = bool(images) and all(
            _SPRITE_NAME_RE.match(p.name) for p in images
        )
    else:
        normalized = True  # spell icons keep pack/folder names

    # Folders to index: item reference art (dcss/dlss) or the advertised spell
    # source pack. Spells' named pack is not indexed up front.
    index_dirs = []
    if domain.reference_sources:
        for name in domain.reference_sources:
            for cand in (project_path(name), project_path("in") / name):
                if cand.is_dir():
                    index_dirs.append(cand)
                    break
    elif primary_dir.is_dir():
        index_dirs = [primary_dir]

    # Items build from the approved folder (move-approved empties the source
    # folder). Spells build from their named + unnamed packs, plus approved
    # images if the optional CLI approval flow was used.
    if domain.key == "items":
        build_dirs = [project_path(domain.approved_dir)]
    else:
        build_dirs = [d for d in build_sources if d.is_dir()]
        approved = project_path(domain.approved_dir)
        if approved.is_dir():
            build_dirs.append(approved)

    return _NS(
        domain=domain,
        api_key=api_key,
        sprites=images,
        images=images,
        meta=meta,
        entries=entries,
        indexed=indexed,
        labeled=labeled,
        unnamed=unnamed,
        infer_done=infer_done,
        label_entries=label_entries,
        infer_entries=infer_entries,
        built=built,
        normalized=normalized,
        index_dirs=index_dirs,
        build_dirs=build_dirs,
        label_dir=domain.source_dir,
        infer_dir=str(infer_dir_path),
        named_nonref=named_nonref,
        meta_path=meta_path,
    )


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------
def _ns(**kw):
    return _NS(**kw)


def _step_normalize(ctx, opts):
    _normalize_mod.normalize(_ns(sprites=opts.get("sprites", ctx.domain.source_dir),
                                 size=32, dry_run=False))


def _step_dedup(ctx, opts):
    # Report + write recolor groups; never auto-delete in the wizard.
    print("Scanning for near-duplicates (report only — nothing is deleted).")
    _dedup_mod.cmd_dedup(_ns(
        domain=ctx.domain.key,
        dir=opts.get("dir", ctx.domain.source_dir), threshold=0.98, size=32,
        apply=False, yes=False, recolor_map=None,
        recursive=opts.get("recursive", ctx.domain.recursive),
        metadata=opts.get("metadata", ctx.domain.metadata),
    ))


def _step_index(ctx, opts):
    ran = False
    for directory in ctx.index_dirs:
        print(f"\n-- indexing {directory} --")
        _index_mod.cmd_index(_ns(
            domain=ctx.domain.key,
            dir=str(directory), source=None,
            metadata=opts.get("metadata", ctx.domain.metadata),
        ))
        ran = True
    if not ran:
        print(f"No source folders found to index for {ctx.domain.title} — skipping.")


def _step_review_label(ctx, opts):
    _review_mod.cmd_review_label(_ns(
        domain=ctx.domain.key,
        sprites=opts.get("sprites", ctx.label_dir),
        labels=opts.get("labels", ctx.domain.labels_json),
        metadata=opts.get("metadata", ctx.domain.metadata),
        output=opts.get("output", ctx.domain.label_review_html),
        top=6,
    ))


def _step_auto_name(ctx, opts):
    _index_mod.cmd_auto_name(_ns(
        sprites=opts.get("sprites", ctx.label_dir),
        labels=opts.get("labels", ctx.domain.labels_json),
        metadata=opts.get("metadata", ctx.domain.metadata),
    ))


def _step_label(ctx, opts):
    if not ctx.api_key:
        print("ORCAROUTER_API_KEY is not set — cannot run the AI labeling step.")
        print("Export it first, e.g.  export ORCAROUTER_API_KEY=your_key")
        return
    _label_mod.cmd_label(_ns(
        domain=ctx.domain.key,
        metadata=opts.get("metadata", ctx.domain.metadata),
        sprites=opts.get("sprites", ctx.label_dir),
        move_tagged_to=None, debug=False,
    ))


def _step_infer(ctx, opts):
    if not ctx.api_key:
        print("ORCAROUTER_API_KEY is not set — cannot run the AI inference step.")
        print("Export it first, e.g.  export ORCAROUTER_API_KEY=your_key")
        return
    _infer_mod.cmd_infer(_ns(
        domain=ctx.domain.key,
        sprites=opts.get("sprites", ctx.infer_dir),
        metadata=opts.get("metadata", ctx.domain.metadata),
        local=False, model=TEXT_MODEL, vision=False,
        timeout=20, deadline=60, fallback_model=None,
    ))


def _step_review_approval(ctx, opts):
    _review_mod.cmd_review_approval(_ns(
        domain=ctx.domain.key,
        sprites=opts.get("sprites", ctx.label_dir),
        metadata=opts.get("metadata", ctx.domain.metadata),
        output=opts.get("output", ctx.domain.approval_html),
        decisions=opts.get("decisions", ctx.domain.approval_json),
    ))


def _step_move_approved(ctx, opts):
    _review_mod.cmd_move_approved(_ns(
        domain=ctx.domain.key,
        sprites=opts.get("sprites", ctx.label_dir),
        decisions=opts.get("decisions", ctx.domain.approval_json),
        to=opts.get("to", ctx.domain.approved_dir),
    ))


def _step_viewer(ctx, opts):
    _view_mod.cmd_viewer(_ns(
        domain=ctx.domain.key,
        target=opts.get("target", ctx.domain.catalog),
        output=opts.get("output", ctx.domain.catalog_view_html),
    ))


def _step_build(ctx, opts):
    # Items build from out/items/approved because move-approved empties the
    # source folder. Spells build from their source packs (+ approved if any).
    _catalog_mod.build_catalog(_ns(
        domain=ctx.domain.key,
        metadata=opts.get("metadata", ctx.domain.metadata),
        sprites=opts.get("sprites", ctx.build_dirs),
        target=opts.get("target", ctx.domain.catalog),
    ))


STEP_DEFS = {
    "normalize": _NS(key="normalize", title="Normalize sprites",
        desc="Standardize to 32x32 canvas, dedupe exact copies, rename sequentially.",
        needs_key=False, optional=False, run=_step_normalize),
    "dedup": _NS(key="dedup", title="Fuzzy dedup + recolor catalogue",
        desc="Find ~98% near-duplicates and same-shape/different-color recolor groups.",
        needs_key=False, optional=False, run=_step_dedup),
    "index": _NS(key="index", title="Index source/reference folders",
        desc="Scan source packs or reference art into metadata (name+category hints for AI).",
        needs_key=False, optional=False, run=_step_index),
    "review-label": _NS(key="review-label", title="Manual labeling page",
        desc="Generate an offline grid page to label sprites by hand (optional).",
        needs_key=False, optional=True, run=_step_review_label),
    "auto-name": _NS(key="auto-name", title="Auto-name from catalogue",
        desc="Fill names/categories for sprites via pixel_hash matching + exported labels.",
        needs_key=False, optional=False, run=_step_auto_name),
    "label": _NS(key="label", title="AI auto-tag (VLLM)",
        desc="Send each sprite to a vision model for structured name/category/tags.",
        needs_key=True, optional=False, run=_step_label),
    "infer": _NS(key="infer", title="Re-infer missing fields (cheap text model)",
        desc="Fill category/subcategory/tags for named sprites from the name + taxonomy.",
        needs_key=True, optional=False, run=_step_infer),
    "review-approval": _NS(key="review-approval", title="Approve/reject page",
        desc="Single-sprite Y/N review page for the post-AI pass (optional).",
        needs_key=False, optional=True, run=_step_review_approval),
    "move-approved": _NS(key="move-approved", title="Move approved out",
        desc="Move approved sprites out of the source folder so only leftovers remain.",
        needs_key=False, optional=False, run=_step_move_approved),
    "build": _NS(key="build", title="Build catalogued library",
        desc="Assemble <category>/<name>.png + .toml and catalog.toml.",
        needs_key=False, optional=False, run=_step_build),
    "viewer": _NS(key="viewer", title="Browse catalogue",
        desc="Generate an offline HTML viewer for the built catalogue.",
        needs_key=False, optional=False, run=_step_viewer),
}


def _steps_for(domain):
    """Return the wizard steps selected by the domain profile."""
    return [STEP_DEFS[key] for key in domain.wizard_steps]


# ---------------------------------------------------------------------------
# Status + rendering
# ---------------------------------------------------------------------------
def _status_for(step, ctx):
    if step.key == "normalize":
        if not ctx.sprites:
            return "skip", "no sprites yet"
        return ("done", "normalized") if ctx.normalized else ("todo", "not normalized")
    if step.key == "dedup":
        if not ctx.images:
            return "skip", "no source images"
        return "todo", "report only (no deletes)"
    if step.key == "index":
        if not ctx.index_dirs:
            return "skip", "no source folders"
        return (("done", "source indexed") if ctx.indexed
                else ("todo", "not indexed"))
    if step.key == "review-label":
        out = project_path(ctx.domain.label_review_html)
        return ("done", "page generated") if out.exists() else ("optional", "optional")
    if step.key == "auto-name":
        if ctx.unnamed:
            return "todo", f"{len(ctx.unnamed)} unnamed"
        return "done", "all named"
    if step.key == "label":
        if not ctx.images:
            return "skip", "no source images"
        if not ctx.api_key:
            return "warn", "needs ORCAROUTER_API_KEY"
        if not ctx.label_entries:
            return "skip", "no source sprites"
        tagged = sum(1 for e in ctx.label_entries if e.get("vllm_tags"))
        if ctx.labeled:
            return "done", "sprites tagged"
        return "todo", f"{tagged}/{len(ctx.label_entries)} tagged"
    if step.key == "infer":
        if not ctx.api_key:
            return "warn", "needs ORCAROUTER_API_KEY"
        if not ctx.infer_entries:
            return "skip", "nothing named yet"
        complete = sum(
            1 for e in ctx.infer_entries
            if e.get("category") and e.get("subcategory") and e.get("tags")
        )
        if ctx.infer_done:
            return "done", "fields complete"
        return "todo", f"{complete}/{len(ctx.infer_entries)} complete"
    if step.key == "review-approval":
        out = project_path(ctx.domain.approval_html)
        return ("done", "page generated") if out.exists() else ("optional", "optional")
    if step.key == "move-approved":
        return "todo", "ready when approved"
    if step.key == "build":
        return ("done", "library built") if ctx.built else ("todo", "not built")
    if step.key == "viewer":
        if not ctx.built:
            return "skip", "build the catalogue first"
        out = project_path(ctx.domain.catalog_view_html)
        return ("done", "page generated") if out.exists() else ("todo", "not generated")
    return "todo", ""


_GLYPH = {"done": "✓", "todo": "●", "skip": " ", "warn": "⚠", "optional": "○"}


def _render(ctx, steps):
    domain = ctx.domain
    print("\n" + "=" * 70)
    print("  SPRITE WORKSHOP — guided 0 → hero")
    print("=" * 70)
    key_state = "set ✓" if ctx.api_key else "NOT SET ⚠ (AI steps will be skipped)"
    print(f"  ORCAROUTER_API_KEY : {key_state}")
    print(f"  domain            : {domain.title} ({domain.key})")
    print(f"  source            : {domain.source_dir}")
    print(f"  images on disk    : {len(ctx.images)}")
    meta_state = f"{len(ctx.entries)} entries" if ctx.meta else "not created yet"
    print(f"  metadata          : {ctx.meta_path} ({meta_state})")
    print(f"  catalogue         : {domain.catalog} "
          f"{'built ✓' if ctx.built else 'not built'}")
    print("-" * 70)
    for i, step in enumerate(steps, 1):
        glyph, note = _status_for(step, ctx)
        flag = "  (AI)" if step.needs_key else ""
        opt = "  [optional]" if step.optional else ""
        print(f"  {_GLYPH[glyph]} {i:2}) {step.title:<38}{flag}{opt}  — {note}")
    print("-" * 70)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _run_step(step, ctx, opts):
    print(f"\n>>> Running: {step.title}")
    try:
        step.run(ctx, opts)
        print(f">>> Done: {step.title}")
    except SystemExit as exc:
        print(f">>> Stopped: {exc}")
    except Exception as exc:  # keep the wizard alive on errors
        print(f">>> Error in {step.key}: {type(exc).__name__}: {exc}")


def _next_index(ctx, steps):
    for i, step in enumerate(steps):
        glyph, _ = _status_for(step, ctx)
        if glyph == "todo":
            return i
    # fall back to AI steps if the key is present
    if ctx.api_key:
        for i, step in enumerate(steps):
            glyph, _ = _status_for(step, ctx)
            if glyph == "warn":
                return i
    return None


def run_wizard(domain_key=None):
    domain = get_domain(domain_key)
    steps = _steps_for(domain)
    print(f"Welcome to the Sprite Workshop ({domain.title}).")
    print("This walks you through the whole pipeline with sensible defaults.")
    print("At each prompt: [Enter] = run the step, [s] = skip, [q] = quit.")
    while True:
        ctx = _snapshot(domain)
        _render(ctx, steps)

        nxt = _next_index(ctx, steps)
        prompt = "Choose [n] run step n"
        if nxt is not None:
            prompt += f"  [r] run next recommended ({steps[nxt].title})"
        prompt += "  [a] run all remaining (non-AI)  [q] quit\n> "
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            print("\n(EOF — exiting wizard)")
            break

        if choice in ("q", "quit", "exit"):
            break
        if choice == "":
            if nxt is None:
                print("Nothing left to run. 🎉")
                continue
            _run_step(steps[nxt], ctx, {})
            continue
        if choice == "a":
            for step in steps:
                c = _snapshot(domain)
                glyph, _ = _status_for(step, c)
                if glyph in ("done", "skip", "optional", "warn"):
                    continue
                if step.needs_key and not c.api_key:
                    continue
                _run_step(step, c, {})
            continue
        if choice == "r":
            if nxt is None:
                print("Nothing left to run. 🎉")
                continue
            _run_step(steps[nxt], ctx, {})
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(steps):
            step = steps[int(choice) - 1]
            glyph, _ = _status_for(step, ctx)
            if glyph == "warn" and not ctx.api_key:
                _run_step(step, ctx, {})
                continue
            # confirm destructive/long steps
            _run_step(step, ctx, {})
            continue
        if choice in ("s",):
            print("Skipped.")
            continue
        print("Type a step number, 'r' (next), 'a' (all remaining), or 'q' (quit).")


if __name__ == "__main__":
    run_wizard()
