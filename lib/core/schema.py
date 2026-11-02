"""Canonical sprite metadata entry schema.

Every command that writes into a domain's metadata JSON (e.g.
out/items/metadata.json, out/spells/metadata.json) uses the same shape.
`new_entry` builds the full default dict so callers only override what they
know, instead of copying a 20-field literal everywhere.
"""
from lib.util.paths import sanitize_name


def new_entry(phash, **overrides):
    """Return a fully-populated metadata entry keyed by *phash*.

    Any provided keyword overrides the defaults (e.g. name="long_sword").
    """
    entry = {
        "name": None,
        "category": None,
        "subcategory": "",
        "source": "sprite",
        "path": None,
        "width": 0,
        "height": 0,
        "tags": [],
        "confidence": None,
        "notes": "",
        "is_recolor": False,
        "recolor_base": None,
        "recolor_variants": [],
        "vllm_name": None,
        "vllm_category": None,
        "vllm_tags": [],
        "vllm_confidence": None,
        "vllm_notes": "",
        "manual": False,
        "pixel_hash": phash,
    }
    entry.update(overrides)
    if entry.get("name") is not None:
        entry["name"] = sanitize_name(entry["name"])
    return entry
