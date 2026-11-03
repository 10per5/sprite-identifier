# Sprite & Spell Asset Management System

A pipeline for normalizing, de-duplicating, AI-tagging, and cataloguing game
assets. It ships with two ready-made **domains** — roguelike items and fantasy
spell icons — and shares one set of mechanics (hashing, metadata, LLM retries,
review pages, TOML catalogues) between them.

Everything is driven from a single entry point, `sprite.py`, which exposes both
individual subcommands **and** an interactive **guided wizard**
(`sprite.py guided`, or just `sprite.py`) that walks you through the selected
domain's workflow step by step.

## Layout

```
project/
├── sprite.py            # single CLI entry point (subcommands + guided wizard)
├── lib/
│   ├── __init__.py
│   ├── wizard.py        # interactive 0-to-hero guided mode
│   ├── util/            # shared helpers
│   │   ├── paths.py     # project_path(), sanitize_name(), iter_images(), atomic_save()
│   │   └── toml.py      # toml_dumps()
│   ├── core/            # pipeline mechanics (no LLM, no UI, no domain vocabulary)
│   │   ├── pixel_hash.py      # 8x8 perceptual hash → 64-bit hex string
│   │   ├── sprite_metadata.py # hash-keyed unified metadata store
│   │   ├── llm_pass.py        # shared label/infer loop, progress, locking, retries
│   │   └── schema.py          # new_entry() canonical metadata dict builder
│   ├── domains/         # asset-family vocabularies and defaults
│   │   ├── base.py            # Domain + PromptSet profiles
│   │   ├── items.py           # item taxonomy, prompts, category mapping
│   │   └── spells.py          # spell taxonomy, prompts, category mapping
│   ├── llm/             # LLM client
│   │   ├── orcarouter.py      # OrcaRouter client (OrcaRouter API key required)
│   │   └── taxonomy.py        # backward-compatible item prompt re-exports
│   └── cli/             # one module per subcommand
│       ├── normalize.py  split.py  dedup.py  catalog.py
│       ├── label.py  infer.py  index.py  review.py
├── template/            # Jinja2 HTML templates for offline review pages
│   ├── sprite_label_review.html.j2
│   └── sprite_approve_reject.html.j2
├── in/
│   ├── sprites/         # extracted/normalized item PNGs (source)
│   ├── dcss/            # DCSS item reference PNGs (categorized by folder)
│   ├── spellicon/       # raw spell icons (mix of PNG/JPG)
│   └── spellicon-named/ # spell icons already named by hand
└── out/
    ├── @items/          # item catalogue: <category>/<name>.png + .toml
    ├── @spells/         # spell catalogue: <category>/<name>.png + .toml
    ├── items/           # item runtime state: metadata, labels, review, approved/
    └── spells/          # spell runtime state: metadata, labels, review, approved/
```

> **Domains vs. mechanics.** `lib/core/` and the CLI contain no item-specific
> categories or prompts. A `Domain` (in `lib/domains/`) supplies the vocabulary,
> prompts, default folders, and a small `classify(path)` hint function. That is
> what lets items and spells reuse the same pipeline without copy-paste.


> **Note:** the old flat scripts (`manage_sprites.py`, `build_catalog.py`,
> `fuzzy_dedup.py`, `normalize_sprites.py`, `split_sheets.py`, `vllm_label.py`,
> `infer_sprites.py`, `pixel_hash.py`, `sprite_metadata.py`,
> `sprite_categories.py`, `tag_taxonomy.py`, `orcarouter.py`) have been removed
> and consolidated into the `lib/` packages above.

---

## Setup

