"""revert — move catalogue assets that fail the readiness gate back to input.

`build` used to promote every metadata entry it could find, so the catalogue
holds icons with placeholder names, unknown categories or no tags. This undoes
those promotions without losing anything:

  * catalogue PNGs go back to the source path recorded in metadata
    (``entry["path"]``, e.g. ``spellicon/<pack>/<file>.png`` → ``in/spellicon/…``)
  * their TOMLs are quarantined under ``out/<domain>/reverted/<stamp>/``
  * a manifest ``out/<domain>/revert-<stamp>.json`` records every move, and
    ``--from-manifest`` replays it backwards into the catalogue
  * ``catalog.toml`` is regenerated from what remains on disk

Dry-run by default; ``--apply`` executes. Metadata is never modified, so the
reverted icons simply re-enter label/infer → review-approval → build.
"""
import collections
import json
import time
import tomllib
from pathlib import Path

from lib.cli.catalog import write_catalog_index
from lib.core.pixel_hash import pixel_hash
from lib.core.quality import entry_issues
from lib.core.sprite_metadata import load as load_metadata
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import atomic_save, project_path


def _rel(path):
    """Project-relative string for manifests (absolute if outside cwd)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def _restore_path(entry, domain, png):
    """Where a catalogue PNG came from, based on the metadata ``path``.

    ``path`` is ``<source>/<relative>`` as written by `index`; it maps back
    under the parent of ``domain.source_dir``. Unknown origins fall back to the
    domain source folder. The catalogue copy is always PNG, so a JPG source is
    restored as ``.png``.
    """
    source_dir = project_path(domain.source_dir)
    recorded = (entry or {}).get("path") or ""
    head, _, rest = recorded.partition("/")
    if rest and head == source_dir.name:
        dest = source_dir / rest
    elif rest and (source_dir.parent / head).is_dir():
        dest = source_dir.parent / recorded
    else:
        dest = source_dir / png.name
    return dest.with_suffix(".png")


def _read_toml(path):
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _prune_empty_dirs(root):
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()


def _plan(domain, target_root, entries, *, revert_all, min_confidence):
    moves, conflicts = [], []
    planned = set()
    for png in sorted(target_root.rglob("*.png")):
        toml_path = png.with_suffix(".toml")
        record = _read_toml(toml_path) if toml_path.exists() else {}
        phash = record.get("pixel_hash") or pixel_hash(png)
        entry = entries.get(phash)
        if not isinstance(entry, dict):
            issues = ["no_metadata"]
        else:
            issues = entry_issues(entry, domain, min_confidence=min_confidence,
                                  variant=bool(record.get("is_recolor")))
        if not issues and not revert_all:
            continue
        dest = _restore_path(entry, domain, png)
        move = {
            "pixel_hash": phash,
            "name": (entry or {}).get("name"),
            "category": (entry or {}).get("category"),
            "issues": issues or ["all"],
            "catalog_png": _rel(png),
            "catalog_toml": _rel(toml_path) if toml_path.exists() else None,
            "restored_to": _rel(dest),
        }
        if dest.exists() or dest in planned:
            move["conflict"] = "restore target already exists"
            conflicts.append(move)
            continue
        planned.add(dest)
        moves.append(move)
    return moves, conflicts


def _print_plan(moves, conflicts, apply, total):
    if not moves and not conflicts:
        print(f"Nothing to revert: all {total} catalogue asset(s) pass the readiness "
              f"gate. Icons reverted earlier are back in the source folder; see "
              f"out/<domain>/revert-*.json.")
        return
    head = "Reverting" if apply else "Would revert (dry-run)"
    print(f"{head} {len(moves)} catalogue asset(s).")
    reasons = collections.Counter(i for m in moves for i in m["issues"])
    for reason, count in reasons.most_common():
        print(f"  {reason:<20} {count}")
    folders = collections.Counter(str(Path(m["catalog_png"]).parent) for m in moves)
    print("  by catalogue folder: " + ", ".join(
        f"{Path(k).name}={v}" for k, v in folders.most_common(12)))
    for move in moves[:5]:
        print(f"    {move['catalog_png']} -> {move['restored_to']}  ({', '.join(move['issues'])})")
    if len(moves) > 5:
        print(f"    … and {len(moves) - 5} more")
    if conflicts:
        print(f"  {len(conflicts)} skipped: restore target already exists "
              f"(first: {conflicts[0]['restored_to']})")


def _apply(moves, target_root, quarantine_root):
    for move in moves:
        png = project_path(move["catalog_png"])
        dest = project_path(move["restored_to"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        png.replace(dest)
        if move["catalog_toml"]:
            toml_path = project_path(move["catalog_toml"])
            quarantined = quarantine_root / toml_path.relative_to(target_root)
            quarantined.parent.mkdir(parents=True, exist_ok=True)
            toml_path.replace(quarantined)
            move["quarantined_toml"] = _rel(quarantined)
    return moves


def _replay(manifest_path, domain, target_root, apply):
    data = json.loads(project_path(manifest_path).read_text(encoding="utf-8"))
    if data.get("domain") != domain.key:
        raise SystemExit(f"Manifest is for domain {data.get('domain')!r}, not {domain.key!r}")
    restorable, missing = [], []
    for move in data.get("moves", []):
        src = project_path(move["restored_to"])
        dest = project_path(move["catalog_png"])
        (restorable if src.exists() and not dest.exists() else missing).append(move)
    verb = "Restoring" if apply else "Would restore (dry-run)"
    print(f"{verb} {len(restorable)} asset(s) into {target_root} from {manifest_path}")
    if missing:
        print(f"  {len(missing)} skipped: source gone or catalogue path taken "
              f"(first: {missing[0]['restored_to']})")
    if not apply:
        print("Re-run with --apply to execute.")
        return
    for move in restorable:
        dest = project_path(move["catalog_png"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        project_path(move["restored_to"]).replace(dest)
        quarantined = move.get("quarantined_toml")
        if quarantined and project_path(quarantined).exists():
            project_path(quarantined).replace(project_path(move["catalog_toml"]))
    count = write_catalog_index(target_root, domain)
    print(f"Regenerated {target_root / 'catalog.toml'} ({count} entries)")


def cmd_revert(args):
    domain = resolve_domain(args)
    target_root = project_path(getattr(args, "target", None) or domain.catalog)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    if not target_root.is_dir():
        raise SystemExit(f"Catalogue not found: {target_root}")

    if args.from_manifest:
        _replay(args.from_manifest, domain, target_root, args.apply)
        return

    entries = load_metadata(meta_path)["entries"]
    moves, conflicts = _plan(domain, target_root, entries,
                             revert_all=args.all,
                             min_confidence=args.min_confidence)
    total = sum(1 for _ in target_root.rglob("*.png"))
    _print_plan(moves, conflicts, args.apply, total)
    if not args.apply:
        if moves:
            print("Nothing moved. Re-run with --apply to execute.")
        return
    if not moves:
        count = write_catalog_index(target_root, domain)
        print(f"Regenerated {target_root / 'catalog.toml'} ({count} entries)")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = meta_path.parent
    quarantine_root = out_dir / "reverted" / stamp
    manifest_path = out_dir / f"revert-{stamp}.json"
    # Write the manifest before touching files so an interrupted run can
    # still be replayed (entries without quarantined_toml just skip the TOML).
    manifest = {"domain": domain.key, "created": stamp, "target": _rel(target_root),
                "moves": moves, "conflicts": conflicts}
    atomic_save(manifest, manifest_path)
    manifest["moves"] = _apply(moves, target_root, quarantine_root)
    atomic_save(manifest, manifest_path)
    _prune_empty_dirs(target_root)
    count = write_catalog_index(target_root, domain)
    print(f"Moved {len(moves)} asset(s) back; TOMLs quarantined in {quarantine_root}")
    print(f"Manifest: {manifest_path}")
    print(f"Regenerated {target_root / 'catalog.toml'} ({count} entries)")
    print(f"Undo with: sprite.py revert --domain {domain.key} "
          f"--from-manifest {_rel(manifest_path)} --apply")


def register(subparsers):
    p = subparsers.add_parser(
        "revert",
        help="move catalogue assets that fail the readiness gate back to the input "
             "folder (dry-run unless --apply)",
    )
    add_domain_argument(p)
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.add_argument("--target", default=None,
                   help="catalogue root (default: out/@items or out/@spells)")
    p.add_argument("--all", action="store_true",
                   help="revert every catalogue asset, not just the flagged ones")
    p.add_argument("--min-confidence", type=float, default=None, metavar="X",
                   help="also revert un-approved entries with model confidence below X")
    p.add_argument("--apply", action="store_true",
                   help="actually move files (default: print the plan only)")
    p.add_argument("--from-manifest", default=None, metavar="PATH",
                   help="replay a previous revert manifest back into the catalogue")
    p.set_defaults(func=cmd_revert)
