"""Shared path / naming / IO helpers.

Centralises the small helpers that were copy-pasted across the old flat
scripts (project_path, sanitize_name, PNG enumeration, atomic JSON writes)
so every command module imports them from one place.
"""
import fcntl
import json
import os
import re
from pathlib import Path

# lib/util/paths.py -> lib/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "template"

# Files that are side-products of other commands, never real sprites.
PNG_SKIP = ("contact_sheet.png",)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def project_path(value):
    """Resolve a possibly-relative path against the current working dir.

    Absolute paths pass through unchanged; relative paths are joined to cwd so
    the CLI behaves the same whether you run it from the project root or not.
    """
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def sanitize_name(name):
    """Lower-case, ascii-fold-ish slug used for sprite and file names."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def iter_images(directory, recursive=True, skip=PNG_SKIP,
                extensions=IMAGE_EXTENSIONS):
    """Return sorted image Path objects under *directory*.

    Reference folders (dcss/dlss) nest subfolders, so recursion is on by
    default; sprite source folders are flat and produce the same result.
    Domains can narrow ``extensions`` (items are PNG-only, spell packs may
    include JPGs) and the catalogue always writes PNG output.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    allowed = tuple(ext.lower() for ext in extensions)
    glob = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        p for p in glob
        if p.is_file() and p.suffix.lower() in allowed and p.name not in skip
    )


def iter_pngs(directory, recursive=True, skip=PNG_SKIP):
    """PNG-only convenience wrapper around :func:`iter_images`."""
    return iter_images(directory, recursive=recursive, skip=skip,
                       extensions=(".png",))



def atomic_save(data, path, lock=True):
    """Write JSON atomically (temp file + os.replace) to avoid corruption.

    When ``lock`` is true (default) an exclusive advisory lock is held on a
    sibling ``<path>.lock`` file for the duration of the rename, so concurrent
    writers (e.g. `label` and `infer` running at the same time) cannot clobber
    each other's writes. Pass ``lock=False`` when the caller already holds a
    `metadata_lock` for the same path (avoids re-locking the same file).
    """
    path = project_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if lock:
        lock_path = path.with_suffix(path.suffix + ".lock")
        with open(lock_path, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            os.replace(tmp_path, path)
    else:
        os.replace(tmp_path, path)