Python 3.11+ is required. Dependencies are declared in `pyproject.toml` and
managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync                    # create .venv and install jinja2, numpy, pillow
uv sync --extra toml       # optionally add tomli-w for TOML output
uv run sprite.py           # run any command inside the managed environment
```

Without uv, `pip install jinja2 numpy pillow` into a virtualenv works too.

---

## Quick start — the guided wizard

You don't have to memorize any commands. Run:

```bash
python3 sprite.py                    # guided item workflow
python3 sprite.py --domain spells    # guided spell workflow
python3 sprite.py guided --domain spells   # equivalent
```

The wizard inspects your project state (normalized? metadata present? AI key
set? catalogue built?) and presents the next sensible steps as a numbered menu.
Pick a number to run a step, `r` to refresh state, `a` to run all remaining in
order, or `q` to quit. AI-dependent steps are flagged ⚠ until
`ORCAROUTER_API_KEY` is exported.

The step list comes from the domain profile. Items get normalize/dedup/index/
review-label/auto-name/label/infer/review-approval/move-approved/build. Spells
get index/dedup/review-label/apply-labels/label/infer/review-approval/
move-approved/build. Spells skip normalize/rename (icons keep their pack names)
and auto-name (there is no reference art to match against). As with items,
manual labeling comes before the AI passes and is optional: the review-label
page lists only icons that are not catalogue-ready (placeholder names like
`Icon13`, no tags), `apply-labels` writes back the cards you edited, and
`label` skips icons you already tagged by hand. Both
domains share the same post-AI gate: a human approves each icon, and `build`
only promotes entries that pass the [readiness gate](#readiness-gate). Spell
dedup is report-only: it never deletes icons, since recolors are often distinct
spells. Both domains end with `viewer` (browse the built catalogue) once
`build` has run.

---

## Subcommands (when you want to drive it manually)

All commands are `python3 sprite.py <command> [options]`.

| Command | Purpose |
|---------|---------|
| `normalize` | 32×32 transparent canvas, exact dedupe, sequential rename |
| `split` | Split multi-frame sprite sheets into individual PNGs |
| `dedup` | Near-duplicate detection + recolor groups (hash-based, `--apply` deletes; spell wizard runs it report-only) |
| `index` | Scan a reference folder (e.g. `dcss/`) into unified metadata (VLLM hints) |
| `auto-name` | Fill names/categories from catalogue entries via `pixel_hash` |
| `review-label` | Offline grid labeling page (name, category, tags) with outline matches; `--pending-only` lists just the icons the build would block |
| `label` | AI tagging via OrcaRouter (`deepseek-v4-flash-vision-exp`), `--move-tagged-to` moves tagged out |
| `infer` | Re-infer category/subcategory/tags for named sprites (cheap text-only by default; `--force` refreshes after a rename) |
| `apply-labels` | Write cards edited on the labeling page (name/category/tags) into metadata as manual labels; untouched cards are ignored |
| `review-approval` | Single-sprite Y/N approve/reject page (post-AI), writes decisions JSON |
| `move-approved` | Commit manual edits (and the approve/reject decision) to metadata, then move approved sprites into the domain's approved folder |
| `build` | Assemble the catalogued library (`out/@items/`) + `catalog.toml`; blocks entries that fail the readiness gate |
| `revert` | Move catalogue assets that fail the gate back to the input folder (dry-run unless `--apply`; `--from-manifest` undoes) |
| `viewer` | Browse the built catalogue (`out/@items` / `out/@spells`) as an offline HTML page |
| `guided` | Launch the interactive 0-to-hero wizard for the selected domain (also the default with no args) |

### Asset domains (`--domain`)

The commands that read/write metadata, review pages, or catalogues accept
`--domain items|spells` (default: `items`). The option can go before or after
the command:

```bash
python3 sprite.py --domain spells build
python3 sprite.py build --domain spells   # equivalent
python3 sprite.py --domain spells         # spell CLI quickstart
```

A domain supplies its own defaults:

| | `items` | `spells` |
|---|---|---|
| source folder | `in/sprites` | `in/spellicon` |
| runtime state | `out/items/` | `out/spells/` |
| metadata | `out/items/metadata.json` | `out/spells/metadata.json` |
| catalogue | `out/@items` | `out/@spells` |
| categories | weapons/armor/food/... | offensive/defensive/healing/... |
| scan subfolders | no | yes (spell packs are nested) |

`build --domain spells` reads `out/spells/approved` (icons approved via
`move-approved`) and the curated `in/spellicon-named` by default. Raw packs in
`in/spellicon` are never built directly. Pass `--sprites DIR` (repeatable) to
override.

Runtime files live in one state directory per domain (`out/items/`,
`out/spells/`): metadata, exported labels, review pages, approval JSON, and the
approved-image folder. The catalogue assets themselves are still grouped by
**category** under `out/@items/<category>/` and `out/@spells/<category>/`.
Metadata stays as one JSON per domain (not per category) because it is the
hash-keyed lookup index used for recolors, resume/progress, and cross-source
matching.

---

## Spell icons

Spell icons reuse the exact item pipeline; only the domain vocabulary and
prompts differ. The worker split is deliberate:

* **`label`** sends an image to the vision model — use it for the *unnamed*
  packs in `in/spellicon/`.
* **`infer`** is text-only and preserves the existing name — use it for the
  *already-named* files in `in/spellicon-named/` to fill in the remaining
  category/subcategory/tags cheaply.

```bash
export ORCAROUTER_API_KEY=your_key

