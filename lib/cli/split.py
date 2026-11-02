"""split — split multi-frame sprite sheets into individual frame PNGs.

Default mode uses connected-component analysis (flood fill) to find distinct
opaque regions. Optional `--grid` mode splits into a regular grid of
canvas-sized cells.
"""
import argparse
from collections import deque

import numpy as np
from PIL import Image

from lib.util.paths import project_path

CANVAS = 32


def connected_components(alpha):
    """Find connected components of opaque pixels using 4-connectivity flood fill.

    Returns list of (ys, xs) coordinate lists.
    """
    h, w = alpha.shape
    visited = np.zeros((h, w), dtype=bool)
    components = []

    for y in range(h):
        for x in range(w):
            if not alpha[y, x] or visited[y, x]:
                continue
            comp_ys = []
            comp_xs = []
            queue = deque()
            queue.append((y, x))
            visited[y, x] = True
            while queue:
                cy, cx = queue.popleft()
                comp_ys.append(cy)
                comp_xs.append(cx)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and alpha[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            components.append((comp_ys, comp_xs))

    return components


def is_shadow_strip(ys, xs, all_comps, h, w):
    """Heuristic: a thin horizontal strip directly below a larger component
    with overlapping x-range is likely a shadow/reflection."""
    y0, y1 = min(ys), max(ys)
    x0, x1 = min(xs), max(xs)
    height = y1 - y0 + 1
    width = x1 - x0 + 1
    if height > 15 or width < 15:
        return False
    # Look for a larger component directly above with overlapping x-range
    for oys, oxs in all_comps:
        oy0, oy1 = min(oys), max(oys)
        ox0, ox1 = min(oxs), max(oxs)
        if oy1 < y0 - 2:  # above with small gap
            ox_overlap = max(0, min(x1, ox1) - max(x0, ox0) + 1)
            if ox_overlap > width * 0.5 and len(oys) > height * 3:
                return True
    return False


def split_connected(png_path, canvas=CANVAS, min_area=200, no_shadows=False):
    """Split using connected components."""
    with Image.open(png_path) as im:
        im = im.convert("RGBA")
        arr = np.asarray(im)
    alpha = arr[:, :, 3] > 0
    h, w = alpha.shape

    components = connected_components(alpha)

    frames = []
    for comp_ys, comp_xs in components:
        if len(comp_ys) < min_area:
            continue
        if no_shadows and is_shadow_strip(comp_ys, comp_xs, components, h, w):
            continue
        y0, y1 = min(comp_ys), max(comp_ys)
        x0, x1 = min(comp_xs), max(comp_xs)
        crop = im.crop((x0, y0, x1 + 1, y1 + 1))
        cw, ch = crop.size
        out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ox = max(0, (canvas - cw) // 2)
        oy = max(0, (canvas - ch) // 2)
        out.paste(crop, (ox, oy), crop)
        frames.append((out, (x0, y0, x1 + 1, y1 + 1)))

    return frames


def split_grid(png_path, canvas=CANVAS, min_area=200):
    """Split into a regular grid of canvas-sized cells."""
    with Image.open(png_path) as im:
        im = im.convert("RGBA")
        arr = np.asarray(im)
    alpha = arr[:, :, 3] > 0
    h, w = alpha.shape

    cols = w // canvas
    rows = h // canvas
    frames = []

    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * canvas, r * canvas
            cell_alpha = alpha[y0:y0 + canvas, x0:x0 + canvas]
            if not cell_alpha.any():
                continue
            # Count opaque pixels in cell
            if cell_alpha.sum() < min_area:
                continue
            crop = im.crop((x0, y0, x0 + canvas, y0 + canvas))
            frames.append((crop, (x0, y0, x0 + canvas, y0 + canvas)))

    return frames


def cmd_split(args):
    src_dir = project_path(args.dir)
    if not src_dir.is_dir():
        raise SystemExit(f"Directory not found: {src_dir}")

    out_dir = project_path(args.output) if args.output else \
        project_path(str(src_dir) + "_split")

    png_files = sorted(p for p in src_dir.iterdir()
                       if p.is_file() and p.suffix.lower() == ".png")
    if not png_files:
        print(f"No PNG files found in {src_dir}")
        return

    if args.dry_run:
        mode = "grid" if args.grid else "connected-components"
        print(f"Dry run: {mode} split of {len(png_files)} sheet(s) into {out_dir}")

    total = 0
    for png_path in png_files:
        if args.grid:
            frames = split_grid(png_path, canvas=args.canvas, min_area=args.min_area)
        else:
            frames = split_connected(png_path, canvas=args.canvas,
                                     min_area=args.min_area,
                                     no_shadows=args.no_shadows)
        if not frames:
            print(f"  {png_path.name}: no frames")
            continue
        if not args.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        for i, (img_on_canvas, bbox) in enumerate(frames):
            name = f"{png_path.stem}_frame{i}.png"
            if args.dry_run:
                print(f"  {png_path.name} -> {name}  canvas={args.canvas}x{args.canvas}  bbox={bbox}")
            else:
                img_on_canvas.save(out_dir / name)
        total += len(frames)
        print(f"  {png_path.name}: {len(frames)} frame(s)")

    if not args.dry_run:
        print(f"\nWrote {total} frame(s) to {out_dir}")
    else:
        print(f"\nDry run complete. {total} frame(s) would be written.")


def register(subparsers):
    p = subparsers.add_parser(
        "split",
        help="split sprite sheets into individual frame PNGs",
    )
    p.add_argument("--dir", default="dcss/scythe",
                  help="directory containing sprite sheet PNGs")
    p.add_argument("--output", default=None,
                  help="output directory (default: <dir>_split)")
    p.add_argument("--canvas", type=int, default=CANVAS,
                  help=f"canvas size in pixels (default: {CANVAS})")
    p.add_argument("--min-area", type=int, default=200,
                  help="minimum frame pixel count (default: 200)")
    p.add_argument("--grid", action="store_true",
                  help="split into a regular grid of canvas-sized cells "
                       "instead of connected components")
    p.add_argument("--no-shadows", action="store_true",
                  help="exclude thin horizontal shadow strips below "
                       "larger sprites (connected-components mode only)")
    p.add_argument("--dry-run", action="store_true",
                  help="print what would be split without writing files")
    p.set_defaults(func=cmd_split)
