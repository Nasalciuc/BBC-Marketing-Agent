"""Tests for YouTube client and video processor."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.video_processor import select_best_frame
from services.youtube_client import _get_ytdlp, search_youtube


def test_get_ytdlp_returns_string():
    assert isinstance(_get_ytdlp(), str)


def test_select_best_frame_middle():
    frames = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
    assert select_best_frame(frames) == "c.jpg"


def test_select_best_frame_empty():
    assert select_best_frame([]) is None


def test_search_youtube_parses_json_lines():
    fake_json = (
        '{"title":"Test Video","webpage_url":"https://youtube.com/watch?v=abc123",'
        '"id":"abc123","duration":120,"uploader":"BBC","view_count":1000}'
    )
    mock_result = MagicMock(stdout=fake_json + "\n", returncode=0)

    with patch("services.youtube_client.subprocess.run", return_value=mock_result):
        videos = search_youtube("monaco f1", max_results=1)

    assert len(videos) == 1
    assert videos[0]["title"] == "Test Video"
    assert videos[0]["id"] == "abc123"


def test_select_best_frame_with_claude_no_client():
    from services.video_processor import select_best_frame_with_claude

    frames = ["a.jpg", "b.jpg", "c.jpg"]
    assert select_best_frame_with_claude(frames, "Test Event", anthropic_client=None) == "b.jpg"


def test_extract_frames_skip_start_param():
    import inspect

    from services.video_processor import extract_frames

    sig = inspect.signature(extract_frames)
    assert "skip_start" in sig.parameters
    assert sig.parameters["skip_start"].default == 5.0


def test_extract_frames_finds_existing_jpegs(tmp_path: Path):
    from services.video_processor import extract_frames

    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00" * 10000)
    out_dir = tmp_path / "frames"
    out_dir.mkdir()
    (out_dir / "frame_001.jpg").write_bytes(b"jpeg")

    with patch("services.video_processor.subprocess.run"):
        frames = extract_frames(str(video), str(out_dir))

    assert frames == [str(out_dir / "frame_001.jpg")]
