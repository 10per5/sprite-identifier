"""build — assemble the final catalogued sprite library.

Reads the unified metadata + a sprite folder, then writes:

  out/@items/<category>/<name>.png             # base image (domain default)
  out/@items/<category>/<name>.toml            # metadata per sprite
  out/@items/<category>/recolor/<name>.png     # recolor variant
  out/@items/<category>/recolor/<name>.toml    # metadata per recolor variant
  out/@items/catalog.toml                      # master index for fast lookups

The target follows ``--domain`` (items -> out/@items, spells -> out/@spells).
This replaces the old standalone `build_catalog.py` and the `apply` subcommand;
both did the same sprite→category move and now share this single implementation.
Names/categories/tags/recolor groups all come from the domain's metadata file,
keyed by pixel_hash so renames never break links.

Every entry passes the readiness gate in ``lib.core.quality`` first; entries
that fail it stay in their source folder and are listed in
``out/<domain>/build_blocked.json`` (``--allow-unsafe`` skips the gate).
``catalog.toml`` is regenerated from all per-sprite TOMLs on disk, not just the
ones promoted by this run.
"""
import argparse
import collections
import shutil
import tomllib
from pathlib import Path

from PIL import Image

from lib.core.pixel_hash import pixel_hash
from lib.core.quality import entry_issues
from lib.core.sprite_metadata import load as load_metadata, variant_to_base, base_to_variants
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import atomic_save, project_path, sanitize_name, iter_images
from lib.util.toml import toml_dumps


def index_sprites_by_hash(sprites_dirs, extensions=(".png",), recursive=False):
    """Return ``{pixel_hash: path}`` across all source dirs.

    Hashing each source image once is important for large spell packs: the
    catalogue loop looks up hundreds of metadata entries.
    """
    mapping = {}
    for sprites_dir in sprites_dirs:
        for path in iter_images(sprites_dir, recursive=recursive,
                                extensions=extensions):
            try:
                mapping.setdefault(pixel_hash(path), path)
            except Exception:
                continue
    return mapping


def _promote_image(src, dest_png, copy=False):
    """Move (or copy) a PNG, or decode+save an input JPG/WebP as PNG."""
    if src.suffix.lower() == ".png":
        if copy:
            shutil.copy2(src, dest_png)
        else:
            src.replace(dest_png)
        return
    with Image.open(src) as image:
        image.convert("RGBA").save(dest_png, format="PNG")
    if not copy:
        src.unlink()


def _free_png_path(dest_dir, name):
    """``<name>.png``, or ``<name>_<n>.png`` if that file already exists."""
    dest_png = dest_dir / f"{name}.png"
    i = 1
    while dest_png.exists():
        dest_png = dest_dir / f"{name}_{i}.png"
        i += 1
    return dest_png


def index_catalogued_hashes(target_root):
    """Return ``{pixel_hash: png_path}`` for every asset already in the catalogue."""
    found = {}
    for toml_path in sorted(Path(target_root).rglob("*.toml")):
        if toml_path.name == "catalog.toml":
            continue
        png = toml_path.with_suffix(".png")
        if not png.exists():
            continue
        try:
            with open(toml_path, "rb") as handle:
                phash = tomllib.load(handle).get("pixel_hash")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if phash:
            found.setdefault(phash, png)
    return found


def write_catalog_index(target_root, domain):
    """Rewrite ``catalog.toml`` from every per-sprite TOML under ``target_root``.

    The index mirrors what is on disk, so assets promoted by earlier runs stay
    listed (the old per-run index silently dropped them) and reverted assets
    disappear. ``id`` (``<category path>/<file stem>``) is unique even when two
    assets share a ``name``. Returns the number of indexed entries.
    """
    target_root = Path(target_root)
    entries = []
    for toml_path in sorted(target_root.rglob("*.toml")):
        if toml_path.name == "catalog.toml":
            continue
        png = toml_path.with_suffix(".png")
        if not png.exists():
            continue
        try:
            with open(toml_path, "rb") as handle:
                record = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"  Warning: skipping unreadable {toml_path}: {exc}")
            continue
        rel = png.relative_to(target_root).as_posix()
        record["id"] = rel[:-len(".png")]
        record["file"] = rel
        entries.append(record)
    catalog = {
        "version": 1,
        "domain": domain.key,
        "count": len(entries),
        "entries": entries,
    }
    (target_root / "catalog.toml").write_text(toml_dumps(catalog), encoding="utf-8")
    return len(entries)


