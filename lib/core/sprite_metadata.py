"""Unified sprite metadata store.

Replaces dcss_catalog.json + sprite_labels.json + recolor_map.json with a
single hash-keyed JSON file per domain (e.g. out/items/metadata.json,
out/spells/metadata.json).

Structure:
  {
    "version": 1,
    "recolor_groups": {
      "<base_pixel_hash>": {
        "variants": {
          "<variant_hash>": {
            "region_colors_base": [[r,g,b], ...],   # 4x4 grid, row-major
            "region_colors_variant": [[r,g,b], ...], # 4x4 grid, row-major
            "color_delta_map": [[dr,dg,db], ...],    # 4x4 grid, row-major
            "changed_pixel_fraction": 0.0            # 0-1, how many opaque pixels changed
          }
        }
      }
    },
    "entries": {
      "<pixel_hash>": { ... sprite entry ... }
    }
  }

All cross-references use pixel_hash so renames/moves never break links.
"""
import contextlib
import fcntl
import json
from pathlib import Path

from lib.util.paths import atomic_save, project_path


def load(path="out/items/metadata.json"):
    path = project_path(path)
    if not path.exists():
        return {"version": 1, "recolor_groups": {}, "entries": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if "recolor_groups" not in data:
        data["recolor_groups"] = {}
    # Backwards-compat: old list-based variant_hashes -> dict-based
    for base_hash, variants in data.get("recolor_groups", {}).items():
        if isinstance(variants, list):
            data["recolor_groups"][base_hash] = {
                "variants": {vh: {} for vh in variants}
            }
    if "entries" not in data:
        # Backwards-compat: old {"labels": {...}} format
        labels = data.get("labels", {})
        entries = {}
        for k, v in labels.items():
            entries[k] = v if isinstance(v, dict) else {}
        data["entries"] = entries
    return data


@contextlib.contextmanager
def metadata_lock(path, exclusive=True):
    """Advisory lock around the unified metadata file.

    Hold this for a whole read-modify-write cycle (one `label`/`infer` run) so
    concurrent processes can't clobber each other's updates. While it is held,
    any `atomic_save` to the *same* path must be called with ``lock=False``
    (the outer lock already covers the rename). Read-only tools ignore this and
    stay safe because `atomic_save` swaps files with an atomic `os.replace`.

    Unix-only (uses fcntl.flock).
    """
    path = project_path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield


def save(data, path="out/items/metadata.json"):
    atomic_save(data, path)


def get_entry(data, phash):
    return data["entries"].get(phash)


def set_entry(data, phash, entry):
    data["entries"][phash] = entry


def entry_for(data, phash, default=None):
    return data["entries"].get(phash, default or {})


def add_recolor_group(data, base_hash, variant_infos):
    """variant_infos: list of dicts with 'hash' plus optional color info."""
    variants = {}
    for vi in variant_infos:
        vh = vi["hash"]
        variants[vh] = {k: v for k, v in vi.items() if k != "hash"}
    data["recolor_groups"][base_hash] = {"variants": variants}


def get_recolor_groups(data):
    return data.get("recolor_groups", {})


def variant_to_base(data):
    """Return {variant_hash: base_hash} lookup."""
    mapping = {}
    for base_hash, group in data.get("recolor_groups", {}).items():
        variants = group.get("variants", group) if isinstance(group, dict) else group
        for vh in variants:
            mapping[vh] = base_hash
    return mapping


def base_to_variants(data):
    """Return {base_hash: {variant_hash: info}} lookup."""
    result = {}
    for base_hash, group in data.get("recolor_groups", {}).items():
        if isinstance(group, dict):
            result[base_hash] = group.get("variants", {})
        else:
            result[base_hash] = {vh: {} for vh in group}
    return result


def all_hashes(data):
    return set(data["entries"].keys())
