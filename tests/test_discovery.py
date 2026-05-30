"""Tests for Gemini discovery client."""
import json

import pytest

pytest.importorskip("google.genai")


class TestDiscoveryParsing:
    """Tests that do not require a Gemini API key."""

    def test_parse_valid_events_json(self):
        from services.gemini_client import DISCOVERY_PROMPT

        assert "JSON" in DISCOVERY_PROMPT
        assert "events" in DISCOVERY_PROMPT

    def test_discovery_prompt_has_categories(self):
        from services.gemini_client import DISCOVERY_PROMPT

        assert "Formula 1" in DISCOVERY_PROMPT
        assert "Grand Slam" in DISCOVERY_PROMPT
        assert "Fashion Week" in DISCOVERY_PROMPT

    def test_discovery_prompt_requires_na_routes(self):
        from services.gemini_client import DISCOVERY_PROMPT

        assert "JFK" in DISCOVERY_PROMPT
        assert "North America" in DISCOVERY_PROMPT or "NA" in DISCOVERY_PROMPT

    def test_image_gen_prompt_no_text(self):
        from services.gemini_client import IMAGE_GEN_SYSTEM

        assert "NEVER" in IMAGE_GEN_SYSTEM
        assert "text" in IMAGE_GEN_SYSTEM.lower()

    def test_verification_prompt_structure(self):
        from services.gemini_client import VERIFICATION_PROMPT

        assert "DA" in VERIFICATION_PROMPT or "NU" in VERIFICATION_PROMPT

    def test_parse_events_json_helper(self):
        from services.gemini_client import _parse_events_json

        raw = json.dumps({"events": [{"name": "Monaco GP", "premium_score": 9.5}]})
        events = _parse_events_json(raw)
        assert len(events) == 1
        assert events[0]["name"] == "Monaco GP"


class TestDiscoveryIntegration:
    """Integration tests — require GEMINI_API_KEY."""

    @pytest.fixture
    def has_api_key(self):
        from config import settings

        if not settings.gemini_api_key:
            pytest.skip("GEMINI_API_KEY not set")

    @pytest.mark.asyncio
    async def test_discover_returns_list(self, has_api_key):
        from services.gemini_client import discover_events

        events = await discover_events(week_offset=1)
        assert isinstance(events, list)

    @pytest.mark.asyncio
    async def test_discover_events_have_required_fields(self, has_api_key):
        from services.gemini_client import discover_events

        events = await discover_events(week_offset=1)
        if events:
            event = events[0]
            assert "name" in event
            assert "city" in event
            assert "routes" in event
            assert "premium_score" in event
