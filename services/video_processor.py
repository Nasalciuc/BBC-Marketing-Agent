"""
Video processor — FFmpeg operations for BBC pipeline.
Extract frames, trim clips, add simple text overlay.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("bbc.video_proc")


def _get_ffmpeg() -> str:
    """Get FFmpeg binary — system PATH first, imageio fallback."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def extract_frames(
    video_path: str,
    output_dir: str,
    interval_sec: int = 3,
    max_frames: int = 10,
    width: int = 1280,
    height: int = 720,
) -> list[str]:
    """
    Extract JPEG frames at regular intervals.
    Returns list of frame file paths sorted chronologically.
    """
    ffmpeg = _get_ffmpeg()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    vf = (
        f"fps=1/{interval_sec},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-vf",
        vf,
        "-frames:v",
        str(max_frames),
        "-q:v",
        "2",
        str(Path(output_dir) / "frame_%03d.jpg"),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        frames = sorted(str(f) for f in Path(output_dir).glob("frame_*.jpg"))
        log.info("Extracted %d frames from %s", len(frames), Path(video_path).name)
        return frames
    except Exception as exc:
        log.error("Extract frames error: %s", exc)
        return []


def select_best_frame(frames: list[str]) -> str | None:
    """Pick the best frame — middle of the list (usually most interesting)."""
    if not frames:
        return None
    return frames[len(frames) // 2]


def trim_video(
    video_path: str,
    output_path: str,
    start: float = 0,
    duration: float = 10,
) -> str | None:
    """Trim video to a specific segment. Returns output path or None."""
    ffmpeg = _get_ffmpeg()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(start),
        "-i",
        video_path,
        "-t",
        str(duration),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        p = Path(output_path)
        if p.exists() and p.stat().st_size > 5000:
            log.info("Trimmed: %s (%dKB)", p.name, p.stat().st_size // 1024)
            return output_path
        return None
    except Exception as exc:
        log.error("Trim error: %s", exc)
        return None


def brand_video(
    video_path: str,
    output_path: str,
    headline: str = "",
    footer: str = "buybusinessclass.com",
) -> str | None:
    """
    Overlay headline + footer text on video.
    Uses FFmpeg drawtext (no external fonts needed).
    """
    ffmpeg = _get_ffmpeg()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    def esc(text: str) -> str:
        return text.replace("'", "").replace(":", " ").replace("\\", "")

    parts: list[str] = []
    if headline:
        parts.append(
            f"drawtext=text='{esc(headline)}':"
            f"fontsize=30:fontcolor=white:borderw=2:bordercolor=black:"
            f"x=30:y=h-100"
        )
    parts.append(
        f"drawtext=text='{esc(footer)}':"
        f"fontsize=20:fontcolor=white:borderw=1:bordercolor=black:"
        f"x=30:y=h-40"
    )

    vf = ",".join(parts)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        video_path,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        p = Path(output_path)
        if p.exists() and p.stat().st_size > 5000:
            log.info("Branded video: %s (%dKB)", p.name, p.stat().st_size // 1024)
            return output_path
        log.warning("Brand video produced empty/small file")
        return None
    except Exception as exc:
        log.error("Brand video error: %s", exc)
        return None
