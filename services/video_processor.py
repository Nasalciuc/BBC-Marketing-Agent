"""
Video processor — FFmpeg operations for BBC pipeline.
Extract frames, trim clips, add simple text overlay.
"""
from __future__ import annotations

import logging
import json
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
            "text": f"""These frames are from a YouTube video for a BuyBusinessClass.com post about: "{event_name}".

CHECK EACH FRAME — three tests, ALL must pass:

TEST 1 — SUBJECT MATCH (most important):
Does the frame CLEARLY SHOW the subject "{event_name}"?
- Post about a resort → must show pools/villas/designed luxury, NOT ruins or random landscape
- Post about a lounge/cabin → must show THE interior, NOT ad campaign footage or people holding signs
- Post about a destination → must show the ICONIC recognizable view
A stranger seeing the frame alone must understand what we sell.

TEST 2 — LUXURY LOOK:
Cinematic, aspirational, premium. Not amateur, not confusing, not ugly.

TEST 3 — CLEAN:
No third-party logos, watermarks, banners, ad graphics, text overlays.

REPLY with EXACTLY one of:
- "BEST: 0" (or 1, 2, 3, 4) — the frame passing ALL THREE tests
- "NONE" — if no frame passes all three (off-subject frames = NONE even if clean and pretty)""",
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
        "-c:v",
        "libx264",
        "-crf",
        "18",
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
        "-crf",
        "18",
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


def concat_videos(clip_a: str, clip_b: str, output_path: str, target_height: int = 1080) -> str | None:
    """Concatenate two clips (cabin intro + destination) into one video.
    Normalizes both to same resolution, CRF 18."""
    ffmpeg = _get_ffmpeg()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"[0:v]scale=-2:{target_height},setsar=1,fps=25[v0];"
        f"[1:v]scale=-2:{target_height},setsar=1,fps=25[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[outv]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        clip_a,
        "-i",
        clip_b,
        "-filter_complex",
        vf,
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        p = Path(output_path)
        if p.exists() and p.stat().st_size > 5000:
            log.info("Concat: %s (%dKB)", p.name, p.stat().st_size // 1024)
            return output_path
        return None
    except Exception as exc:
        log.error("Concat error: %s", exc)
        return None


def probe_video_stream(path: str) -> dict | None:
    """Return width, height, bit_rate via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,bit_rate",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        return streams[0] if streams else None
    except Exception as exc:
        log.warning("ffprobe failed for %s: %s", path, exc)
        return None
