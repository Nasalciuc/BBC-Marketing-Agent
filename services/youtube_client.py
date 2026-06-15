"""
YouTube client — search + download via yt-dlp subprocess.
Used by real footage pipeline to get event material from YouTube.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("bbc.youtube")


def _get_ytdlp() -> str:
    """Find yt-dlp binary in PATH."""
    for name in ("yt-dlp", "yt_dlp"):
        path = shutil.which(name)
        if path:
            return path
    return "yt-dlp"


def search_youtube(query: str, max_results: int = 3) -> list[dict]:
    """
    Search YouTube for videos.
    Returns: [{"title": str, "url": str, "id": str, "duration": int, "uploader": str}]
    """
    ytdlp = _get_ytdlp()
    cmd = [
        ytdlp,
        f"ytsearch{max_results}:{query}",
        "--dump-json",
        "--no-download",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
        videos: list[dict] = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                videos.append(
                    {
                        "title": data.get("title", ""),
                        "url": data.get("webpage_url")
                        or f"https://youtube.com/watch?v={data.get('id', '')}",
                        "id": data.get("id", ""),
                        "duration": data.get("duration", 0),
                        "uploader": data.get("uploader", ""),
                        "view_count": data.get("view_count", 0),
                    }
                )
            except json.JSONDecodeError:
                continue

        log.info("YouTube search '%s': %d results", query[:40], len(videos))
        return videos

    except subprocess.TimeoutExpired:
        log.warning("YouTube search timeout: %s", query[:40])
        return []
    except Exception as exc:
        log.error("YouTube search error: %s", exc)
        return []


def download_clip(
    url: str,
    output_path: str,
    max_seconds: int = 30,
    max_height: int = 720,
) -> str | None:
    """
    Download first max_seconds of a YouTube video.
    Returns local file path or None if failed.
    """
    ytdlp = _get_ytdlp()
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(output_path).stem

    cmd = [
        ytdlp,
        url,
        "--format",
        f"best[height<={max_height}]/best",
        "--download-sections",
        f"*0-{max_seconds}",
        "--output",
        str(out_dir / f"{stem}.%(ext)s"),
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--force-overwrites",
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)

        for f in sorted(out_dir.glob(f"{stem}.*")):
            if f.is_file() and f.stat().st_size > 5000 and f.suffix in (".mp4", ".mkv", ".webm"):
                log.info("Downloaded: %s (%dKB)", f, f.stat().st_size // 1024)
                return str(f)

        log.warning("yt-dlp ran but no output file found")
        return None

    except subprocess.TimeoutExpired:
        log.warning("Download timeout: %s", url[:60])
        return None
    except Exception as exc:
        log.error("Download error: %s", exc)
        return None
