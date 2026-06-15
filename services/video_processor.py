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
    skip_start: float = 5.0,
) -> list[str]:
    """
    Extract JPEG frames at regular intervals.
    Skips first skip_start seconds to avoid intro logos/watermarks.
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
        "-ss",
        str(skip_start),
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
        log.info("Extracted %d frames (skipped first %ss)", len(frames), skip_start)
        return frames
    except Exception as exc:
        log.error("Extract frames error: %s", exc)
        return []


def select_best_frame(frames: list[str]) -> str | None:
    """Pick the best frame — middle of the list as default."""
    if not frames:
        return None
    return frames[len(frames) // 2]


def select_best_frame_with_claude(
    frames: list[str],
    event_name: str,
    anthropic_client=None,
    model: str = "claude-sonnet-4-20250514",
) -> str | None:
    """
    Claude SEES each frame and picks the best one for BBC.
    Rejects frames with logos, watermarks, broadcast overlays.
    Returns clean frame path or None if all are rejected.
    """
    if not frames:
        return None
    if not anthropic_client:
        return select_best_frame(frames)

    import base64

    sample = frames[:5] if len(frames) > 5 else frames

    images_content: list[dict] = []
    for i, fp in enumerate(sample):
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        images_content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            }
        )
        images_content.append({"type": "text", "text": f"Frame {i}"})

    images_content.append(
        {
            "type": "text",
            "text": f"""These are frames extracted from a YouTube video about "{event_name}".
I need ONE frame for a BuyBusinessClass.com branded post.

CHECK EACH FRAME:
- Does it have visible LOGOS from other companies? (TV channels, sponsors)
- Does it have WATERMARKS or BANNERS? (subscribe buttons, app ads, tickers)
- Does it have BROADCAST OVERLAYS? (scores, timers, news chyrons)
- Is it VISUALLY APPEALING for a luxury travel brand?

REPLY with EXACTLY one of:
- "BEST: 0" (or 1, 2, 3, 4) — the number of the cleanest, most beautiful frame
- "NONE" — if ALL frames have logos/watermarks/overlays and none are usable

Pick the frame that is CLEAN and BEAUTIFUL. No logos. No watermarks.""",
        }
    )

    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": images_content}],
        )
        answer = resp.content[0].text.strip().upper()

        if "NONE" in answer:
            log.warning("Claude rejected ALL frames (logos/watermarks)")
            return None

        for ch in answer:
            if ch.isdigit():
                idx = int(ch)
                if 0 <= idx < len(sample):
                    log.info("Claude selected frame %d: %s", idx, sample[idx])
                    return sample[idx]

        return select_best_frame(frames)

    except Exception as exc:
        log.warning("Claude frame check failed: %s — using middle frame", exc)
        return select_best_frame(frames)


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