# 1. Optional manual labeling first (grid page): name/tag any icons by hand.
python3 sprite.py index --domain spells --dir in/spellicon
python3 sprite.py review-label --domain spells --pending-only  # -> out/spells/label_review.html
#    Export labels.json from the page into out/spells/, then:
python3 sprite.py apply-labels --domain spells

# 2. Already-named icons: index names/category hints, then fill tags.
python3 sprite.py index --domain spells --dir in/spellicon-named
python3 sprite.py infer --domain spells --sprites in/spellicon-named

# 3. Everything else: the vision model names/categorises/tags it
#    (icons you tagged by hand in step 1 are skipped).
python3 sprite.py label --domain spells --sprites in/spellicon

# 4. Review: approve/reject each icon, fixing names/categories on the page
python3 sprite.py review-approval --domain spells   # -> out/spells/approve_reject.html
python3 sprite.py move-approved --domain spells     # merge edits, move approved

# 5. Assemble out/@spells/<category>/<name>.png + .toml + catalog.toml
python3 sprite.py build --domain spells

# 6. Browse the result (offline HTML page)
python3 sprite.py viewer --domain spells
```

> **Note:** like the item `build`, the spell build *promotes* (moves) source
> images into the catalogue (`--copy` keeps the source). JPG inputs are decoded
> and written as PNG. Review ids for nested packs are `<pack>/<file>`, and
> decisions are matched by pixel hash, so equal filenames in different packs
> don't collide.

### Readiness gate

`build` checks every entry before promoting it (`lib/core/quality.py`). An entry
is blocked, left where it is, and listed in `out/<domain>/build_blocked.json`
when it has:

| reason | meaning |
|---|---|
| `placeholder_name` | empty, `unnamed`, `icon12`, `sprite_0031`, digits only |
| `invalid_category` | not in the domain vocabulary (aliases such as `off`→`offensive` are expanded when results are stored) |
| `missing_tags` | no tags |
| `unprocessed` | never got a usable LLM response and nobody edited it |
| `flagged_for_review` | the last LLM response was incomplete |
| `rejected` | rejected on the approve/reject page |
| `low_confidence` | below `--min-confidence X` (opt-in) |

Approving an icon waives the soft checks (tags, unprocessed, review flag,
confidence). It never waives name or category, because those decide the
catalogue path. `--allow-unsafe` disables the gate entirely.

`label`/`infer` only store a model response when it has a real name, a known
category and at least one tag. Anything less is kept under `vllm_last` with
`needs_review`, and the entry is retried on the next run. After 3 incomplete
responses it is left for the approve/reject page.

`catalog.toml` is regenerated from every per-sprite TOML in the catalogue, so
assets from earlier runs stay indexed. Each record has a unique `id`
(`<category>/<file stem>`) even when two assets share a `name`.

### Undoing bad promotions (`revert`)

```bash
python3 sprite.py revert --domain spells            # dry-run: list flagged catalogue assets
python3 sprite.py revert --domain spells --all      # dry-run: the whole catalogue
python3 sprite.py revert --domain spells --apply    # move flagged icons back to in/spellicon
python3 sprite.py revert --domain spells --from-manifest out/spells/revert-<stamp>.json --apply
```

`--apply` moves each flagged PNG back to its recorded source path
(`entry["path"]`, e.g. `in/spellicon/<pack>/<file>.png`). It quarantines the
TOMLs under `out/<domain>/reverted/<stamp>/`, writes a manifest that
`--from-manifest` can replay in reverse, prunes empty category folders and
regenerates `catalog.toml`. Metadata is not modified, so the reverted icons go
back through label/infer → review → build.

---

## Catalogue viewer

Once a catalogue is built (`out/@items` / `out/@spells`), you can browse it as
a self-contained offline HTML page — no server needed:

```bash
python3 sprite.py viewer --domain items    # -> out/items/catalog_view.html
python3 sprite.py viewer --domain spells   # -> out/spells/catalog_view.html
```

The page is generated from `catalog.toml` (the master index written by
`build`), so it always reflects exactly what is in the catalogue:

- searchable grid filtered by name/category/tag and by top-level category
- recolor variants grouped under their base image (toggle to flat view, `g`)
- click any sprite for a details lightbox (tags, pixel hash, source,
  confidence, base/recolor links) with prev/next navigation
- `/` focuses the search box, `Esc` closes the lightbox

It reuses the same shared CSS/JS design system as the review pages, which all
live in `lib/cli/_html.py` and are inlined at render time — edit once, every
generated page picks it up next time it is regenerated.

---

## Workflows

### 1. Normalize sprites

Standardize all PNGs in `in/sprites/` to a 32×32 transparent canvas, drop exact
duplicates, and rename sequentially (`sprite_0001.png` …):

```bash
python3 sprite.py normalize --sprites in/sprites --size 32
```

Use `--dry-run` to see what would change without modifying files.

### 2. Delete identical duplicates (external)

Run `fdupes` on your host to remove exact byte-identical duplicates:

```bash
fdupes -r -d -N in/sprites/
```

After this, run fuzzy dedup to catch near-identical renders and catalogue recolors.

### 3. Fuzzy near-duplicate detection + recolor catalogue

Find images that are ~98%+ pixel-identical, and catalogue same-shape/different-color
recolor groups into unified metadata:

```bash
python3 sprite.py dedup --dir in/sprites --threshold 0.98 --apply --yes
```

`--apply` deletes all-but-first in each identical group (prompts `y/N`; `--yes`
skips prompt). Recolor groups store `base_hash → {variant_hash: {...}}` in the
unified metadata file — so you know exactly which colors changed, where, and by how much.

### 3b. Split multi-frame sprite sheets (optional)

Some reference directories pack multiple animation frames into a single PNG sheet
with transparent gutters. Split them before cataloguing:

```bash
python3 sprite.py split --dir dcss/scythe --output dcss/scythe_split --min-area 200
python3 sprite.py split --dir dcss/scythe --dry-run   # inspect first
```

Then index the split output:

```bash
python3 sprite.py index --dir dcss/scythe_split --source dcss
```

### 4. Preindex DCSS (+DLSS) into catalogue

Scan reference folders into unified metadata so auto-name and the AI can match
sprites by `pixel_hash` against catalogue names/categories:

```bash
python3 sprite.py index --dir dcss --source dcss
python3 sprite.py index --dir dlss --source dlss   # if you have a dlss/ folder
```

Each PNG gets `pixel_hash` as key, name from filename stem, category from folder
path. These become hints for the AI (name+category prefilled) and targets for
auto-name matching.

### 5. Label sprites (manual, optional)

Generate the offline labeling page:

```bash
python3 sprite.py review-label --sprites in/sprites \
  --labels out/items/labels.json --output out/items/label_review.html --top 6
