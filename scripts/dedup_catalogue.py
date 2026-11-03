"""Remove catalogue assets that share a pixel_hash (left by repeated `build --copy`).

Keeps the un-suffixed / lowest-sorted file per hash. Dry-run unless --apply.
Run `python sprite.py build` afterwards (or re-run this with --apply, which
regenerates catalog.toml itself).
"""
import argparse
import collections
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.cli.catalog import write_catalog_index  # noqa: E402
from lib.domains import resolve_domain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="spells")
    ap.add_argument("--target", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    domain = resolve_domain(args)
    root = Path(args.target or domain.catalog)

    groups = collections.defaultdict(list)
    for t in sorted(root.rglob("*.toml")):
        if t.name == "catalog.toml" or not t.with_suffix(".png").exists():
            continue
        with open(t, "rb") as fh:
            h = tomllib.load(fh).get("pixel_hash")
        if h:
            groups[h].append(t)

    removed = 0
    for h, tomls in groups.items():
        if len(tomls) < 2:
            continue
        tomls.sort(key=lambda p: (len(p.stem), p.stem))
        keep, *drop = tomls
        print(f"{h}: keep {keep.relative_to(root)}")
        for d in drop:
            print(f"   drop {d.relative_to(root)} (+ .png)")
            removed += 1
            if args.apply:
                d.with_suffix(".png").unlink()
                d.unlink()
    print(f"{removed} duplicate asset(s) {'removed' if args.apply else 'found (dry-run)'}")
    if args.apply:
        print(f"catalog.toml: {write_catalog_index(root, domain)} entries")


if __name__ == "__main__":
    main()
