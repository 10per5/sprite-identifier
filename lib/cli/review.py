"""review — manual labeling & approve/reject pages.

`review-label`    generates an offline grid labeling page
                  (out/<domain>/label_review.html)
                  that shows each sprite alongside its best catalogue matches.
`review-approval` generates a single-sprite approve/reject page
                  (out/<domain>/approve_reject.html) for the post-VLLM pass.
`move-approved`   moves approved sprites out of the working folder based on the
                  saved decisions JSON so only failed/rejected sprites remain
                  visible.
"""
import argparse
import hashlib
import json
from pathlib import Path

from lib.cli._html import render_template, relative_prefix
from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import (
    entry_for,
    load as load_metadata,
    metadata_lock,
)
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import atomic_save, project_path, iter_images


def _recursive_for(args, domain):
    recursive = getattr(args, "recursive", None)
    return domain.recursive if recursive is None else recursive


def _display_path(path):
    """CWD-relative path when possible (review pages assume project-local assets)."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _id_to_hash(dirs, recursive, extensions):
    """Map sprite id (file stem) -> pixel_hash across all known image folders.

    Decisions are keyed by the id the review page was generated with. A file may
    since have been moved to the approved folder, so scan both the working and
    destination folders before merging.
    """
    lookup = {}
    for directory in dirs:
        if not directory.exists():
            continue
        for path in iter_images(directory, recursive=recursive,
                                extensions=extensions):
            lookup[path.stem] = pixel_hash(path)
    return lookup


def _merge_label_hashes(lookup, labels_path):
    """Add id -> pixel_hash mappings from an exported labels JSON.

    Old review ids can disappear from disk after `build`, but labels.json keeps
    the id/hash mapping, so historical manual edits can still be applied.
    """
    path = project_path(labels_path)
    if not path.exists():
        return lookup
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return lookup
    for sid, record in (data.get("labels") or {}).items():
        if isinstance(record, dict) and record.get("pixel_hash"):
            lookup.setdefault(sid, record["pixel_hash"])
    return lookup


def merge_decisions(meta_path, decisions, id_lookup):
    """Write manual name/category/subcategory/tags edits into metadata.

    Decisions are hash-matched when they carry a ``pixel_hash`` (new exports),
    otherwise by the reviewed sprite id via ``id_lookup``. This runs for every
    record regardless of the approve/reject decision, so renames of rejected or
    still-pending sprites are not lost.

    Returns ``(applied, unmatched)`` where ``unmatched`` is a list of sprite ids
    that could not be resolved to an on-disk image or metadata hash.
    """
    applied = 0
    unmatched = []
    with metadata_lock(meta_path):
        meta = load_metadata(meta_path)
        for sid, record in decisions.items():
            if not isinstance(record, dict):
                continue
            phash = record.get("pixel_hash") or id_lookup.get(sid)
            if not phash:
                unmatched.append(sid)
                continue

            entry = meta["entries"].setdefault(phash, {})
            if record.get("name"):
                entry["name"] = record["name"]
            if record.get("category"):
                entry["category"] = record["category"]
            if "subcategory" in record:
                entry["subcategory"] = record.get("subcategory") or ""
            if isinstance(record.get("tags"), list):
                entry["tags"] = record["tags"]
            entry["pixel_hash"] = phash
            entry["manual"] = True
            applied += 1
        atomic_save(meta, meta_path, lock=False)  # outer metadata_lock held
    return applied, unmatched


def _render_review_page(sprite_data, initial_labels, categories, output_path,
                        download_name):
    html = render_template(
        "sprite_label_review.html.j2",
        sprites=json.dumps(sprite_data, ensure_ascii=False),
        labels=json.dumps(initial_labels, ensure_ascii=False),
        categories=json.dumps(sorted(categories), ensure_ascii=False),
        asset_prefix=relative_prefix(output_path),
        download_name=download_name,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def cmd_review_label(args):
    domain = resolve_domain(args)
    sprites_dir = project_path(getattr(args, "sprites", None) or domain.source_dir)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    recursive = _recursive_for(args, domain)
    sprite_files = iter_images(sprites_dir, recursive=recursive,
                               extensions=domain.extensions)
    if not sprite_files:
        raise SystemExit("Sprite folder must contain image files")

    # Load unified metadata for initial labels + candidate matching.
    meta = load_metadata(meta_path)

    # Build pixel_hash -> sprite_id mapping for this sprite folder.
    hash_to_sprite_id = {}
    for path in sprite_files:
        hash_to_sprite_id[pixel_hash(path)] = path.stem

    # Build initial_labels keyed by sprite_id from unified metadata.
    initial_labels = {}
    for path in sprite_files:
        ph = pixel_hash(path)
        entry = entry_for(meta, ph)
        initial_labels[path.stem] = {
            "name": entry.get("name", ""),
            "category": entry.get("category", ""),
            "reference": entry.get("path"),
            "pixel_hash": ph,
            "source": entry.get("source", ""),
        }

    # Build catalogue candidate entries from the domain's reference sources.
    catalogue_entries = []
    for phash, entry in meta["entries"].items():
        if not isinstance(entry, dict):
            continue
        if entry.get("source") not in domain.reference_sources:
            continue
        catalogue_entries.append({
            "path": entry.get("path", ""),
            "name": entry.get("name", ""),
            "category": entry.get("category", ""),
            "pixel_hash": phash,
        })

    sprite_data = []
    top_count = max(1, args.top)
    for path in sprite_files:
        ph = pixel_hash(path)
        # Match sprite to catalogue entries by pixel_hash (exact match = same image).
        candidates = []
        for ce in catalogue_entries:
            if ce["pixel_hash"] == ph:
                candidates.append({
                    "path": ce["path"],
                    "name": ce["name"],
                    "category": ce["category"],
                    "pixel_hash": ce["pixel_hash"],
                    "score": 1.0,
                })
                if len(candidates) >= top_count:
                    break
        sprite_data.append({
            "id": path.stem,
            "path": _display_path(path),
            "pixel_hash": ph,
            "candidates": candidates,
        })

    label_path = project_path(getattr(args, "labels", None) or domain.labels_json)
    output = project_path(getattr(args, "output", None) or domain.label_review_html)
    _render_review_page(sprite_data, initial_labels, domain.categories, output,
                        download_name=label_path.name)
    print(f"Built review page for {len(sprite_data)} sprites: {output}")
    print(f"Open the HTML locally; export labels to {label_path}")
    print(f"Then run: sprite.py auto-name --labels {label_path} --sprites {sprites_dir}")


def cmd_review_approval(args):
    """Generate a single-sprite approve/reject page for post-VLLM review."""
    domain = resolve_domain(args)
    sprites_dir = project_path(getattr(args, "sprites", None) or domain.source_dir)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    output = project_path(getattr(args, "output", None) or domain.approval_html)
    decisions_path = project_path(getattr(args, "decisions", None) or domain.approval_json)
    recursive = _recursive_for(args, domain)
    sprite_files = iter_images(sprites_dir, recursive=recursive,
                               extensions=domain.extensions)
    if not sprite_files:
        raise SystemExit("Sprite folder must contain image files")

    meta = load_metadata(meta_path)

    sprite_data = []
    for path in sprite_files:
        ph = pixel_hash(path)
        entry = entry_for(meta, ph, {})
        sprite_data.append({
            "id": path.stem,
            "path": _display_path(path),
            "pixel_hash": ph,
            "name": entry.get("name", ""),
            "category": entry.get("category", ""),
            "subcategory": entry.get("subcategory", ""),
            "tags": entry.get("tags", []),
            "vllm_name": entry.get("vllm_name", ""),
            "vllm_category": entry.get("vllm_category", ""),
            "vllm_tags": entry.get("vllm_tags", []),
        })

    initial_decisions = {}
    if decisions_path.exists():
        try:
            initial_decisions = json.loads(
                decisions_path.read_text(encoding="utf-8")
            ).get("decisions", {})
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read {decisions_path}: {exc}")

    current_ids = {sprite["id"] for sprite in sprite_data}
    stale_ids = sorted(set(initial_decisions) - current_ids)
    if stale_ids:
        preview = ", ".join(stale_ids[:8])
        if len(stale_ids) > 8:
            preview += ", …"
        print(f"Warning: {len(stale_ids)} saved decision(s) are for sprites not in "
              f"this review set and will be ignored by the page counters: {preview}")

    html = render_template(
        "sprite_approve_reject.html.j2",
        sprites=json.dumps(sprite_data, ensure_ascii=False),
        decisions=json.dumps(initial_decisions, ensure_ascii=False),
        categories=json.dumps(sorted(domain.categories), ensure_ascii=False),
        asset_prefix=relative_prefix(output),
        download_name=decisions_path.name,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"Built review pass page for {len(sprite_data)} sprites: {output}")
    print(f"Open locally; Y = approve, N = reject, Arrow keys = nav, Ctrl+S = save JSON")


def cmd_move_approved(args):
    """Commit manual edits and move approved sprites out of the working folder."""
    domain = resolve_domain(args)
    decisions_path = project_path(getattr(args, "decisions", None) or domain.approval_json)
    if not decisions_path.exists():
        raise SystemExit(f"Decisions file not found: {decisions_path}")
    try:
        data = json.loads(decisions_path.read_text(encoding="utf-8"))
        decisions = data.get("decisions", {})
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"Could not read decisions file: {exc}")
    if not decisions:
        raise SystemExit("No decisions found in decisions file")

    sprites_dir = project_path(getattr(args, "sprites", None) or domain.source_dir)
    done_dir = project_path(getattr(args, "to", None) or domain.approved_dir)
    done_dir.mkdir(parents=True, exist_ok=True)
    recursive = _recursive_for(args, domain)

    # Commit renames/edits before any files move. Scan both the working folder
    # and the approved folder because earlier `move-approved` runs may already
    # have relocated the ids that the decisions JSON refers to.
    if not getattr(args, "no_merge", False):
        meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
        lookup_dirs = [sprites_dir, done_dir]
        for src in getattr(domain, "build_sources", ()):
            lookup_dirs.append(project_path(src))
        lookup = _id_to_hash(lookup_dirs, recursive, domain.extensions)
        lookup = _merge_label_hashes(lookup, domain.labels_json)
        applied, unmatched = merge_decisions(meta_path, decisions, lookup)
        print(f"Merged {applied} decision(s) into {meta_path}")
        if unmatched:
            print(f"  {len(unmatched)} decision(s) had no matching image/hash "
                  f"(first: {', '.join(sorted(unmatched)[:5])})")

    if getattr(args, "merge_only", False):
        return

    moved = skipped = 0
    for path in iter_images(sprites_dir, recursive=recursive,
                            extensions=domain.extensions):
        sid = path.stem
        d = decisions.get(sid)
        if not d:
            continue
        decision = d.get("decision", d) if isinstance(d, dict) else d
        if decision != "y":
            continue
        dest = done_dir / path.name
        if dest.exists():
            dest = done_dir / f"{sid}_{hashlib.md5(path.name.encode()).hexdigest()[:8]}{path.suffix}"
        path.replace(dest)
        moved += 1
    print(f"Moved {moved} approved sprite(s) to {done_dir}, skipped {skipped}")


def register(subparsers):
    p = subparsers.add_parser(
        "review-label",
        help="generate an offline visual labeling page",
    )
    add_domain_argument(p)
    p.add_argument("--sprites", default=None,
                   help="image folder to review (default: domain source folder)")
    p.add_argument("--labels", default=None, help="labels export path (default: domain labels JSON)")
    p.add_argument("--metadata", default=None, help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--output", default=None, help="HTML output path (default: domain review page)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.add_argument("--top", type=int, default=6)
    p.set_defaults(func=cmd_review_label)

    p = subparsers.add_parser(
        "review-approval",
        help="single-sprite approve/reject page for post-VLLM review",
    )
    add_domain_argument(p)
    p.add_argument("--sprites", default=None,
                   help="image folder to review (default: domain source folder)")
    p.add_argument("--metadata", default=None, help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--output", default=None, help="HTML output path (default: domain approval page)")
    p.add_argument("--decisions", default=None, help="saved decisions JSON (default: domain decisions JSON)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.set_defaults(func=cmd_review_approval)

    p = subparsers.add_parser(
        "move-approved",
        help="commit manual edits and move approved sprites out of the working folder",
    )
    add_domain_argument(p)
    p.add_argument("--sprites", default=None,
                   help="image folder to move from (default: domain source folder)")
    p.add_argument("--decisions", default=None, help="saved decisions JSON (default: domain decisions JSON)")
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--to", default=None, help="destination folder (default: domain approved folder)")
    p.add_argument("--no-merge", action="store_true",
                   help="move files only; do not write decision names/tags into metadata")
    p.add_argument("--merge-only", action="store_true",
                   help="commit decision names/tags into metadata without moving files")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.set_defaults(func=cmd_move_approved)
