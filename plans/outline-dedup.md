# Deferred: silhouette/outline-based dedup

**Status:** deferred — idea captured, not implementing now (too complicated for
the current pass, and the current dedup is not good enough to build on).

## What we learned from the current dedup

`dedup` (lib/cli/dedup.py) was meant to find ~98%-identical near-duplicates and
same-shape/different-color recolor groups. In practice, on the spell packs:

1. **It grouped 731 unrelated icons under one "base".** 734/735 spellicon PNGs
   are fully opaque (no alpha channel), so `classify()`'s shape test
   (`mean |Δalpha|` over the whole canvas) is trivially 1.0 for every pair and
   never returns "different". Color similarity then decided, and anything
   ≥2% color-different was stamped "recolor". The first file in sorted order
   (`3-spear-parallel.png`) became the base for the entire remaining set.
2. **It is O(n²) pairwise** (`analyze()` compares every image with every later
   image). 735 icons ≈ 270k comparisons; the dcss reference set (5359 images)
   ≈ 14M — minutes of pure CPU per run, with nothing useful coming out.
3. The one metric that was computed honestly — `changed_pixel_fraction` over
   overlapping opaque pixels (0.74–1.00 for those 731) — was never used as a
   veto, so garbage groups were written to metadata anyway.

A classifier fix (union-of-opaque-pixels similarity + rejecting "recolor" when
the silhouette is uninformative, i.e. fully opaque) landed in `lib/cli/dedup.py`.
It stops the false mega-groups and keeps genuine transparent-sprite recolors
working, but it does not make dedup *useful* on opaque packs — with no alpha,
shape is simply not measurable, so opaque icons can only be "identical" or
"different".

## The idea we actually want (outline-based matching)

Real spell icons are distinguishable by **which pixels form the outlines inside
the icon** — the internal edge structure (blade silhouettes, rune strokes, glow
boundaries, pose gestures), not the raw color values and not the outer alpha
mask.

Proposed shape (not started):

1. **Normalize / background-remove first.** Chroma-key or flood-fill the opaque
   background to transparency so there *is* a silhouette to compare. This also
   fixes the fully-opaque degenerate case at the source.
2. **Extract a structural signature per icon instead of comparing images
   pairwise:**
   - edge map (e.g. Sobel/Canny-style gradient magnitude on the resized sprite),
   - or a coarse line/stroke graph from the edge map (which pixels belong to
     outlines vs fills),
   - plus the existing 8×8 perceptual hash as a coarse first bucket.
3. **Bucket, don't compare.** Hash/signature the structural descriptors, then
   only compare within buckets (near-neighbour index), turning O(n²) into ~O(n)
   lookups. Group threshold becomes "same outline graph + recolored palette"
   (hue-rotate the fills, keep the edges), which is what a true recolor is.
4. Optionally fold `changed_pixel_fraction` back in as a *veto*: same shape but
   >95% of pixels re-colored is suspicious; same shape + localized palette
   change is a recolor.

## Rough effort / blockers

- Edge detection + graph extraction on 32×32 pixel art is fiddly (single-pixel
  lines, dithering, glow bleeding into edges). Needs a small prototype to see
  whether outlines are even stable across the pack styles (spearman pixel art,
  ooze renders, EarthMage icons).
- Cheap indexing is easy; *good* shape descriptors are the hard 20%.
- This is a "nice-to-have" cleanup pass, not on the critical path: the
  catalogue (build) and AI labelling already work without it.

## If/when we pick this up

- Write `lib/core/silhouette.py` with the structural signature + bucketing.
- Replace the pairwise loop in `analyze()` with the bucket pass.
- Re-run on `in/spellicon` (735) and a `dcss` sample; compare groups against a
  hand-labelled expectation before enabling any `--apply` path.
