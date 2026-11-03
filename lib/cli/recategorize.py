"""recategorize — re-judge the category of already catalogued assets.

The first labelling pass put 900 of 1133 spell icons into ``offensive/``,
mostly because the model agreed with a filename hint. This pass asks the model
for the category ONLY, without showing it the current one, and lets you move
the icons that clearly belong elsewhere:

  sprite.py recategorize --domain spells --category offensive --limit 50   # sample
  sprite.py recategorize --domain spells --category offensive              # propose
  sprite.py recategorize --domain spells --from-proposals out/spells/recategorize-<stamp>.json --apply

* Proposing never moves anything. It writes
  ``out/<domain>/recategorize-<stamp>.json`` (saved as it goes) and prints the
  current vs. proposed split per category.
* ``--apply`` moves proposals with ``confidence >= --min-confidence`` (PNG,
  TOML and recolor variants), updates metadata and regenerates
  ``catalog.toml``. Set ``"accept": true/false`` on a proposal in the JSON to
  override the threshold by hand.
* Each apply writes a move manifest (old id → new id) for anything downstream
  that references catalogue paths; ``--undo <manifest> --apply`` reverses it.
  ``pixel_hash`` never changes.
"""
import collections
import json
import os
import time
import tomllib
from pathlib import Path

from lib.cli.catalog import _free_png_path, write_catalog_index
from lib.cli.revert import _prune_empty_dirs, _rel
from lib.core.quality import canonical_category
from lib.core.sprite_metadata import (
    base_to_variants,
    load as load_metadata,
    metadata_lock,
)
from lib.domains import add_domain_argument, resolve_domain
from lib.llm.orcarouter import (
    VISION_MODEL,
    call_vllm_with_fallback,
    check_api_auth,
    image_to_b64,
)
from lib.util.paths import atomic_save, project_path
from lib.util.toml import toml_dumps

# Same estimate as `label` (image + short prompt, tiny reply).
COST_PER_SPRITE = (2600 / 1_000_000) * 0.220 + (60 / 1_000_000) * 0.660


