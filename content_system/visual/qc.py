"""
Checks on a candidate photo that need no AI, before the vision check:

  size     the model's image is cropped to 9:16 and scaled to 1080 x 1920. What counts is the width of
           that crop (a landscape image keeps only a slice): under 540 px it's refused, under 1080 it is
           noted (it will look soft). A source that isn't 9:16 is noted.
  blank    an almost uniform image (a failed generation)
  exposure far too dark or far too bright overall
  notes    the strongest straight line across the frame, and how much detail sits in the upper 45%
           (where the logo and the headline go) — read by a person, never a refusal

Two photos joined are left to the vision check: on the approved photos in assets/curated/ a
straight-line detector also fires on every sea horizon and building edge, so it can't decide alone.
None of the approved photos trips a refusal here.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

TARGET = (1080, 1920)
MIN_WIDTH = 540          # the smallest approved photo is 572 px wide; under 1080 is only noted
ASPECT_TOLERANCE = 0.10  # a source more than 10% off 9:16 is noted
TOP_BUSY = 9.0           # mean gradient in the upper 45% above which the headline area is "busy"


@dataclass
class Check:
    ok: bool
    problems: list[str] = field(default_factory=list)
    notes: dict[str, float | str] = field(default_factory=dict)


def fit(data: bytes) -> tuple[Image.Image, tuple[int, int]]:
    """The candidate as the design uses it: 9:16, 1080 x 1920. Returns it and the source size."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        size = im.size
        return ImageOps.fit(im, TARGET, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)), size


def _grey(img: Image.Image, width: int = 270) -> np.ndarray:
    small = img.convert("L").resize((width, int(width * img.height / img.width)), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.float32)


def seam_score(g: np.ndarray) -> tuple[float, float, str]:
    """(jump, ratio, where) for the strongest straight line across the frame, vertical or horizontal."""
    best = (0.0, 0.0, "")
    for axis, name in ((1, "vertical"), (0, "horizontal")):
        jumps = np.abs(np.diff(g, axis=axis))
        line = np.median(jumps, axis=0 if axis == 1 else 1)        # per boundary, over the whole length
        line = line[2:-2]                                          # ignore the very edges
        if line.size == 0:
            continue
        i = int(np.argmax(line))
        typical = float(np.median(line)) + 0.5
        if line[i] / typical > best[1]:
            pos = (i + 2) / (g.shape[1] if axis == 1 else g.shape[0])
            best = (float(line[i]), float(line[i] / typical), f"{name} at {pos:.0%}")
    return best


def crop_width(size: tuple[int, int]) -> int:
    """The width of the 9:16 crop `fit` takes from an image of this size."""
    w, h = size
    return int(min(w, h * TARGET[0] / TARGET[1]))


def inspect(img: Image.Image, source_size: tuple[int, int]) -> Check:
    problems: list[str] = []
    w, h = source_size
    notes: dict[str, float | str] = {"source": f"{w}×{h}"}
    if abs((w / h) / (TARGET[0] / TARGET[1]) - 1) > ASPECT_TOLERANCE:
        notes["aspect"] = f"not 9:16 ({w}×{h}): only a {crop_width(source_size)} px wide slice is kept"
    width = crop_width(source_size)
    if width < MIN_WIDTH:
        problems.append(f"too small: the 9:16 crop of {w}×{h} is {width} px wide (needs at least {MIN_WIDTH})")
    elif width < TARGET[0]:
        notes["size"] = f"upscaled from {width} px wide: it may look soft"
    g = _grey(img)
    spread, mean = float(g.std()), float(g.mean())
    notes |= {"spread": round(spread, 1), "brightness": round(mean, 1)}
    if spread < 12:
        problems.append("almost uniform: not a usable photograph")
    if mean < 22:
        problems.append("far too dark")
    elif mean > 236:
        problems.append("far too bright")
    jump, ratio, where = seam_score(g)
    notes |= {"line": round(jump, 1), "line_ratio": round(ratio, 1), "line_at": where}
    top = g[: int(g.shape[0] * 0.45)]
    detail = float(np.mean(np.abs(np.diff(top, axis=0))) + np.mean(np.abs(np.diff(top, axis=1))))
    notes["top_detail"] = round(detail, 1)
    if detail > TOP_BUSY:
        notes["top"] = "busy: the headline may be hard to read"
    return Check(ok=not problems, problems=problems, notes=notes)


def jpeg(img: Image.Image, quality: int = 92) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()
