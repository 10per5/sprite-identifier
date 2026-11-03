#!/usr/bin/env python3
"""sprite.py — unified command-line interface for the sprite pipeline.

Run with no arguments to launch the guided "0-to-hero" wizard:

    python3 sprite.py

…or call a command directly:

    python3 sprite.py normalize --sprites in/sprites --dry-run
    python3 sprite.py dedup --dir in/sprites --apply --yes
    python3 sprite.py index --dir dcss
    python3 sprite.py auto-name
    python3 sprite.py label --move-tagged-to tagged
    python3 sprite.py build --domain items --target out/@items
    python3 sprite.py index --domain spells --dir in/spellicon-named
    python3 sprite.py infer --domain spells --sprites in/spellicon-named
    python3 sprite.py build --domain spells
    python3 sprite.py guided          # same as no arguments

Commands are grouped under lib/cli; the guided wizard lives in lib/wizard.py.
"""
import argparse
import json
import sys
from pathlib import Path

# Make the project root importable no matter where the script is launched from.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.cli import normalize, split, dedup, catalog, label, infer, index, review, view, revert
from lib.domains import DOMAINS, add_domain_argument
from lib.wizard import run_wizard


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sprite.py",
        description="Sprite pipeline: normalize, dedup, index, label, infer, review, build, revert.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
asset domains:
  --domain items    in/sprites                         -> out/@items   (default)
  --domain spells   in/spellicon + in/spellicon-named  -> out/@spells

domain-aware commands:
  build, revert, index, label, infer, review-label, review-approval, move-approved, guided

generic / item-only commands:
  normalize, split, dedup, auto-name

examples:
  python3 sprite.py                                   # guided item workflow
  python3 sprite.py --domain spells                   # guided spell workflow
  python3 sprite.py --domain spells build             # global --domain form
  python3 sprite.py infer --domain spells --sprites in/spellicon-named
  python3 sprite.py label --domain spells --sprites in/spellicon
  python3 sprite.py build --domain spells
  python3 sprite.py review-approval --domain spells --sprites in/spellicon
  python3 sprite.py move-approved --domain spells
  python3 sprite.py revert --domain spells            # dry-run: flagged catalogue assets
""",
    )
    parser.add_argument(
        "--domain",
        choices=sorted(DOMAINS),
        default=None,
        help="asset family for the command (default: items). May also be "
             "placed after the command.",
    )
    sub = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="COMMAND",
        required=False,
    )

    for mod in (normalize, split, dedup, catalog, label, infer, index, review, view, revert):
        mod.register(sub)

    p = sub.add_parser(
        "guided", help="launch the interactive 0-to-hero wizard"
    )
    add_domain_argument(p)
    p.set_defaults(func=lambda a: run_wizard(getattr(a, "domain", None)))
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # No subcommand → guided wizard for the selected domain.
    if not getattr(args, "command", None):
        run_wizard(getattr(args, "domain", None))
        return

    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