def _sprite_record(entry, domain, name, category, phash, *, is_recolor,
                   base_name=None, recolor_variants=()):
    reference = entry.get("reference")
    default_source = (domain.reference_sources[0]
                      if reference and domain.reference_sources
                      else domain.default_source)
    confidence = entry.get("confidence")
    if confidence is None:
        confidence = entry.get("vllm_confidence")
    return {
        "name": name,
        "category": category,
        "subcategory": entry.get("subcategory") or "",
        "reference": reference,
        "source": entry.get("source") or default_source,
        "confidence": confidence,
        "pixel_hash": phash,
        "is_recolor": is_recolor,
        "base_image": base_name,
        "recolor_of": base_name,
        "recolor_variants": list(recolor_variants),
        "tags": entry.get("tags") or category.split("/"),
    }


def build_catalog(args):
    domain = resolve_domain(args)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    target_root = project_path(getattr(args, "target", None) or domain.catalog)
    allow_unsafe = getattr(args, "allow_unsafe", False)
    copy = getattr(args, "copy", False)
    min_confidence = getattr(args, "min_confidence", None)

    source_values = getattr(args, "sprites", None) or list(domain.build_sources)
    if isinstance(source_values, str):
        source_values = [source_values]
    sprites_dirs = [project_path(value) for value in source_values]
    recursive = getattr(args, "recursive", None)
    if recursive is None:
        recursive = domain.recursive

    meta = load_metadata(meta_path)
    entries = meta["entries"]
    v2b = variant_to_base(meta)
    variants_by_base = base_to_variants(meta)
    sources_by_hash = index_sprites_by_hash(sprites_dirs, domain.extensions, recursive)

    target_root.mkdir(parents=True, exist_ok=True)

    def issues_for(phash, entry, variant=False):
        if allow_unsafe:
            return []
        issues = entry_issues(entry, domain, min_confidence=min_confidence,
                              variant=variant)
        if variant:
            base = entries.get(v2b[phash])
            if not isinstance(base, dict) or entry_issues(
                    base, domain, min_confidence=min_confidence):
                issues.append("base_blocked")
        return issues

    catalogued = index_catalogued_hashes(target_root)
    moved = skipped = already = 0
    recolor_count = 0
    blocked = []
    built_names = {}

    def promote(phash, entry, *, variant):
        nonlocal moved, skipped, already, recolor_count
        if phash in catalogued:
            already += 1
            return
        src = sources_by_hash.get(phash)
        if src is None or not src.exists():
            skipped += 1
            return
        issues = issues_for(phash, entry, variant=variant)
        if issues:
            blocked.append({
                "pixel_hash": phash,
                "name": entry.get("name"),
                "category": entry.get("category"),
                "source_file": str(src),
                "is_recolor": variant,
                "issues": issues,
            })
            return

        name = sanitize_name(entry.get("name") or "unnamed")
        category = entry.get("category") or "other/unclassified"
        dest_dir = target_root / category.replace("\\", "/")
        if variant:
            dest_dir = dest_dir / "recolor"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_png = _free_png_path(dest_dir, name)
        _promote_image(src, dest_png, copy=copy)
        catalogued[phash] = dest_png
        moved += 1
        built_names.setdefault((category, name), []).append(dest_png.stem)

        if variant:
            recolor_count += 1
            base = entries.get(v2b[phash])
            base_name = (sanitize_name(base.get("name") or "unnamed")
                         if isinstance(base, dict) else f"unknown_{v2b[phash][:8]}")
            record = _sprite_record(entry, domain, name, category, phash,
                                    is_recolor=True, base_name=base_name)
        else:
            recolor_variants = [
                sanitize_name(entries[vh].get("name") or "unnamed")
                for vh in variants_by_base.get(phash, {})
                if isinstance(entries.get(vh), dict)
            ]
            record = _sprite_record(entry, domain, name, category, phash,
                                    is_recolor=False,
                                    recolor_variants=recolor_variants)
        (dest_dir / f"{dest_png.stem}.toml").write_text(
            toml_dumps(record), encoding="utf-8")

    # --- Phase 1: base (non-recolor) images; Phase 2: recolor variants ---
    for phash, entry in entries.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if entry.get("is_recolor") or phash in v2b:
            continue
        promote(phash, entry, variant=False)
    for phash, entry in entries.items():
        if isinstance(entry, dict) and phash in v2b:
            promote(phash, entry, variant=True)

    indexed = write_catalog_index(target_root, domain)
    catalog_path_out = target_root / "catalog.toml"

    verb = "Copied" if copy else "Moved"
    print(f"{verb} {moved} sprite(s) into {target_root}")
    print(f"  Base images: {moved - recolor_count}")
    print(f"  Recolor variants: {recolor_count}")
    print(f"  Not in source folder(s): {skipped}")
    print(f"  Already catalogued (skipped): {already}")
    print(f"Master index: {catalog_path_out} ({indexed} entries)")

    duplicates = {key: stems for key, stems in built_names.items() if len(stems) > 1}
    if duplicates:
        print(f"  Note: {len(duplicates)} name(s) were promoted more than once this run "
              f"(files suffixed _1, _2, …; use `id` in catalog.toml to tell them apart).")

    report_path = meta_path.parent / "build_blocked.json"
    if blocked:
        reasons = collections.Counter(i for b in blocked for i in b["issues"])
        print(f"BLOCKED {len(blocked)} sprite(s) that are not catalogue-ready "
              f"(left in place):")
        for reason, count in reasons.most_common():
            print(f"  {reason:<20} {count}")
        atomic_save({"domain": domain.key, "count": len(blocked),
                     "reasons": dict(reasons), "entries": blocked}, report_path)
        print(f"Details: {report_path}")
        print("Fix them via label/infer or the approve/reject page, or pass "
              "--allow-unsafe to promote anyway.")
    elif report_path.exists():
        report_path.unlink()

    # A zero-match run almost always means the source folder is wrong (e.g. the
    # sprites were already moved out by `move-approved`). Point --sprites at the
    # folder the approved sprites were moved to (default: out/items/approved).
    if moved == 0 and not blocked and skipped > 0:
        print(
            "WARNING: 0 sprites matched in the source folder(s). If you ran "
            "`move-approved` first, pass --sprites <approved folder> (the approved "
            "set is moved out of the source folder)."
        )


def register(subparsers):
    p = subparsers.add_parser(
        "build",
        help="build the catalogued library (out/@items by default) from metadata + sprites",
    )
    add_domain_argument(p)
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--sprites", action="append", default=None, metavar="DIR",
                   help="folder of (named) source images to catalog; repeatable "
                        "(default: domain build sources)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.add_argument("--target", default=None,
                   help="output catalogue root (default: out/@items or out/@spells)")
    p.add_argument("--allow-unsafe", action="store_true",
                   help="promote entries even if they fail the readiness gate "
                        "(placeholder name, unknown category, no tags, no LLM pass)")
    p.add_argument("--min-confidence", type=float, default=None, metavar="X",
                   help="also block un-approved entries with model confidence below X")
    p.add_argument("--copy", action="store_true",
                   help="copy source images instead of moving them (non-destructive)")
    p.set_defaults(func=build_catalog)
