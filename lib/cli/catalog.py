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
"""
import argparse
import os

import numpy as np
from PIL import Image

from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import load as load_metadata, variant_to_base, base_to_variants
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import project_path, sanitize_name, iter_images
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


def _promote_image(src, dest_png):
    """Move a PNG, or decode+save an input JPG/WebP as PNG."""
    if src.suffix.lower() == ".png":
        src.replace(dest_png)
        return
    with Image.open(src) as image:
        image.convert("RGBA").save(dest_png, format="PNG")
    src.unlink()


def build_catalog(args):
    domain = resolve_domain(args)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    target_root = project_path(getattr(args, "target", None) or domain.catalog)

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

    moved = skipped = 0
    recolor_count = 0
    catalog_entries = []

    # --- Phase 1: Process base (non-recolor) images ---
    for phash, entry in entries.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if entry.get("is_recolor"):
            continue
        if phash in v2b:
            continue  # recolor variant, handled in Phase 2

        name = sanitize_name(entry.get("name") or "unnamed")
        category = entry.get("category") or "other/unclassified"
        reference = entry.get("reference")
        default_source = (domain.reference_sources[0]
                          if reference and domain.reference_sources
                          else domain.default_source)
        source = entry.get("source") or default_source
        tags = entry.get("tags") or []
        subcategory = entry.get("subcategory") or ""

        src = sources_by_hash.get(phash)
        if src is None or not src.exists():
            skipped += 1
            continue

        safe_cat = category.replace("/", os.sep).replace("\\", os.sep)
        dest_dir = target_root / safe_cat
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_png = dest_dir / f"{name}.png"
        if dest_png.exists():
            stem = name
            i = 1
            while dest_png.exists():
                dest_png = dest_dir / f"{stem}_{i}.png"
                i += 1

        _promote_image(src, dest_png)
        moved += 1

        if not tags:
            parts = category.split("/")
            tags = parts

        phash = pixel_hash(dest_png)

        recolor_variants = []
        for vh in variants_by_base.get(phash, {}):
            for v_ph, v_entry in entries.items():
                if v_ph == vh and isinstance(v_entry, dict):
                    recolor_variants.append(sanitize_name(v_entry.get("name") or "unnamed"))
                    break

        sprite_toml = {
            "name": name,
            "category": category,
            "subcategory": subcategory,
            "reference": reference,
            "source": source,
            "confidence": entry.get("confidence"),
            "pixel_hash": phash,
            "is_recolor": False,
            "base_image": None,
            "recolor_of": None,
            "recolor_variants": recolor_variants,
            "tags": tags,
        }
        toml_path = dest_dir / f"{dest_png.stem}.toml"
        toml_path.write_text(toml_dumps(sprite_toml), encoding="utf-8")

        catalog_entries.append({
            "name": name,
            "category": category,
            "subcategory": subcategory,
            "reference": reference,
            "source": source,
            "confidence": entry.get("confidence"),
            "pixel_hash": phash,
            "is_recolor": False,
            "base_image": None,
            "recolor_of": None,
            "recolor_variants": recolor_variants,
            "tags": tags,
            "file": f"{category}/{dest_png.name}",
        })

    # --- Phase 2: Process recolor variants ---
    for phash, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if phash not in v2b:
            continue

        name = sanitize_name(entry.get("name") or "unnamed")
        base_hash = v2b[phash]

        base_name = None
        for b_ph, b_entry in entries.items():
            if b_ph == base_hash and isinstance(b_entry, dict):
                base_name = sanitize_name(b_entry.get("name") or "unnamed")
                break
        if not base_name:
            base_name = f"unknown_{base_hash[:8]}"

        category = entry.get("category") or "other/unclassified"
        reference = entry.get("reference")
        default_source = (domain.reference_sources[0]
                          if reference and domain.reference_sources
                          else domain.default_source)
        source = entry.get("source") or default_source
        tags = entry.get("tags") or []
        subcategory = entry.get("subcategory") or ""

        src = sources_by_hash.get(phash)
        if src is None or not src.exists():
            skipped += 1
            continue

        safe_cat = category.replace("/", os.sep).replace("\\", os.sep)
        dest_dir = target_root / safe_cat / "recolor"
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_png = dest_dir / f"{name}.png"
        if dest_png.exists():
            stem = name
            i = 1
            while dest_png.exists():
                dest_png = dest_dir / f"{stem}_{i}.png"
                i += 1

        _promote_image(src, dest_png)
        moved += 1
        recolor_count += 1

        phash = pixel_hash(dest_png)

        if not tags:
            parts = category.split("/")
            tags = parts

        sprite_toml = {
            "name": name,
            "category": category,
            "subcategory": subcategory,
            "reference": reference,
            "source": source,
            "confidence": entry.get("confidence"),
            "pixel_hash": phash,
            "is_recolor": True,
            "base_image": base_name,
            "recolor_of": base_name,
            "recolor_variants": [],
            "tags": tags,
        }
        toml_path = dest_dir / f"{dest_png.stem}.toml"
        toml_path.write_text(toml_dumps(sprite_toml), encoding="utf-8")

        catalog_entries.append({
            "name": name,
            "category": category,
            "subcategory": subcategory,
            "reference": reference,
            "source": source,
            "confidence": entry.get("confidence"),
            "pixel_hash": phash,
            "is_recolor": True,
            "base_image": base_name,
            "recolor_of": base_name,
            "recolor_variants": [],
            "tags": tags,
            "file": f"{category}/recolor/{dest_png.name}",
        })

    # Write master catalog index.
    catalog = {
        "version": 1,
        "domain": domain.key,
        "count": len(catalog_entries),
        "entries": catalog_entries,
    }
    catalog_path_out = target_root / "catalog.toml"
    catalog_path_out.write_text(toml_dumps(catalog), encoding="utf-8")

    print(f"Cataloged {len(catalog_entries)} sprite(s) into {target_root}")
    print(f"  Base images: {moved - recolor_count}")
    print(f"  Recolor variants: {recolor_count}")
    print(f"  Skipped: {skipped}")
    print(f"Master index: {catalog_path_out}")

    # A zero-match run almost always means the source folder is wrong (e.g. the
    # sprites were already moved out by `move-approved`). Point --sprites at the
    # folder the approved sprites were moved to (default: out/items/approved).
    if moved == 0 and skipped > 0:
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
    p.set_defaults(func=build_catalog)
