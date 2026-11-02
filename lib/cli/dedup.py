"""dedup — near-duplicate detection + recolor catalogue.

Find images that are ~98%+ pixel-identical, and catalogue same-shape/different-
color recolor groups into the unified metadata store (hash-based, so it
survives renames/moves).
"""
import argparse
import json

import numpy as np
from PIL import Image

from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import load, save, add_recolor_group
from lib.domains import add_domain_argument, resolve_domain
from lib.util.paths import project_path, iter_images


def image_data(path, size=32):
    """Return (rgb_vector, alpha_mask) for an image."""
    with Image.open(path) as im:
        im = im.convert("RGBA")
        im = im.resize((size, size), Image.Resampling.NEAREST)
        arr = np.asarray(im, dtype=np.float32) / 255.0
    rgb = arr[:, :, :3].ravel()
    alpha = arr[:, :, 3].ravel()  # float alpha, not boolean
    return rgb, alpha


def similarity(a, b):
    """Return similarity score in [0, 1] based on mean absolute difference."""
    mad = np.mean(np.abs(a - b))
    return 1.0 - mad


def classify(rgb_a, alpha_a, rgb_b, alpha_b, shape_thresh=0.98, color_thresh=0.98):
    """Classify relationship between two images.

    Returns:
        'identical'  - silhouette and color both >= threshold
        'recolor'    - silhouette >= shape_thresh, color < color_thresh
        'different'  - silhouette < shape_thresh

    Both shape and color similarity are measured over the *union of opaque
    pixels* only, so shared transparent/background pixels can't inflate the
    score. When both images are (nearly) fully opaque the silhouette carries
    no information at all — the alpha masks agree everywhere trivially — so
    only a near-identical color match can be 'identical' and everything else
    is 'different' (never 'recolor'). Without this guard, every pair of
    unrelated opaque icons collapses onto the first sorted file as a bogus
    recolor group.
    """
    opaque_a = alpha_a > 0.5
    opaque_b = alpha_b > 0.5
    union = opaque_a | opaque_b
    rgb_a2 = rgb_a.reshape(-1, 3)
    rgb_b2 = rgb_b.reshape(-1, 3)

    # Nearly-fully-opaque masks are trivially identical: "same shape" can't be
    # verified, so a recolor claim has no basis. Color decides identical vs not.
    if opaque_a.mean() > 0.995 and opaque_b.mean() > 0.995:
        sim_color = similarity(rgb_a2[union], rgb_b2[union]) if union.any() else 1.0
        return "identical" if sim_color >= color_thresh else "different"

    sim_shape = similarity(alpha_a[union], alpha_b[union]) if union.any() else 1.0
    if sim_shape < shape_thresh:
        return "different"
    sim_color = similarity(rgb_a2[union], rgb_b2[union]) if union.any() else 1.0
    if sim_color >= color_thresh:
        return "identical"
    return "recolor"


def region_colors(rgb, alpha, grid=4):
    """Average RGB per grid cell of non-transparent pixels.

    Returns list of [r, g, b] per cell, row-major, size grid*grid.
    Cells with no opaque pixels get [0, 0, 0].
    """
    h = w = 32  # image_data resizes to 32x32
    cell_h = h // grid
    cell_w = w // grid
    alpha2d = alpha.reshape(h, w)
    rgb2d = rgb.reshape(h, w, 3)
    regions = []
    for gy in range(grid):
        for gx in range(grid):
            y0, y1 = gy * cell_h, (gy + 1) * cell_h
            x0, x1 = gx * cell_w, (gx + 1) * cell_w
            cell_alpha = alpha2d[y0:y1, x0:x1]
            mask = cell_alpha > 0.5
            if not mask.any():
                regions.append([0.0, 0.0, 0.0])
                continue
            cell_rgb = rgb2d[y0:y1, x0:x1]
            regions.append([float(cell_rgb[:, :, i][mask].mean()) for i in range(3)])
    return regions


