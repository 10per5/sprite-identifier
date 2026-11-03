# Spell-catalogue audit & workflow handover

Status: **§6 implemented** (gate, strict LLM merge, spell review steps, `revert`). `out/` untouched —
`revert --apply` not run on the real catalogue yet (dry-run: 518 flagged).
Everything below is reproducible with the commands in [Reproduce](#reproduce).

---

## 1. TL;DR

* `build` promotes **every** metadata entry it can find on disk. It has no completeness
  gate, so un-named / tag-less / failed-LLM icons were moved straight into `out/@spells`.
* The **item** flow is gated by a human (`review-approval` → `move-approved` → build from
  `out/items/approved`). The **spell** flow is not: the spells domain profile simply omits
  those two steps from its wizard step list, so `index → label/infer → build` runs
  unsupervised and builds straight out of the raw input folder `in/spellicon`.
* Current damage: **1651 catalogue PNGs = 1651 metadata entries** (100% promoted), of which
  ~700 are junk; `catalog.toml` only indexes **916** of them (735 silently lost).
* Fix plan (§6): a shared readiness gate (`lib/core/quality.py`), strict LLM-result
  acceptance in `label`/`infer`, a gate in `build`, review steps for spells, and a new
  reversible `revert` command to undo the bad promotions.

---

## 2. Why the two workflows diverge

| | items | spells |
|---|---|---|
| wizard steps | `normalize, dedup, index, review-label, auto-name, label, infer, review-approval, move-approved, build, viewer` | `index, dedup, label, infer, build, viewer` |
| build source | `out/items/approved` (human-approved only) | `in/spellicon` (**raw input**, consumed) |
| post-LLM gate | `review-approval` + `move-approved` | **none** |

Sources: `lib/domains/spells.py:393` (`wizard_steps=("index","dedup","label","infer","build","viewer")`),
`lib/domains/spells.py:378` (`build_sources=("in/spellicon",)`), `lib/wizard.py:122-131`
(items build from `domain.approved_dir`, spells from `domain.build_sources`),
`README.md:85-89` documents the omission as intentional ("spell packs ... are curated").

The assumption behind it was "spell packs already carry names". That is only true for the
*filename*, and `index` happily converts filenames into metadata `name` + heuristic
`category` (`lib/cli/index.py:127-136` + `lib/domains/spells.py:332` `classify`), so an icon
named `Icon37.png` becomes a legit-looking entry `name="icon37", category="offensive"`
**before any LLM ever sees it**. From `build`'s point of view that entry is complete.

## 3. Catalogue audit (`out/spells/metadata.json`, 1651 entries)

| symptom | count | example |
|---|---|---|
| no tags **and** no LLM evidence at all | 262 | `arm_drop_spear` — never got a usable response |
| partial/failed pass (has `vllm_name`, empty tags) | 256 | `radiant_fury`, `iron_helmet`, `golden_boots` |
| placeholder / empty name | 235 with the current rule (`lib/core/quality.py`: also pack-word + counter names like `ability_icons1_12`); the narrow regex below only finds 58 | `""`, `icon13`, `icon43`, `icon48` |
| category outside the taxonomy | 3 | `def` (`spear_on_shield`), `off` (`verdant_radiance`), `mov` (`golden_boots`) |
| `confidence` set | **0 / 1651** | never written, always `None` |
| catalogue PNGs / TOMLs | 1651 / 1652 | every single entry was promoted |
| entries in `catalog.toml` | **916** | 735 catalogue assets missing from the index |
| duplicate `(category, name)` in catalogue | 138 | build suffixes the *file* (`name_1.png`) but not the `name` field |
| source packs referenced in metadata | 34 | e.g. `48 Spearman Skills Icons Pixel Art`, `dustychest-icons` — most no longer on disk (consumed by `build`) |

Only 50 icons remain in `in/spellicon` (48 in `necromancer2`, 1 `fire`, 1 `geomancer-…`) —
everything else was moved into the catalogue.

## 4. Root causes (all in code, all fixable)

1. **`build` promotes anything** with a name + category; no readiness check at all
   (`lib/cli/catalog.py:83-120`).
2. **`index` pre-fills name + category** from filename heuristics, so entries look complete
   pre-LLM (`lib/cli/index.py:127-136`, `lib/domains/spells.py:332-368`).
3. **Empty/partial LLM responses count as success.** `lib/llm/orcarouter.py:140-147` returns
   `tags: []` with no error; only `notes` starting with `error:` are rejected in
   `lib/core/llm_pass.py:185-192`. `lib/cli/label.py:39-50` then writes the empty tags into
   `vllm_tags`, and salvaged/partial JSON (`_salvage_json`) can produce an empty `name`.
4. **No category canonicalisation.** Whatever string the model returns is stored verbatim →
   `def`, `off`, `mov` became catalogue folders (`out/@spells/def`, `/off`, `/mov`).
5. **`confidence` is never persisted.** `_apply_result` sets `vllm_confidence` only
   (`lib/cli/label.py:48`, `lib/cli/infer.py:72`); `entry["confidence"]` stays `None`, which
   is why no confidence threshold could ever have protected the catalogue.
6. **Failed passes keep the fallback name** `name or sprite_id` (`lib/llm/orcarouter.py:133`),
   i.e. the input filename (`Icon43`) is laundered into a "name".
7. **`catalog.toml` is rewritten from scratch per run** (`lib/cli/catalog.py:256-263`) and
   only includes entries matched *in that run* → previously catalogued assets silently drop
   out of the index (the 735 above).
8. **Spells build consumes the input folder** (`build_sources=("in/spellicon",)`), making the
   promotion irreversible by hand and different from items (approved folder).

## 5. Overwrite / collision guards today

* **Within one category:** `lib/cli/catalog.py:111-117` (and 204-210 for recolors) append
  `_1`, `_2`, … when `<category>/<name>.png` exists → **no file clobbering**.
* **Identical images:** everything is keyed by `pixel_hash`; `index_sprites_by_hash` uses
  `setdefault`, so duplicates collapse to one file (`lib/cli/catalog.py:30-44`).
* **But there is no name-level guard:**
  * the suffixed duplicates keep the *same* `name` in their TOML → 138 duplicate
    `(category, name)` pairs today, so `catalog.toml` consumers can't tell them apart;
  * placeholder names collide by design (`unnamed`, `unnamed_1`, `unnamed_2`, … from
    `sanitize_name` in `lib/util/paths.py:32-38`; ~6 entries already have an empty name);
  * distinct categories may legitimately share a name, nothing warns;
  * when `catalog.toml` is rebuilt, an asset dropped from the index can be re-added later
    under a new suffix → duplicate entries with no dedupe pass.
  So: **file-level overwrite is safe, identity-level dedupe is not.**

## 6. Fix plan (not implemented yet)

1. `lib/core/quality.py` (new) — shared rules used by every stage:
   * `canonical_category()` — normalises/`alias`-expands to the domain vocabulary
     (`off→offensive`, `def→defensive`, `mov→movement`, unique prefixes), returns `None` on junk;
   * `is_placeholder_name()` — `unnamed`, `sprite_0031`, `icon12`, digits-only, empty;
   * `entry_issues(entry, domain)` → reason codes: `placeholder_name`, `invalid_category`,
     `missing_tags`, `unprocessed` (no `vllm_*` and not manual), `flagged_for_review`,
     optional `low_confidence`;
   * `apply_llm_result()` — merge a model response **only** if it is actually useful
     (never overwrite a good name/tags with empty ones, always persist `confidence`, set
     `needs_review` + `llm_status` when a response is incomplete).
2. `lib/cli/label.py` + `lib/cli/infer.py` — use `apply_llm_result`; an incomplete response
   leaves the entry pending, so it is retried instead of being catalogued.
3. `lib/cli/catalog.py` — build skips entries with issues unless `--allow-unsafe`; prints a
   per-reason summary; optional `--report out/<domain>/build_blocked.json`; keeps previously
   catalogued assets in `catalog.toml` instead of clobbering it; `--copy` as a
   non-destructive alternative to the current move.
4. `lib/domains/spells.py` — add `review-approval` + `move-approved` to `wizard_steps`, add
   `in/spellicon-named` (README already promises it) to `build_sources`, add
   `category_aliases`.
5. `lib/wizard.py` — surface blocked counts in the build/viewer step status.
6. `lib/cli/revert.py` (new `sprite.py revert`) — **reversible undo**:
   * default **dry-run**, `--apply` to execute;
   * moves flagged catalogue PNGs back to their recorded source path
     (`entry["path"]`, e.g. `spellicon/<pack>/<file>.png` → `in/…`), so nothing is lost and the
     pre-build state is restored exactly;
   * quarantines their TOMLs under `out/<domain>/reverted/<stamp>/` (never deletes);
   * writes a manifest `out/<domain>/revert-<stamp>.json`, and supports
     `--from-manifest` to replay the move back into the catalogue;
   * regenerates `catalog.toml` from what remains (also repairs the 735 missing entries).
7. `README.md` — document the gate, the spells review steps, and `revert`.

### Revert dry-run (safe, nothing moves)

```bash
python3 sprite.py revert --domain spells            # plan only
python3 sprite.py revert --domain spells --all      # plan: undo the whole catalogue
python3 sprite.py revert --domain spells --apply    # move flagged icons back to in/spellicon
python3 sprite.py revert --domain spells --from-manifest out/spells/revert-<stamp>.json --apply
```

---

## Reproduce

```bash
python3 - <<'PY'
import json, collections, re
from pathlib import Path
E = json.loads(Path('out/spells/metadata.json').read_text())["entries"]
CATS = {"offensive","defensive","healing","buff","debuff","control","summoning",
        "movement","utility","divination","other"}
P = re.compile(r'^(unnamed|sprite_?\d+|icon_?\d+|\d+)$', re.I)
bad = {h: [r for r in (
        "placeholder_name" if not (e.get("name") or "").strip() or P.match(e.get("name") or "") else None,
        "invalid_category" if (e.get("category") or "") not in CATS else None,
        "missing_tags" if not e.get("tags") else None,
        "unprocessed" if not (e.get("vllm_tags") or e.get("vllm_name")) else None,
      ) if r] for h, e in E.items() if isinstance(e, dict)}
print(len(E), sum(1 for v in bad.values() if v))
print(collections.Counter(r for v in bad.values() for r in v))
PY
```
