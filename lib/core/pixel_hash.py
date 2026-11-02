"""Perceptual hashing for sprite images.

8x8 grayscale average-hash → 64-bit integer. Used as the stable
cross-reference key throughout the project (renames/moves never break links).
"""
import numpy as np
from PIL import Image
from pathlib import Path


def pixel_hash(path, size=8):
    """Return a 64-bit average-hash of the image at *path*."""
    with Image.open(path) as im:
        im = im.convert("L").resize((size, size), Image.Resampling.NEAREST)
        pixels = np.asarray(im, dtype=np.uint8)
    # average-hash: 1 where pixel >= mean, 0 otherwise.
    # Pack MSB-first into a 64-bit integer, then render as a 16-char hex
    # string so it matches the canonical key format used in the metadata JSON.
    mean = pixels.mean()
    bits = pixels > mean
    h = 0
    for bit in bits.flatten():
        h = (h << 1) | int(bit)
    return format(h, "016x")


def hamming_distance(h1, h2):
    """Number of differing bits between two hash strings (hex) or ints."""
    b1 = int(h1, 16) if isinstance(h1, str) else h1
    b2 = int(h2, 16) if isinstance(h2, str) else h2
    return bin(b1 ^ b2).count("1")


def hash_image_file(path, size=8):
    """Alias used by some callers; returns the integer hash."""
    return pixel_hash(path, size=size)
