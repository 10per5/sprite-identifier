"""Domain registry.

Import from here rather than reaching into ``lib.domains.items`` /
``lib.domains.spells`` so the CLI has one stable entry point.
"""
import argparse

from lib.domains.base import Domain, PromptSet
from lib.domains.items import ITEM_DOMAIN
from lib.domains.spells import SPELL_DOMAIN

DOMAINS = {domain.key: domain for domain in (ITEM_DOMAIN, SPELL_DOMAIN)}
DEFAULT_DOMAIN = ITEM_DOMAIN.key


def get_domain(name=None):
    """Return a domain profile by key (``None`` -> the default item domain)."""
    key = name or DEFAULT_DOMAIN
    try:
        return DOMAINS[key]
    except KeyError as exc:
        choices = ", ".join(sorted(DOMAINS))
        raise SystemExit(f"Unknown domain {key!r} (choose from: {choices})") from exc


def add_domain_argument(parser, default=DEFAULT_DOMAIN):
    """Attach ``--domain`` to a subcommand.

    ``default=argparse.SUPPRESS`` is deliberate: the top-level parser also
    accepts ``--domain`` (``sprite.py --domain spells build``). If the
    subparser set its own default, it would overwrite the global value.
    """
    parser.add_argument(
        "--domain",
        choices=sorted(DOMAINS),
        default=argparse.SUPPRESS,
        help=f"asset family to operate on (default: {default}; may also be set "
             f"globally before the command)",
    )


def resolve_domain(args):
    return get_domain(getattr(args, "domain", None))
