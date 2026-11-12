"""Build the two logo variants used by design system v4 from the master logo.

Input : assets/logos/LOGO_BBC_1200X300.png (black mark on white)
Output: assets/logos/bbc_logo_white.png  (for DARK tone)
        assets/logos/bbc_logo_navy.png   (for LIGHT tone)
Ink becomes opacity (anti-aliased edges preserved), white background becomes transparent,
then the image is cropped to the visible mark.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "logos" / "LOGO_BBC_1200X300.png"
OUT = {"bbc_logo_white.png": (255, 255, 255), "bbc_logo_navy.png": (11, 24, 41)}


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}")
        return 1
    rgba = np.asarray(Image.open(SRC).convert("RGBA")).astype(float)
    lum = 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
    alpha = np.clip((255 - lum) * (rgba[..., 3] / 255.0), 0, 255)
    ys, xs = np.where(alpha > 20)
    box = (xs.min() - 4, ys.min() - 4, xs.max() + 5, ys.max() + 5)
    for name, rgb in OUT.items():
        out = np.zeros_like(rgba)
        out[..., 0], out[..., 1], out[..., 2] = rgb
        out[..., 3] = alpha
        Image.fromarray(out.astype("uint8"), "RGBA").crop(box).save(SRC.parent / name)
        print("wrote", SRC.parent / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