```

Open the HTML in a browser, label sprites, export to `out/items/labels.json`.

### 5b. Auto-name from catalogue (optional)

Fill in names/categories from catalogue entries via `pixel_hash` matching, and
merge exported labels from the review page:

```bash
python3 sprite.py auto-name --sprites in/sprites
```

Infers category from name where still unclassified.

### 6. AI auto-tagging

Send each sprite to `deepseek-v4-flash-vision-exp` via OrcaRouter:

```bash
export ORCAROUTER_API_KEY=your_key
python3 sprite.py label --sprites in/sprites
```

- Skips sprites that already have AI tags
- Saves every 5 sprites (atomic writes + progress file, Ctrl+C safe)
- `--move-tagged-to <folder>` moves tagged sprites out of `in/sprites/` as they're done
- `--debug` prints raw failed responses
- Failed/empty responses are retried with backoff; still-failed sprites stay untagged for retry

### 6b. Re-inference pass (`infer`)

Re-fill missing `category`/`subcategory`/`tags` for already-named sprites. Because
the sprites are named, this **defaults to a cheap TEXT-ONLY model** (no image sent)
— category/subcategory/most tags come from the name + taxonomy. Visual-only tags
(actual color, glow, material sheen) are the only thing lost vs. vision.

```bash
export ORCAROUTER_API_KEY=your_key
python3 sprite.py infer --sprites in/sprites          # cheap text-only (default)
python3 sprite.py infer --vision                      # opt into the vision model
python3 sprite.py infer --model qwen/qwen3.5-flash    # override the model
python3 sprite.py infer --force                       # refresh every named sprite, even if complete
```

`infer` normally skips entries that already have name + category +
subcategory + tags. A manual rename is detected automatically (the name no
longer matches the model's previous `vllm_name`), so wizard step 7 refreshes
those entries once. Use `--force` to refresh *every* named sprite on disk
regardless; the name itself is never overwritten.

The API key is validated up front, so a bad/expired key fails fast with a clear
message instead of grinding through every sprite.

**Speed controls (deadline + fallback).** If a model is slow, `infer` won't
grind through retries on a model that isn't responding — it fails over to another
model within a hard per-sprite budget:

```bash
python3 sprite.py infer --timeout 20 --deadline 60 \
    --fallback-model deepseek/deepseek-v4-flash-vision-exp
