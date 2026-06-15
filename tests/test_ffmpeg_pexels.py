"""Tests for FFmpeg and Pexels config helpers."""
from services.ffmpeg_utils import ffmpeg_version, get_ffmpeg_path


def test_ffmpeg_available():
    path = get_ffmpeg_path()
    assert path is not None
    assert ffmpeg_version() is not None


def test_pexels_config_field():
    from config import settings

    assert hasattr(settings, "pexels_api_key")