def color_delta_map(rgb_a, alpha_a, rgb_b, alpha_b, grid=4):
    """Per-cell RGB delta between two images (variant - base).

    Returns list of [dr, dg, db] per cell, row-major, size grid*grid.
    Only cells where both images have opaque pixels get a real delta;
    others get [0, 0, 0].
    """
    h = w = 32
    cell_h = h // grid
    cell_w = w // grid
    aa2d = alpha_a.reshape(h, w)
    ab2d = alpha_b.reshape(h, w)
    ca2d = rgb_a.reshape(h, w, 3)
    cb2d = rgb_b.reshape(h, w, 3)
    deltas = []
    for gy in range(grid):
        for gx in range(grid):
            y0, y1 = gy * cell_h, (gy + 1) * cell_h
            x0, x1 = gx * cell_w, (gx + 1) * cell_w
            aa = aa2d[y0:y1, x0:x1]
            ab = ab2d[y0:y1, x0:x1]
            mask = (aa > 0.5) & (ab > 0.5)
            if not mask.any():
                deltas.append([0.0, 0.0, 0.0])
                continue
            ca = ca2d[y0:y1, x0:x1]
            cb = cb2d[y0:y1, x0:x1]
            deltas.append([float(cb[:, :, i][mask].mean() - ca[:, :, i][mask].mean())
                          for i in range(3)])
    return deltas


def changed_pixel_mask(rgb_a, alpha_a, rgb_b, alpha_b, thresh=0.15):
    """Fraction of opaque pixels that changed color beyond thresh."""
    mask = (alpha_a > 0.5) & (alpha_b > 0.5)
    if not mask.any():
        return 0.0
    diff = np.abs(rgb_a - rgb_b).reshape(-1, 3)
    changed = (diff > thresh).any(axis=1)
    return float(changed[mask.ravel()].mean())


def analyze(directory, threshold=0.98, size=32, recursive=False,
            extensions=(".png",)):
    """Analyze images for near-duplicates and recolors."""
    png_files = sorted(iter_images(directory, recursive=recursive,
                                   extensions=extensions))
    if not png_files:
        print("No PNG files found.")
        return [], {}, {}

    data = {}
    hashes = {}
    for path in png_files:
        try:
            data[path] = image_data(path, size=size)
            hashes[path] = pixel_hash(path)
        except Exception as exc:
            print(f"Warning: could not process {path.name}: {exc}")

    paths = sorted(data.keys())
    identical_groups = []
    recolor_map = {}  # base pixel_hash -> {variant_hash: color_info}
    used_identical = set()

    for i, p1 in enumerate(paths):
        if p1 in used_identical:
            continue
        group = {p1}
        for p2 in paths[i + 1:]:
            if p2 in used_identical:
                continue
            rel = classify(data[p1][0], data[p1][1], data[p2][0], data[p2][1],
                           shape_thresh=threshold, color_thresh=threshold)
            if rel == "identical":
                group.add(p2)
                used_identical.add(p2)
        if len(group) > 1:
            identical_groups.append(group)
            used_identical.add(p1)

    # Recolor detection among remaining (non-identical) images
    remaining = [p for p in paths if p not in used_identical]
    used_recolor = set()
    for i, p1 in enumerate(remaining):
        if p1 in used_recolor:
            continue
        variants = []
        for p2 in remaining[i + 1:]:
            if p2 in used_recolor:
                continue
            rel = classify(data[p1][0], data[p1][1], data[p2][0], data[p2][1],
                           shape_thresh=threshold, color_thresh=threshold)
            if rel == "recolor":
                variants.append(p2)
                used_recolor.add(p2)
        if variants:
            base_hash = hashes[p1]
            variant_infos = []
            for v in variants:
                vh = hashes[v]
                rgb_a, alpha_a = data[p1]
                rgb_b, alpha_b = data[v]
                variant_infos.append({
                    "hash": vh,
                    "region_colors_base": region_colors(rgb_a, alpha_a),
                    "region_colors_variant": region_colors(rgb_b, alpha_b),
                    "color_delta_map": color_delta_map(rgb_a, alpha_a, rgb_b, alpha_b),
                    "changed_pixel_fraction": changed_pixel_mask(rgb_a, alpha_a, rgb_b, alpha_b),
                })
            recolor_map[base_hash] = variant_infos
            used_recolor.add(p1)

    return identical_groups, recolor_map, hashes


