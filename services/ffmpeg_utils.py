"""FFmpeg path resolution — system binary or imageio-ffmpeg fallback."""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("bbc.ffmpeg")

_ffmpeg_path: str | None = None


def get_ffmpeg_path() -> str | None:
    """Return path to ffmpeg executable, or None if unavailable."""
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path or None

    system = shutil.which("ffmpeg")
    if system:
        _ffmpeg_path = system
        return system

    try:
        import imageio_ffmpeg

        _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        return _ffmpeg_path
    except Exception as exc:
        log.debug("imageio-ffmpeg unavailable: %s", exc)
        _ffmpeg_path = ""
        return None


def ffmpeg_version() -> str | None:
    """Return first line of ffmpeg -version, or None."""
    path = get_ffmpeg_path()
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.splitlines()[0] if result.stdout else None
    except Exception:
        return None


def run_ffmpeg(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run ffmpeg with resolved binary. args exclude the ffmpeg executable itself."""
    path = get_ffmpeg_path()
    if not path:
        raise RuntimeError("FFmpeg not found — install ffmpeg or pip install imageio-ffmpeg")
    return subprocess.run(
        [path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
