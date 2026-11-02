"""Render the six v4 styles on one or more backgrounds → output/design_v4/ (for human review).

python scripts/render_design_catalog.py                       # default background
python scripts/render_design_catalog.py --bg a.jpg --bg b.jpg # your own raw images (no text on them)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.design_system import PostDesign, Style, Trip, render_post  # noqa: E402

OUT = Path("output/design_v4")


def designs(bg: str) -> list[PostDesign]:
    return [
        PostDesign(style=Style.OFFER, background=bg, headline="Paris. Longchamp. Five million on Sunday.", tag="Longchamp · 4 Oct", to_iata="CDG"),
        PostDesign(style=Style.EDITORIAL, background=bg, headline="Dubai. The heat breaks. The season opens.", line="Business class from New York · November", big_headline=True),
        PostDesign(style=Style.FACT, background=bg, lead="Thirteen times", follow="the world’s best business class.", line="Skytrax World Airline Awards · 18 Sep 2026"),
        PostDesign(style=Style.PRICE_HERO, background=bg, fare_iatas=["CDG", "LHR", "FCO"], trip=Trip.ONE_WAY, follow="One way to Paris, London or Rome.", line="Business class from New York"),
        PostDesign(style=Style.MULTI, background=bg, headline="Where will you go next?", destinations=["London", "Paris", "Rome", "Dubai", "Tokyo"], fare_iatas=["LHR", "CDG", "FCO", "DXB", "HND"], trip=Trip.ONE_WAY),
        PostDesign(style=Style.MINIMAL, background=bg, headline="Your seat is ready.", line="A specialist, not a booking form.", big_headline=True),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bg", action="append", default=[])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0
    for i, bg in enumerate(a.bg or ["assets/defaults/default_background.jpg"], 1):
        for d in designs(bg):
            res = render_post(d, strict=False)
            path = OUT / f"{i:02d}_{d.style.value}_{res.tone.value}.jpg"
            path.write_bytes(res.jpeg)
            status = "OK" if not res.metrics["issues"] else "QA: " + "; ".join(res.metrics["issues"])
            failures += bool(res.metrics["issues"])
            print(f"{path}  fare={res.fare}  {status}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