def cmd_dedup(args):
    domain = resolve_domain(args)
    directory = project_path(getattr(args, "dir", None) or domain.source_dir)
    if not directory.is_dir():
        raise SystemExit(f"Directory not found: {directory}")

    recursive = getattr(args, "recursive", None)
    recursive = domain.recursive if recursive is None else recursive

    def display(path):
        """Show the pack-relative path so duplicate basenames stay distinct."""
        try:
            return str(path.relative_to(directory))
        except ValueError:
            return path.name

    identical_groups, recolor_map, hashes = analyze(
        directory, threshold=args.threshold, size=args.size,
        recursive=recursive, extensions=domain.extensions,
    )

    if not identical_groups and not recolor_map:
        print("No near-duplicates or recolors found.")
        return

    print(f"Analysis (threshold={args.threshold}, recursive={recursive}):\n")

    total_dupes = 0
    for group in identical_groups:
        sorted_files = sorted(group, key=display)
        keeper = sorted_files[0]
        dupes = sorted_files[1:]
        total_dupes += len(dupes)
        print(f"  IDENTICAL KEEP: {display(keeper)}")
        for d in dupes:
            print(f"    IDENTICAL DUPE: {display(d)}")
        print()

    total_recolors = 0
    for base_hash, variant_infos in recolor_map.items():
        # Find the base path for display
        base_path = next((p for p, h in hashes.items() if h == base_hash), None)
        base_name = display(base_path) if base_path else base_hash
        total_recolors += len(variant_infos)
        print(f"  RECOLOR BASE: {base_name} ({base_hash}...)")
        for vi in variant_infos:
            vh = vi["hash"]
            vpath = next((p for p, h in hashes.items() if h == vh), None)
            vname = display(vpath) if vpath else vh
            cf = vi.get("changed_pixel_fraction", 0.0)
            print(f"    RECOLOR VARIANT: {vname} ({vh}...) changed={cf:.2f}")
        print()

    print(f"Total identical dupes: {total_dupes}")
    print(f"Total recolor variants: {total_recolors}")

    if args.apply and identical_groups:
        if not args.yes:
            try:
                confirmed = input(f"Delete {total_dupes} identical duplicate file(s)? (y/N): ")
            except EOFError:
                print("No TTY available. Use --yes to auto-confirm.")
                confirmed = ""
        else:
            confirmed = "y"
        if confirmed.lower() == "y":
            for group in identical_groups:
                sorted_files = sorted(group, key=display)
                for d in sorted_files[1:]:
                    d.unlink()
            print(f"Deleted {total_dupes} file(s).")
        else:
            print("Aborted. No files deleted.")

    if args.recolor_map:
        out_path = project_path(args.recolor_map)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(recolor_map, indent=2) + "\n", encoding="utf-8")
        print(f"Recolor map (hash-based) written to {out_path}")

    meta_setting = getattr(args, "metadata", None) or domain.metadata
    if meta_setting:
        meta_path = project_path(meta_setting)
        meta = load(meta_path)
        for base_hash, variant_infos in recolor_map.items():
            add_recolor_group(meta, base_hash, variant_infos)
        save(meta, meta_path)
        print(f"Recolor groups written to unified metadata: {meta_path}")


def register(subparsers):
    p = subparsers.add_parser(
        "dedup",
        help="find near-duplicates and recolors via fuzzy pixel matching",
    )
    add_domain_argument(p)
    p.add_argument("--dir", default=None,
                   help="directory to scan (default: domain source folder)")
    p.add_argument("--threshold", type=float, default=0.98,
                   help="similarity threshold (0-1, default 0.98)")
    p.add_argument("--size", type=int, default=32,
                   help="resize dimension for comparison (default 32)")
    p.add_argument("--apply", action="store_true",
                   help="delete all but first file in each identical group")
    p.add_argument("--yes", action="store_true",
                   help="skip confirmation prompt when using --apply")
    p.add_argument("--recursive", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="scan subfolders (default: on for spells, off for items)")
    p.add_argument("--recolor-map", default=None,
                   help="write recolor mapping JSON to this path (legacy)")
    p.add_argument("--metadata", default=None,
                   help="unified metadata JSON path (default: domain metadata)")
    p.set_defaults(func=cmd_dedup)
