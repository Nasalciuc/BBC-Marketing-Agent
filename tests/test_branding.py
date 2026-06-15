"""Teste pentru branding engine — Playwright HTML templates."""
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from services.branding_engine import (
    _detect_trip_type,
    _extract_trip_label,
    _format_price_amount,
    _get_subtitle,
    _is_monetary_price,
    _normalize_price,
    _resolve_headline,
    generate_branded_image,
)

TEST_BG = "assets/defaults/default_background.jpg"
HAS_BG = Path(TEST_BG).exists()
SKIP_REASON = "Playwright not installed or no test background"


class TestNormalizePrice:
    def test_shell_comma_artifact(self):
        assert _normalize_price(",069") == "$2,069"
        assert _normalize_price("\\,069") == "$2,069"

    def test_standard_formats(self):
        assert _normalize_price("$2,069") == "$2,069"
        assert _normalize_price("2,033") == "$2,033"


class TestDetectTripType:
    def test_one_way_variants(self):
        assert _detect_trip_type("from $1,511 One-Way") == "One-Way"
        assert _detect_trip_type("$1,511 one way") == "One-Way"
        assert _detect_trip_type("oneway $999") == "One-Way"

    def test_round_trip_default(self):
        assert _detect_trip_type("from $2,033 Round-Trip") == "Round-Trip"
        assert _detect_trip_type("$2,033") == "Round-Trip"
        assert _detect_trip_type("from $2,033") == "Round-Trip"


class TestPriceAndSubtitle:
    def test_format_price_amount_one_way(self):
        assert _format_price_amount("from $1,511 One-Way") == "from $1,511"
        assert _extract_trip_label("from $1,511 One-Way") == "one-way"

    def test_format_price_amount_round_trip(self):
        assert _format_price_amount("$2,069 round-trip") == "from $2,069"
        assert _extract_trip_label("$2,069 round-trip") == "round-trip"

    def test_format_price_amount_no_trip_type(self):
        assert _format_price_amount("$2,033") == "from $2,033"
        assert _extract_trip_label("$2,033") == ""

    def test_subtitle(self):
        assert _get_subtitle("London") == "Business Class to London"
        assert _get_subtitle("London", "") == ""

    def test_is_monetary_price(self):
        assert _is_monetary_price("$1,511 one-way") is True
        assert _is_monetary_price("Lie-flat seats · Lounge access") is False
        assert _is_monetary_price("") is False

    def test_resolve_headline_emotional(self):
        assert _resolve_headline(None, "London is calling", "London") == "London is calling"

    def test_resolve_headline_hides_iata_route(self):
        assert _resolve_headline(None, "JFK → London", "London") == "London"


@pytest.mark.skipif(not HAS_PLAYWRIGHT or not HAS_BG, reason=SKIP_REASON)
class TestBrandingEngine:
    def test_generates_bytes(self):
        result = generate_branded_image("Test Event", "JFK → LHR", "$2,033", TEST_BG)
        assert isinstance(result, bytes)
        assert len(result) > 10_000

    def test_correct_dimensions(self):
        result = generate_branded_image("Test Event", "JFK → LHR", "$2,033", TEST_BG)
        img = Image.open(BytesIO(result))
        assert img.size == (1200, 628)

    def test_jpeg_format(self):
        result = generate_branded_image("Test Event", "JFK → LHR", "$2,033", TEST_BG)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"

    def test_file_size_reasonable(self):
        result = generate_branded_image("Test Event", "JFK → LHR", "$2,033", TEST_BG)
        assert len(result) < 800_000

    def test_different_routes_different_images(self):
        img1 = generate_branded_image("Event A", "JFK → LHR", "$2,033", TEST_BG)
        img2 = generate_branded_image("Event B", "JFK → NRT", "$2,399", TEST_BG)
        assert img1 != img2

    def test_long_event_name(self):
        result = generate_branded_image(
            "Formula 1 Australian Grand Prix Melbourne 2026",
            "LAX → MEL",
            "$3,200",
            TEST_BG,
        )
        assert isinstance(result, bytes)

    def test_arrow_replacement(self):
        result = generate_branded_image("Test", "JFK -> NCE", "$2,069", TEST_BG)
        assert isinstance(result, bytes)

    def test_custom_badge(self):
        result = generate_branded_image(
            "F1 Monaco",
            "JFK → NCE",
            "$2,069",
            TEST_BG,
            badge_text="LIMITED OFFER",
        )
        assert isinstance(result, bytes)

    def test_with_caption(self):
        result = generate_branded_image(
            "F1 Monaco",
            "JFK → NCE",
            "$2,069",
            TEST_BG,
            caption="Fly business class to the glamour capital",
        )
        assert isinstance(result, bytes)

    def test_price_without_dollar_sign(self):
        """Preț fără $ prefix se normalizează."""
        result = generate_branded_image("Test", "JFK → LHR", "2,033", TEST_BG)
        assert isinstance(result, bytes)
        assert len(result) > 10_000
