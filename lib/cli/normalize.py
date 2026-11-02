"""normalize — standardize sprites to a 32x32 transparent canvas.

* crop to the non-transparent bounding box, center into a square canvas
  (shrunk to fit when larger, never enlarged)
* drop exact-duplicate frames (keep the lowest index)
* rename sequentially: sprite_0001.png ...
* write manifest.json + contact_sheet.png
"""
import argparse
import hashlib
import json
import math

import numpy as np
from PIL import Image

from lib.util.paths import project_path, iter_pngs

FRAME = 32


def frame_sprite(img_rgba, size=FRAME):
    """Crop to non-transparent bbox, then center into size x size canvas."""
    arr = np.asarray(img_rgba)
    mask = arr[:, :, 3] > 16
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    crop = img_rgba.crop((x0, y0, x1 + 1, y1 + 1))
    w, h = crop.size
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if max(w, h) > size:
        scale = size / max(w, h)
        nw = max(1, round(w * scale))
        nh = max(1, round(h * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        w, h = crop.size
    ox = (size - w) // 2
    oy = (size - h) // 2
    out.paste(crop, (ox, oy), crop)
    return out


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(args):
    sprites_dir = project_path(args.sprites)
    if not sprites_dir.is_dir():
        raise SystemExit(f"sprites dir not found: {sprites_dir}")

    files = iter_pngs(sprites_dir, recursive=False)
    print(f"Found {len(files)} PNG files in {sprites_dir}")

    # De-duplicate by content hash, keep lowest index first.
    seen = {}
    unique = []
    dup_count = 0
    for path in files:
        d = digest(path)
        if d in seen:
            dup_count += 1
            continue
        seen[d] = path
        unique.append(path)

    if dup_count:
        print(f"Dropping {dup_count} exact-duplicate file(s)")

    out_dir = sprites_dir  # normalize in place
    tmp_dir = sprites_dir / ".normalize_tmp"
    tmp_dir.mkdir(exist_ok=True)

    manifest = []
    n_centered = n_shrunk = 0
    for new_index, src in enumerate(unique, start=1):
        with Image.open(src) as img:
            img = img.convert("RGBA")
            w, h = img.size
            framed = frame_sprite(img, size=args.size)
            if max(w, h) > args.size:
                n_shrunk += 1
            else:
                n_centered += 1
            fname = f"sprite_{new_index:04d}.png"
            out_path = tmp_dir / fname
            framed.save(out_path)
            manifest.append({
                "file": fname,
                "new_index": new_index,
                "source": src.name,
                "src_w": w,
                "src_h": h,
            })

    if not args.dry_run:
        # Replace originals with normalized versions.
        for old in files:
            old.unlink()
        for tmp in tmp_dir.iterdir():
            tmp.replace(sprites_dir / tmp.name)
        tmp_dir.rmdir()
        with open(sprites_dir / "manifest.json", "w") as f:
            json.dump({"frame": args.size, "count": len(manifest), "sprites": manifest}, f, indent=2)

        # Contact sheet.
        cols = 20
        rows = math.ceil(len(manifest) / cols)
        sheet = Image.new("RGBA", (cols * args.size, rows * args.size), (0, 0, 0, 0))
        for idx, m in enumerate(manifest):
            im = Image.open(sprites_dir / m["file"]).convert("RGBA")
            r, c = divmod(idx, cols)
            sheet.paste(im, (c * args.size, r * args.size))
        sheet.save(sprites_dir / "contact_sheet.png")

    print(f"centered (<={args.size}): {n_centered}")
    print(f"shrunk   (>{args.size}): {n_shrunk}")
    print(f"normalized {len(manifest)} sprite(s)")
    if args.dry_run:
        print("dry-run: no files were changed")


def register(subparsers):
    p = subparsers.add_parser(
        "normalize",
        help="standardize sprites to a 32x32 transparent canvas, dedupe, rename",
    )
    p.add_argument("--sprites", default="in/sprites", help="sprite directory")
    p.add_argument("--size", type=int, default=FRAME, help="canvas size in px")
    p.add_argument("--dry-run", action="store_true", help="report only, do not modify files")
    p.set_defaults(func=normalize)