```

- `--timeout N` (default 20): per-request socket timeout in seconds. Capped by
  the remaining deadline budget, so a single hung request can't eat the whole budget.
- `--deadline N` (default 60): max wall-clock seconds spent on one sprite across
  all models + retries. When exceeded the sprite is skipped (logged as an error)
  instead of stalling the batch, and you can simply re-run to retry it.
- `--fallback-model MODEL` (repeatable): extra model(s) to try after the primary
  if it is slow or errors (non-auth). Default fallback is the vision model
  (`deepseek/deepseek-v4-flash-vision-exp`), which is currently responsive — so a
  slow text-only run escalates to it automatically. Add it multiple times for an
  ordered chain (e.g. `--fallback-model a --fallback-model b`).

Fast, transient errors (empty completions, truncated/malformed JSON — common with
flash models) are retried on the **same** model up to a few times before failing
over, since a second attempt usually succeeds. Slow/time-out/HTTP errors fail over
to the next model right away so the deadline isn't wasted on a stuck model.

The model that actually produced each result is shown in the per-sprite log, e.g.
`[1/44] double_golden_morningstar (…) → … | … [deepseek/deepseek-v4-flash-vision-exp]`,
so you can see when the fallback kicked in.

### 7. Post-AI approve/reject pass (manual)

Single-sprite review page to approve (Y) or reject (N) each AI result. Rejected
sprites stay in `in/sprites/` for a second pass or manual redo. Decisions
auto-save to browser localStorage.

```bash
python3 sprite.py review-approval
```

Open `out/items/approve_reject.html` locally:

- **Y** = approve · **N** = reject · **ArrowRight/Left** = navigate · **Esc** = unfocus inputs so Y/N work
- Edit name/category/tags manually before deciding
- **Ctrl+S / "Save JSON"** writes `out/items/approve_reject.json`
- Counts only include sprites in the current page; saved decisions for ids that
  are no longer present are shown as a `stale` warning and kept in the JSON

Then commit your manual edits and move approved sprites out of `in/sprites/` so
only failed/rejected remain:

```bash
python3 sprite.py move-approved                 # merges edits, then moves approved
python3 sprite.py move-approved --merge-only    # only write edits into metadata
```

`move-approved` applies the name/category/subcategory/tags from **every**
decision (approved, rejected, or pending) into the hash-keyed metadata before
moving files. New decision exports also carry `pixel_hash`, so duplicate files
that share an image are updated together.

### 8. Build the item catalogue

Move labeled sprites into `out/@items/<category>/<name>.png` with TOML metadata:

```bash
python3 sprite.py build --domain items --sprites out/items/approved --target out/@items
```

Output: `out/@items/catalog.toml` + `<category>/<name>.png` + `<name>.toml` per
sprite. Recolor variants go to `<category>/recolor/`. (For spell icons see
[Spell icons](#spell-icons) above; the same command is used with
`--domain spells`.)

> **Note:** `build` *moves* (promotes) sprites from `--sprites` into `--target`.
> Point `--sprites` at `out/items/approved` (or wherever approved sprites live), not at
> your only copy of the source.

---

## Hash-based linking

All metadata uses `pixel_hash` (8×8 grayscale perceptual hash, rendered as a
16-char hex string) as the cross-reference key instead of pathnames. Renaming or
moving files won't break references:

- Sprite → catalogue match: compute sprite's `pixel_hash`, look it up in metadata
- Recolor variants: `base_hash → [variant_hashes]` in `recolor_groups`
- AI results, manual labels, and auto-names are all keyed by `pixel_hash`

Single source of truth per domain: `out/items/metadata.json` for items,
`out/spells/metadata.json` for spells.

```
out/@items/
├── catalog.toml              # master index (all entries)
├── weapons/swords/
│   ├── long_sword.png
│   └── long_sword.toml
├── armor/chest/
│   ├── chain_mail.png
│   └── chain_mail.toml
└── other/unclassified/
    └── sprite_0036.toml      # unnamed sprites, ready for manual naming
