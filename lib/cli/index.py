"""index / auto-name — populate the metadata store.

`index`     scans a reference folder (e.g. dcss/ or dlss/) into the unified
            metadata store. Each PNG gets pixel_hash as key, name from the
            filename stem, category from the folder path, and a source label.
            These become hints for auto-name and VLLM (name+category prefilled)
            and targets for pixel_hash matching.

`auto-name` fills in names/categories for sprites that already have a pixel_hash
            entry but no name, by matching against the indexed catalogue and any
            exported label file, then infers a category from the name when still
            unclassified.
"""
import argparse
import collections
import json
from PIL import Image

from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import (
    load as load_metadata,
    save as save_metadata,
    entry_for,
    set_entry,
)
from lib.core.schema import new_entry
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import project_path, sanitize_name, iter_images, iter_pngs

# Filename -> category mappings used to infer a category when a sprite is
# named but has no indexed reference to copy one from.
_NAME_TO_CATEGORY = {
    "swords": ("sword", "blade", "katana", "falchion", "sabre", "scimitar", "cutlass", "rapier", "claymore", "broadsword", "longsword", "shortsword", "greatsword", "estoc", "zweihander"),
    "daggers": ("dagger", "knife", "stiletto", "poniard", "vampire_tooth"),
    "clubs": ("club", "mace", "ankus", "morningstar", "morning_star", "eveningstar", "flanged_mace"),
    "lances": ("polearm", "glaive", "halberd", "bardiche", "trident", "trishula", "pike", "lance", "spear"),
    "ranged": ("bow", "crossbow", "xbow", "arrow", "bolt", "javelin", "shuriken", "throw", "sling", "blowgun", "tomahawk", "rock", "stone", "dart", "needle", "boomerang"),
    "axes": ("axe",),
    "hammers": ("hammer",),
    "flails": ("flail", "scourge"),
    "whips": ("whip",),
    "scythes": ("scythe",),
    "staves": ("staff", "stave", "lajatang", "sceptre", "wucad_mu"),
    "head": ("helmet", "headgear", "helm", "hat", "crown", "hood", "circlet", "mask"),
    "boots": ("boot", "shoe", "sandal", "sabaton"),
    "gloves": ("glove", "gauntlet", "mitt"),
    "legs": ("legging", "greave", "trousers", "kilt", "skirt"),
    "chest": ("armour", "armor", "torso", "robe", "chainmail", "plate", "scale", "leather", "mail"),
    "shields": ("shield",),
    "fruit": ("fruit", "berry", "apple", "orange", "banana", "pear", "plum"),
    "fish": ("fish",),
    "meat": ("meat", "ham", "bacon", "jamon"),
    "vegetable": ("vegetable", "lettuce", "potato", "carrot", "onion"),
    "potions": ("potion",),
    "scrolls": ("scroll",),
    "books": ("book",),
    "wands": ("wand",),
    "gems": ("gem", "ruby", "amethist", "mineral", "ingot"),
    "jewelry": ("ring", "amulet", "jewelry"),
    "currency": ("coin", "currency", "gold", "silver", "bronze"),
    "keys": ("key",),
    "runes": ("rune",),
    "tools": ("tool", "ladder", "shovel", "tableware", "storage"),
    "accessories": ("accessory", "cloak", "ring"),
    "floor": ("floor",),
    "walls": ("wall",),
    "effects": ("effect", "blood"),
}

_SUB_TO_TOP = {
    "swords": "weapons/swords", "daggers": "weapons/daggers", "clubs": "weapons/clubs",
    "lances": "weapons/lances", "ranged": "weapons/ranged", "axes": "weapons/axes",
    "hammers": "weapons/hammers", "flails": "weapons/flails", "whips": "weapons/whips",
    "scythes": "weapons/scythes", "staves": "weapons/staves",
    "head": "armor/head", "boots": "armor/boots", "gloves": "armor/gloves",
    "legs": "armor/legs", "chest": "armor/chest", "shields": "armor/shields",
    "fruit": "food/fruit", "fish": "food/fish", "meat": "food/meat", "vegetable": "food/vegetable",
    "potions": "consumables/potions", "scrolls": "consumables/scrolls", "books": "consumables/books",
    "wands": "magic/wands", "gems": "treasure/gems", "jewelry": "treasure/jewelry",
    "currency": "treasure/currency", "keys": "treasure/keys", "runes": "treasure/runes",
    "tools": "tools", "accessories": "accessories", "floor": "environment/floor",
    "walls": "environment/walls", "effects": "effects",
}


def infer_category_from_name(name):
    n = name.lower()
    for sub, words in _NAME_TO_CATEGORY.items():
        if any(w in n for w in words):
            return _SUB_TO_TOP[sub]
    return "other/unclassified"


