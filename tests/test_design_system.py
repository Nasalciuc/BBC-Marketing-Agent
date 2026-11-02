"""Design system v4 — models, tone, copy rules, and rendered layout (Playwright)."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from services.design_system import (
    CONTENT_TO_STYLE,
    PostDesign,
    Style,
    Tone,
    Trip,
    caption_ending,
    fare_for,
    pick_tone,
    render_post,
    validate_week_mix,
)

BG = "assets/defaults/default_background.jpg"

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    HAS_PW = True
except ImportError:
    HAS_PW = False


def _img(fill, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    a = np.full((1920, 1080, 3), fill, dtype=float) + rng.normal(0, noise, (1920, 1080, 3))
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"))


class TestTone:
    def test_bright_calm_sky_is_light_even_without_card(self):
        assert pick_tone(_img(235, noise=3), has_card=False)[0] == Tone.LIGHT

    def test_dark_image_is_dark(self):
        assert pick_tone(_img(30, noise=5), has_card=True)[0] == Tone.DARK

    def test_medium_busy_image_light_only_with_card(self):
        img = _img(165, noise=55)
        assert pick_tone(img, has_card=True)[0] == Tone.LIGHT
        assert pick_tone(img, has_card=False)[0] == Tone.DARK


class TestModel:
    def test_offer_requires_route_fields(self):
        with pytest.raises(ValidationError):
            PostDesign(style=Style.OFFER, background=BG, headline="Paris.")

    def test_no_fare_styles_reject_iata(self):
        with pytest.raises(ValidationError):
            PostDesign(style=Style.EDITORIAL, background=BG, headline="Dubai.", line="x", to_iata="DXB")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            PostDesign(style=Style.MINIMAL, background=BG, headline="x", line="y", price="$1")

    def test_every_content_type_has_a_style(self):
        for t in ("event", "cabin", "deal", "multi", "destination", "hotel", "lounge", "news", "award", "brand", "lifestyle"):
            assert t in CONTENT_TO_STYLE


class TestCopyRules:
    def test_caption_ending_priced_vs_not(self):
        assert caption_ending(Style.OFFER).startswith("The fare above")
        assert caption_ending(Style.EDITORIAL, "Dubai") == "Ask us for today's fare to Dubai."
        assert "$" not in caption_ending(Style.PRICE_HERO)

    def test_week_mix(self):
        ok = [Style.OFFER, Style.FACT, Style.OFFER, Style.PRICE_HERO, Style.EDITORIAL]
        assert validate_week_mix(ok) == []
        bad = [Style.OFFER, Style.PRICE_HERO, Style.MULTI, Style.OFFER, Style.EDITORIAL]
        issues = validate_week_mix(bad)
        assert any("in a row" in i for i in issues) and any("max 60%" in i for i in issues)
        assert any("max 2" in i for i in validate_week_mix([Style.FACT] * 3))

    def test_fare_comes_from_engine(self):
        assert fare_for(["CDG", "LHR", "FCO"], Trip.ONE_WAY) == min(fare_for([i], Trip.ONE_WAY) for i in ("CDG", "LHR", "FCO"))


CATALOG = [
    PostDesign(style=Style.OFFER, background=BG, headline="Paris. Longchamp. Five million on Sunday.", tag="Longchamp · 4 Oct", to_iata="CDG"),
    PostDesign(style=Style.EDITORIAL, background=BG, headline="Dubai. The heat breaks. The season opens.", line="Business class from New York · November", big_headline=True),
    PostDesign(style=Style.FACT, background=BG, lead="Thirteen times", follow="the world’s best business class.", line="Skytrax World Airline Awards · 18 Sep 2026"),
    PostDesign(style=Style.PRICE_HERO, background=BG, fare_iatas=["CDG", "LHR", "FCO"], trip=Trip.ONE_WAY, follow="One way to Paris, London or Rome.", line="Business class from New York"),
    PostDesign(style=Style.MULTI, background=BG, headline="Where will you go next?", destinations=["London", "Paris", "Rome", "Dubai", "Tokyo"], fare_iatas=["LHR", "CDG", "FCO", "DXB", "HND"], trip=Trip.ONE_WAY),
    PostDesign(style=Style.MINIMAL, background=BG, headline="Your seat is ready.", line="A specialist, not a booking form.", big_headline=True),
]


@pytest.mark.skipif(not (HAS_PW and Path(BG).exists()), reason="Playwright/Chromium or default background missing")
@pytest.mark.parametrize("design", CATALOG, ids=[d.style.value for d in CATALOG])
def test_render_passes_layout_qa(design, tmp_path):
    res = render_post(design, strict=True)             # raises DesignQAError on any layout violation
    img = Image.open(__import__("io").BytesIO(res.jpeg))
    assert img.size == (1080, 1920)
    assert res.metrics["textBottom"] <= 0.45 * 1920
    (tmp_path / f"{design.style}.jpg").write_bytes(res.jpeg)