```

Recolor variants go to `out/@items/<category>/recolor/<name>.png` with
`is_recolor = true` and `base_image` pointing to the base sprite.

Each `<name>.toml`:

```toml
name = "long_sword"
category = "weapons/swords"
subcategory = "longsword"
reference = "dcss/sword/long_sword.png"
source = "dcss"
confidence = 0.95
pixel_hash = "a1b2c3d4e5f6..."
is_recolor = false
base_image = null
recolor_of = null
recolor_variants = ["long_sword_red", "long_sword_blue"]
tags = ["steel", "melee", "one_handed"]
```

Spell entries use the same shape, with a spell-centred vocabulary:

```toml
name = "frost_nova"
category = "offensive"
subcategory = "ice"
source = "spellicon"
pixel_hash = "0123456789abcdef"
tags = ["ice", "area", "burst", "damage", "slowing", "blue", "crystalline"]
```

Spell **categories** describe what the spell does: `offensive`, `defensive`,
`healing`, `buff`, `debuff`, `control`, `summoning`, `movement`, `utility`,
`divination`, `other`. **Subcategories** describe the element/school: `fire`,
`ice`, `lightning`, `earth`, `air`, `water`, `poison`, `holy`, `shadow`,
`nature`, `arcane`, `physical`. Tags add function, target, effect, visual,
quality, school, size, and color descriptors.

---

## Full end-to-end example

```bash
# 0. (Optional) launch the guided wizard instead of typing these by hand:
python3 sprite.py

