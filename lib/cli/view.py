"""viewer — offline HTML browser for the built catalogue.

Reads ``catalog.toml`` (the master index written by ``build``) and renders a
self-contained, searchable/filterable page showing every catalogue sprite with
its metadata. Recolor variants are grouped under their base image. No server
needed: the page is opened directly via ``file://`` and image paths are
computed relative to the page location.
"""
import argparse
import os
import tomllib
from pathlib import Path
from urllib.parse import quote

from lib.cli._html import render_template, dump_json
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import project_path


def _entry_src(catalog_root, output_path, entry):
    """Relative URL from the generated page to the sprite's PNG file."""
    try:
        rel = Path(os.path.relpath(catalog_root.resolve(),
                                   output_path.parent.resolve()))
    except ValueError:
        rel = Path(".")
    rel_str = rel.as_posix().rstrip("/")
    file_path = (entry.get("file") or "").strip("/")
    if not file_path:
        return "#"
    joined = "/".join(quote(segment) for segment in file_path.split("/"))
    if rel_str in ("", "."):
        return joined
    return f"{rel_str}/{joined}"


def cmd_viewer(args):
    domain = resolve_domain(args)
    catalog_root = project_path(getattr(args, "target", None) or domain.catalog)
    output = project_path(getattr(args, "output", None) or domain.catalog_view_html)

    catalog_toml = catalog_root / "catalog.toml"
    if not catalog_toml.exists():
        raise SystemExit(
            f"Catalogue not built yet: {catalog_toml} missing — run "
            f"`sprite.py build --domain {domain.key}` first"
        )
    with open(catalog_toml, "rb") as handle:
        data = tomllib.load(handle)
    entries = data.get("entries", [])
    for entry in entries:
        entry["src"] = _entry_src(catalog_root, output, entry)

    html = render_template(
        "sprite_catalog_view.html.j2",
        domain_title=domain.title,
        entries=dump_json(entries),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")

    category_count = len(
        {(entry.get("category") or "other").split("/")[0] for entry in entries}
    )
    recolor_count = sum(1 for entry in entries if entry.get("is_recolor"))
    print(f"Built catalogue viewer for {len(entries)} sprite(s) "
          f"({category_count} categories, {recolor_count} recolors): {output}")
    print("Open the HTML locally and browse the catalogue.")


def register(subparsers):
    p = subparsers.add_parser(
        "viewer",
        help="generate an offline HTML browser for the built catalogue",
    )
    add_domain_argument(p)
    p.add_argument("--target", default=None,
                   help="catalogue root to view (default: out/@items or out/@spells)")
    p.add_argument("--output", default=None,
                   help="HTML output path (default: domain catalogue viewer page)")
    p.set_defaults(func=cmd_viewer)
