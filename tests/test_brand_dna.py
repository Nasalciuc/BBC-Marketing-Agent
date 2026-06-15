"""Tests for BBC Brand DNA prompts."""
from prompts.brand_dna import (
    BBC_BRAND_DNA,
    BBC_CONTENT_SELECTION_CONTEXT,
    BBC_YOUTUBE_SEARCH_CONTEXT,
)


def test_brand_dna_loaded():
    assert len(BBC_BRAND_DNA) > 1000
    assert "BuyBusinessClass.com" in BBC_BRAND_DNA
    assert "NEVER SHOW" in BBC_BRAND_DNA or "WE NEVER SHOW" in BBC_BRAND_DNA


def test_youtube_context_luxury_focus():
    assert "LUXURY" in BBC_YOUTUBE_SEARCH_CONTEXT
    assert "crash" in BBC_YOUTUBE_SEARCH_CONTEXT.lower()


def test_content_selection_filter():
    assert "ASPIRATIONAL" in BBC_CONTENT_SELECTION_CONTEXT
    assert "Accidents" in BBC_CONTENT_SELECTION_CONTEXT