def read_labels(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError("labels must be a JSON object")
    return labels


def cmd_index(args):
    """Scan a reference/source folder into unified metadata."""
    domain = resolve_domain(args)
    dir_value = getattr(args, "dir", None) or domain.index_dir
    src_dir = project_path(dir_value)
    if not src_dir.is_dir():
        raise SystemExit(f"Directory not found: {src_dir}")
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    meta = load_metadata(meta_path)

    recursive = getattr(args, "recursive", None)
    if recursive is None:
        recursive = domain.recursive

    source = args.source or src_dir.name
    added = updated = 0
    for path in iter_images(src_dir, recursive=recursive,
                            extensions=domain.extensions):
        relative = path.relative_to(src_dir).as_posix()
        with Image.open(path) as image:
            width, height = image.size
        phash = pixel_hash(path)
        category, subcategory = domain.classify(relative)
        entry = new_entry(
            phash,
            name=path.stem,
            category=category,
            subcategory=subcategory,
            source=source,
            path=f"{source}/{relative}",
            width=width,
            height=height,
        )
        existing = entry_for(meta, phash)
        if existing:
            if not existing.get("name"):
                existing["name"] = entry["name"]
            if not existing.get("category"):
                existing["category"] = entry["category"]
            if not existing.get("subcategory"):
                existing["subcategory"] = entry["subcategory"]
            if not existing.get("path"):
                existing["path"] = entry["path"]
            if not existing.get("source"):
                existing["source"] = entry["source"]
            existing["width"] = entry["width"]
            existing["height"] = entry["height"]
            updated += 1
        else:
            set_entry(meta, phash, entry)
            added += 1

    save_metadata(meta, meta_path)
    counts = collections.Counter(e.get("category", "other/unclassified") for e in meta["entries"].values() if isinstance(e, dict))
    print(f"Indexed {added + updated} image(s) from {src_dir} into {meta_path}")
    print(f"  Added: {added}, Updated: {updated}, Total entries: {len(meta['entries'])}")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")


def cmd_auto_name(args):
    """Derive names from catalogue entries for unlabeled sprites via pixel_hash."""
    label_path = project_path(args.labels)
    sprites_dir = project_path(args.sprites)
    meta_path = project_path(args.metadata)

    meta = load_metadata(meta_path)

    # Read exported labels (from review page) if provided.
    exported_labels = {}
    if label_path.exists():
        try:
            exported_labels = read_labels(label_path)
        except (json.JSONDecodeError, OSError):
            exported_labels = {}

    # Build sprite_id -> pixel_hash mapping from sprite files.
    sprite_id_to_hash = {}
    for path in iter_pngs(sprites_dir, recursive=False):
        sprite_id_to_hash[path.stem] = pixel_hash(path)

    named = already = created = cat_inferred = merged = 0

    # Merge exported labels into unified metadata entries.
    for sprite_id, label in exported_labels.items():
        if not isinstance(label, dict):
            continue
        phash = label.get("pixel_hash") or sprite_id_to_hash.get(sprite_id)
        if not phash:
            continue
        entry = entry_for(meta, phash, {})
        if label.get("name"):
            entry["name"] = sanitize_name(label["name"])
            named += 1
        else:
            already += 1
        if label.get("category"):
            entry["category"] = label["category"]
        if label.get("reference"):
            entry["path"] = label["reference"]
        entry["pixel_hash"] = phash
        entry["manual"] = True
        set_entry(meta, phash, entry)
        merged += 1

    # Auto-name: fill names via pixel_hash matching to catalogue entries.
    for phash, entry in meta["entries"].items():
        if entry.get("source") in ("dcss", "dlss"):
            continue  # catalogue references already have names
        if entry.get("name"):
            already += 1
            continue
        # Try hash-based lookup against catalogue entries (dcss/dlss).
        name = None
        ref_path = None
        for c_ph, c_entry in meta["entries"].items():
            if c_ph == phash and c_entry.get("source") in ("dcss", "dlss"):
                name = c_entry.get("name")
                ref_path = c_entry.get("path")
                break
        if name:
            entry["name"] = sanitize_name(name)
            entry["path"] = ref_path or entry.get("path")
            named += 1
        else:
            # Find sprite file by hash to get sprite_id for fallback name.
            sprite_id = next((sid for sid, h in sprite_id_to_hash.items() if h == phash), None)
            entry["name"] = sanitize_name(sprite_id or "unnamed")
            named += 1
        # Infer category from name if still unclassified.
        if not entry.get("category") or entry.get("category") == "other/unclassified":
            entry["category"] = infer_category_from_name(entry["name"])
            cat_inferred += 1

    # Create entries for sprites that have no metadata entry at all.
    all_sprite_hashes = set(sprite_id_to_hash.values())
    for phash in all_sprite_hashes:
        if phash not in meta["entries"]:
            sprite_id = next((sid for sid, h in sprite_id_to_hash.items() if h == phash), None)
            name = sanitize_name(sprite_id or "unnamed")
            meta["entries"][phash] = new_entry(
                phash,
                name=name,
                category=infer_category_from_name(name),
                source="sprite",
                path=f"in/sprites/{sprite_id}.png" if sprite_id else None,
            )
            created += 1

    save_metadata(meta, meta_path)
    print(f"Auto-named {named} sprite(s) ({already} already had names), merged {merged} "
          f"exported label(s), created {created} new entr(y|ies), inferred {cat_inferred} categories")


def register(subparsers):
    p = subparsers.add_parser(
        "index",
        help="scan a reference/source folder into unified metadata for VLLM hints",
    )
    add_domain_argument(p)
    p.add_argument("--dir", default=None,
                   help="folder to scan (default: dcss for items, in/spellicon for spells)")
    p.add_argument("--source", default=None, help="source label for entries (default: folder name)")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for item references)")
    p.add_argument("--metadata", default=None, help="unified metadata JSON path (default: domain metadata)")
    p.set_defaults(func=cmd_index)

    p = subparsers.add_parser(
        "auto-name",
        help="derive names from catalogue entries via pixel_hash",
    )
    p.add_argument("--sprites", default="in/sprites")
    p.add_argument("--labels", default="out/items/labels.json", help="exported labels from review page (optional)")
    p.add_argument("--metadata", default="out/items/metadata.json", help="unified metadata JSON path")
    p.set_defaults(func=cmd_auto_name)