def _read_toml(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _base_records(target_root, categories):
    """``(png, toml_path, record)`` for every non-recolor catalogue asset."""
    for toml_path in sorted(target_root.rglob("*.toml")):
        if toml_path.name == "catalog.toml" or toml_path.parent.name == "recolor":
            continue
        png = toml_path.with_suffix(".png")
        if not png.exists():
            continue
        record = _read_toml(toml_path)
        if categories and record.get("category") not in categories:
            continue
        yield png, toml_path, record


def _distribution(target_root):
    return collections.Counter(r.get("category") for _, _, r in _base_records(target_root, None))


def _pack(entry):
    """``spellicon/<pack>/<file>`` → ``<pack>`` (empty when unknown)."""
    parts = (entry.get("path") or "").split("/")
    return "/".join(parts[1:-1])


def _accepted(proposal, min_confidence):
    if "accept" in proposal:
        return bool(proposal["accept"]) and bool(proposal.get("proposed"))
    return (bool(proposal.get("proposed"))
            and proposal["proposed"] != proposal["current"]
            and (proposal.get("confidence") or 0) >= min_confidence)


def _print_summary(proposals, target_root, min_confidence):
    changes = [p for p in proposals if p.get("proposed") and p["proposed"] != p["current"]]
    accepted = [p for p in proposals if _accepted(p, min_confidence)]
    failed = sum(1 for p in proposals if not p.get("proposed"))
    print(f"\n{len(proposals)} judged · {len(changes)} would change category · "
          f"{len(accepted)} pass confidence >= {min_confidence}"
          + (f" · {failed} without a usable answer" if failed else ""))
    flows = collections.Counter((p["current"], p["proposed"]) for p in changes)
    for (src, dst), n in flows.most_common(12):
        print(f"  {src:>11} → {dst:<11} {n}")

    before = _distribution(target_root)
    after = collections.Counter(before)
    for p in accepted:
        after[p["current"]] -= 1
        after[p["proposed"]] += 1
    print(f"\n  {'category':<12}{'now':>6}{'after':>7}")
    for cat in sorted(set(before) | set(after), key=lambda c: -after[c]):
        print(f"  {cat:<12}{before[cat]:>6}{after[cat]:>7}")
    low = [p for p in changes if p not in accepted]
    if low:
        print(f"\n  {len(low)} change(s) below the threshold stay put; set \"accept\": true "
              f"on a proposal (or lower --min-confidence) to move them.")


def _earlier_judgments(meta_dir, domain, model):
    """``pixel_hash -> proposal`` from earlier proposals files, newest wins.

    The model sees only the image, so its answer for an icon stays valid across
    runs (same model); only answers with a usable category are reused.
    """
    found = {}
    for path in sorted(meta_dir.glob("recategorize-2*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("domain") != domain.key or data.get("model") != model:
            continue
        for proposal in data.get("proposals", []):
            if proposal.get("pixel_hash") and proposal.get("proposed"):
                found[proposal["pixel_hash"]] = proposal
    return found


def _propose(args, domain, target_root, meta_path, proposals_path):
    api_key = os.environ.get("ORCAROUTER_API_KEY")
    if not api_key:
        raise SystemExit("ORCAROUTER_API_KEY environment variable is required")
    ok, detail = check_api_auth(api_key, model=args.model)
    if not ok:
        raise SystemExit(f"API auth FAILED: {detail}")

    entries = load_metadata(meta_path)["entries"]
    categories = set(args.category or [])
    records = list(_base_records(target_root, categories))
    if not records:
        raise SystemExit("No catalogue assets match --category.")

    report = {"domain": domain.key, "created": time.strftime("%Y%m%d-%H%M%S"),
              "model": args.model, "categories": sorted(categories),
              "proposals": []}
    proposals = report["proposals"]

    # Carry earlier answers into this report instead of paying to re-ask.
    earlier = {} if args.rejudge else _earlier_judgments(
        meta_path.parent, domain, args.model)
    todo = []
    for png, toml_path, record in records:
        old = earlier.get(record.get("pixel_hash"))
        if old:
            proposals.append({**old, "file": png.relative_to(target_root).as_posix(),
                              "name": record.get("name"),
                              "current": record.get("category")})
        else:
            todo.append((png, toml_path, record))
    if proposals:
        print(f"Reusing {len(proposals)} earlier judgment(s); "
              f"--rejudge to ask the model again.")
    records = todo[:args.limit] if args.limit else todo
    if not records:
        atomic_save(report, proposals_path)
        print(f"Nothing new to judge.\nProposals: {_rel(proposals_path)}")
        return report
    print(f"Judging {len(records)} asset(s) with {args.model} "
          f"({'vision' if not args.text else 'text-only'}), est. "
          f"~${len(records) * COST_PER_SPRITE:.2f}")

    try:
        _judge(records, args, domain, entries, api_key, report, proposals_path)
    except KeyboardInterrupt:
        atomic_save(report, proposals_path)
        print(f"\nInterrupted; {len(proposals)} proposal(s) saved to "
              f"{_rel(proposals_path)}. Rerun to continue where it stopped.")
        raise SystemExit(130)
    atomic_save(report, proposals_path)
    print(f"\nProposals: {_rel(proposals_path)}")
    return report


def _judge(records, args, domain, entries, api_key, report, proposals_path):
    proposals = report["proposals"]
    for i, (png, _toml_path, record) in enumerate(records, 1):
        entry = entries.get(record.get("pixel_hash")) or {}
        result = call_vllm_with_fallback(
            api_key,
            None if args.text else image_to_b64(png),
            png.stem, "", record.get("name") or png.stem,
            model=args.model, use_image=not args.text,
            timeout=args.timeout, deadline=args.timeout * 3,
            prompts=domain.recategorize_prompts,
            context={"pack": _pack(entry),
                     "subcategory": record.get("subcategory") or "unknown",
                     "tags": ", ".join(record.get("tags") or []) or "none"},
        )
        if result.get("auth_error"):
            atomic_save(report, proposals_path)
            raise SystemExit(f"Auth error: {result.get('notes')}")
        failed = result.get("notes", "").startswith("error:")
        proposed = None if failed else canonical_category(result.get("category"), domain)
        confidence = result.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        proposals.append({
            "pixel_hash": record.get("pixel_hash"),
            "file": png.relative_to(target_root).as_posix(),
            "name": record.get("name"),
            "current": record.get("category"),
            "proposed": proposed,
            "confidence": confidence,
            "notes": result.get("notes", ""),
        })
        mark = ("=" if proposed == record.get("category")
                else "→ " + (proposed or "?"))
        print(f"  [{i}/{len(records)}] {record.get('name')} [{record.get('category')}] "
              f"{mark} ({confidence if confidence is not None else '-'}) "
              f"{result.get('notes', '')[:70]}")
        if i % 10 == 0:
            atomic_save(report, proposals_path)
        time.sleep(args.sleep)


def _move_asset(png, toml_path, dest_dir, new_category):
    """Move a PNG+TOML pair into ``dest_dir``, rewriting its category."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_png = _free_png_path(dest_dir, png.stem)
    record = _read_toml(toml_path)
    record["category"] = new_category
    png.replace(dest_png)
    dest_png.with_suffix(".toml").write_text(toml_dumps(record), encoding="utf-8")
    toml_path.unlink()
    return dest_png


def _apply(report, domain, target_root, meta_path, min_confidence):
    accepted = [p for p in report["proposals"] if _accepted(p, min_confidence)]
    if not accepted:
        print("Nothing to move.")
        return
    moves, stale = [], []
    with metadata_lock(meta_path):
        meta = load_metadata(meta_path)
        variants = base_to_variants(meta)
        recolor_files = {}
        for toml_path in target_root.rglob("recolor/*.toml"):
            record = _read_toml(toml_path)
            recolor_files[record.get("pixel_hash")] = toml_path

        for p in accepted:
            png = target_root / p["file"]
            toml_path = png.with_suffix(".toml")
            if not (png.exists() and toml_path.exists()
                    and _read_toml(toml_path).get("pixel_hash") == p["pixel_hash"]):
                stale.append(p["file"])
                continue
            new_cat = p["proposed"]
            dest = _move_asset(png, toml_path, target_root / new_cat, new_cat)
            moves.append({"pixel_hash": p["pixel_hash"],
                          "old_id": p["file"][:-len(".png")],
                          "new_id": dest.relative_to(target_root).as_posix()[:-len(".png")],
                          "old_category": p["current"], "new_category": new_cat})
            entry = meta["entries"].get(p["pixel_hash"])
            if isinstance(entry, dict):
                entry.setdefault("recategorized_from", entry.get("category"))
                entry["category"] = new_cat
            # Recolor variants follow their base image.
            for vh in variants.get(p["pixel_hash"], {}):
                v_toml = recolor_files.get(vh)
                if not v_toml or not v_toml.exists():
                    continue
                v_png = v_toml.with_suffix(".png")
                v_dest = _move_asset(v_png, v_toml, target_root / new_cat / "recolor", new_cat)
                moves.append({"pixel_hash": vh,
                              "old_id": v_png.relative_to(target_root).as_posix()[:-len(".png")],
                              "new_id": v_dest.relative_to(target_root).as_posix()[:-len(".png")],
                              "old_category": p["current"], "new_category": new_cat})
                v_entry = meta["entries"].get(vh)
                if isinstance(v_entry, dict):
                    v_entry["category"] = new_cat
        atomic_save(meta, meta_path, lock=False)  # outer metadata_lock held

    stamp = time.strftime("%Y%m%d-%H%M%S")
    manifest_path = meta_path.parent / f"recategorize-moves-{stamp}.json"
    atomic_save({"domain": domain.key, "created": stamp, "moves": moves}, manifest_path)
    _prune_empty_dirs(target_root)
    count = write_catalog_index(target_root, domain)
    print(f"Moved {len(moves)} asset(s) (incl. recolor variants); "
          f"catalog.toml regenerated ({count} entries)")
    if stale:
        print(f"  {len(stale)} proposal(s) skipped: file moved or changed since proposing "
              f"(first: {stale[0]})")
    print(f"Move map (old id → new id): {_rel(manifest_path)}")
    print(f"Undo with: sprite.py recategorize --domain {domain.key} "
          f"--undo {_rel(manifest_path)} --apply")


def _undo(manifest_path, domain, target_root, meta_path, apply):
    data = json.loads(project_path(manifest_path).read_text(encoding="utf-8"))
    moves = data.get("moves", [])
    ready = [m for m in moves if (target_root / f"{m['new_id']}.png").exists()]
    verb = "Moving back" if apply else "Would move back (dry-run)"
    print(f"{verb} {len(ready)} of {len(moves)} asset(s) from {manifest_path}")
    if not apply:
        return
    with metadata_lock(meta_path):
        meta = load_metadata(meta_path)
        for m in ready:
            png = target_root / f"{m['new_id']}.png"
            old = Path(m["old_id"])
            dest = _move_asset(png, png.with_suffix(".toml"),
                               target_root / old.parent, m["old_category"])
            if dest.stem != old.name:
                print(f"  {m['old_id']} was taken; restored as {dest.relative_to(target_root)}")
            entry = meta["entries"].get(m["pixel_hash"])
            if isinstance(entry, dict):
                entry["category"] = m["old_category"]
                if entry.get("recategorized_from") == m["old_category"]:
                    entry.pop("recategorized_from")
        atomic_save(meta, meta_path, lock=False)
    _prune_empty_dirs(target_root)
    count = write_catalog_index(target_root, domain)
    print(f"Restored; catalog.toml regenerated ({count} entries)")


def cmd_recategorize(args):
    domain = resolve_domain(args)
    if domain.recategorize_prompts is None:
        raise SystemExit(f"recategorize is not set up for the {domain.key} domain")
    target_root = project_path(getattr(args, "target", None) or domain.catalog)
    meta_path = project_path(getattr(args, "metadata", None) or domain.metadata)
    if not target_root.is_dir():
        raise SystemExit(f"Catalogue not found: {target_root}")

    if args.undo:
        _undo(args.undo, domain, target_root, meta_path, args.apply)
        return

    if args.from_proposals:
        report = json.loads(project_path(args.from_proposals).read_text(encoding="utf-8"))
        if report.get("domain") != domain.key:
            raise SystemExit(f"Proposals are for domain {report.get('domain')!r}")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report = _propose(args, domain, target_root, meta_path,
                          meta_path.parent / f"recategorize-{stamp}.json")

    _print_summary(report["proposals"], target_root, args.min_confidence)
    if args.apply:
        _apply(report, domain, target_root, meta_path, args.min_confidence)
    else:
        print("\nNothing moved. Apply with: --from-proposals <file> --apply")


def register(subparsers):
    p = subparsers.add_parser(
        "recategorize",
        help="re-judge catalogue categories with the LLM and move misfiled assets "
             "(proposals only unless --apply)",
    )
    add_domain_argument(p)
    p.add_argument("--category", action="append", default=None, metavar="CAT",
                   help="only judge assets currently in this category (repeatable; "
                        "default: all)")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="judge at most N assets (quick sample)")
    p.add_argument("--min-confidence", type=float, default=0.7, metavar="X",
                   help="only move proposals at or above this confidence (default 0.7)")
    p.add_argument("--rejudge", action="store_true",
                   help="ask the model again for icons already judged in an earlier "
                        "proposals file (default: reuse those answers)")
    p.add_argument("--from-proposals", default=None, metavar="PATH",
                   help="use an earlier proposals file instead of calling the model")
    p.add_argument("--apply", action="store_true",
                   help="move the accepted proposals (default: report only)")
    p.add_argument("--undo", default=None, metavar="MANIFEST",
                   help="move assets back using a recategorize-moves-*.json manifest")
    p.add_argument("--model", default=VISION_MODEL, help=f"model (default: {VISION_MODEL})")
    p.add_argument("--text", action="store_true",
                   help="text-only: judge from name/tags without sending the image (cheaper)")
    p.add_argument("--timeout", type=int, default=30, help="per-request timeout in seconds")
    p.add_argument("--sleep", type=float, default=0.5, help="pause between requests")
    p.add_argument("--target", default=None, help="catalogue root (default: domain catalogue)")
    p.add_argument("--metadata", default=None, help="metadata JSON (default: domain metadata)")
    p.set_defaults(func=cmd_recategorize)
