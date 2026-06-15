"""Tests for BBC Veo video prompt library."""
from prompts.video_prompts import (
    NEGATIVE_PROMPT_VIDEO,
    get_all_categories,
    get_video_prompt,
    resolve_video_prompt,
)


class TestVideoPrompts:
    def test_destination_london(self):
        p = get_video_prompt("destination", "london")
        assert "London" in p or "Thames" in p
        assert "pan" in p.lower()

    def test_event_f1_monaco(self):
        p = get_video_prompt("event", "f1_monaco")
        assert "Monaco" in p or "Formula 1" in p

    def test_unknown_subcategory_falls_back(self):
        p = get_video_prompt("destination", "unknown_city")
        assert len(p) > 50

    def test_custom_append(self):
        p = get_video_prompt("destination", "london", custom="extra mist on river")
        assert "Additionally" in p
        assert "mist" in p

    def test_get_all_categories(self):
        cats = get_all_categories()
        assert "destination" in cats
        assert "london" in cats["destination"]
        assert "f1_monaco" in cats["event"]

    def test_resolve_custom_prompt(self):
        p = resolve_video_prompt(custom_prompt="Slow pan over ocean at dawn")
        assert p == "Slow pan over ocean at dawn"

    def test_resolve_category(self):
        p = resolve_video_prompt(category="lifestyle", subcategory="cabin_lieflat")
        assert "cabin" in p.lower() or "lie-flat" in p.lower()

    def test_negative_prompt_has_watermark_guard(self):
        assert "watermark" in NEGATIVE_PROMPT_VIDEO.lower()