# 1. Normalize source sprites
python3 sprite.py normalize --sprites in/sprites

# 2. Delete exact duplicates (external)
fdupes -r -d -N in/sprites/

# 3. Fuzzy dedup + catalogue recolors into unified metadata
python3 sprite.py dedup --dir in/sprites --threshold 0.98 --apply --yes

# 4. Preindex DCSS (+DLSS) into catalogue (gives AI name+category hints)
python3 sprite.py index --dir dcss --source dcss

# 5. (Optional) Manual labeling page
python3 sprite.py review-label --sprites in/sprites \
  --labels out/items/labels.json --output out/items/label_review.html

# 6. Open the HTML, label sprites, export

# 7. Auto-name remaining blanks via pixel_hash matching against catalogue
python3 sprite.py auto-name --sprites in/sprites

# 8. AI tagging — moves tagged sprites out of in/sprites/ as it goes
export ORCAROUTER_API_KEY=your_key
python3 sprite.py label --sprites in/sprites --move-tagged-to tagged

# 9. Post-AI approve/reject pass
python3 sprite.py review-approval

# 10. Open out/items/approve_reject.html, Y/N each sprite, save JSON

# 11. Move approved sprites out of in/sprites/
python3 sprite.py move-approved --to out/items/approved

# 12. Fix rejected sprites (second LLM pass or manual), then build
python3 sprite.py build --sprites out/items/approved --target out/@items

# 13. Browse the built catalogue
python3 sprite.py viewer --domain items
```

---

## Category Taxonomy

Top-level: `weapons`, `armor`, `food`, `consumables`, `magic`, `treasure`,
`tools`, `accessories`, `environment`, `effects`, `currency`, `other`

Weapon sub-types include: `sword`, `dagger`, `shortsword`, `longsword`,
`broadsword`, `claymore`, `scimitar`, `sabre`, `rapier`, `estoc`, `zweihander`,
`katana`, `axe`, `battle_axe`, `war_axe`, `double_axe`, `mace`, `morning_star`,
`flanged_mace`, `war_hammer`, `maul`, `flail`, `scourge`, `spear`, `javelin`,
`pike`, `lance`, `trident`, `bow`, `short_bow`, `long_bow`, `crossbow`,
`bolt`, `arrow`, `shuriken`, `dart`, `staff`, `stave`, `whip`, `scythe`,
`polearm`, `glaive`, `halberd`, `claw`, `katar`

Armor sub-types: `helmet`, `chain_mail`, `plate_mail`, `scale_mail`,
`leather_armor`, `robe`, `shield`, `buckler`, `greaves`, `boots`, `gauntlets`

Food sub-types: `fruit`, `vegetable`, `meat`, `fish`, `bread`, `ration`

Tag materials: `steel`, `iron`, `bronze`, `silver`, `gold`, `wood`, `leather`,
`bone`, `stone`, `cloth`, `glass`, `crystal`, `obsidian`, `pearl`, `ivory`

Tag colors: `red`, `green`, `blue`, `yellow`, `orange`, `gray`, `black`,
`white`, `cyan`, `magenta`, `brown`, `golden`

Tag visuals: `glowing`, `magical`, `enchanted`, `cursed_item`, `blessed_item`,
`ancient`, `ornate`, `rusty`, `bloodstained`, `frosty`, `fiery`, `sparkling`

Tag quality: `common`, `uncommon`, `rare`, `magical`, `cursed`, `blessed`,
`ancient`, `elven`, `orcish`, `dwarven`, `giant`, `demonic`, `holy`, `rusty`,
`ornate`
