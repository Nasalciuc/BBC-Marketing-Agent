"""Teste pentru branding engine."""
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from services.branding_engine import generate_branded_image

TEST_BG = "assets/defaults/default_background.jpg"
HAS_TEST_BG = Path(TEST_BG).exists()


@pytest.mark.skipif(not HAS_TEST_BG, reason="No test background image")
class TestBrandingEngine:
    def test_generates_bytes(self):
        result = generate_branded_image(
            event_name="Test Event",
            route="JFK → LHR",
            price="$2,033",
            background_url_or_path=TEST_BG,
        )
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_correct_dimensions_landscape(self):
        result = generate_branded_image(
            event_name="Test Event",
            route="JFK → LHR",
            price="$2,033",
            background_url_or_path=TEST_BG,
        )
        img = Image.open(BytesIO(result))
        assert img.size == (1200, 628)

    def test_jpeg_format(self):
        result = generate_branded_image(
            event_name="Test Event",
            route="JFK → LHR",
            price="$2,033",
            background_url_or_path=TEST_BG,
        )
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_file_size_reasonable(self):
        result = generate_branded_image(
            event_name="Test Event",
            route="JFK → LHR",
            price="$2,033",
            background_url_or_path=TEST_BG,
        )
        assert len(result) < 500_000

    def test_different_routes_produce_different_images(self):
        img1 = generate_branded_image("Event A", "JFK → LHR", "$2,033", TEST_BG)
        img2 = generate_branded_image("Event B", "JFK → NRT", "$2,399", TEST_BG)
        assert img1 != img2

    def test_long_event_name(self):
        result = generate_branded_image(
            event_name="Formula 1 Australian Grand Prix Melbourne 2026",
            route="JFK → MEL",
            price="$3,200",
            background_url_or_path=TEST_BG,
        )
        assert isinstance(result, bytes)

    def test_custom_badge_text(self):
        result = generate_branded_image(
            event_name="F1 Monaco",
            route="JFK → NCE",
            price="$2,069",
            background_url_or_path=TEST_BG,
            badge_text="LIMITED OFFER",
        )
        assert isinstance(result, bytes)
