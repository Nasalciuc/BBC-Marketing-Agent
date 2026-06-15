"""
BBC Veo Client — Gemini video generation (text-to-video, image-to-video).

Requires Gemini API paid tier + billing on Google AI Studio.
Uses the same GEMINI_API_KEY as gemini_client.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from config import settings
from prompts.video_prompts import NEGATIVE_PROMPT_VIDEO, resolve_video_prompt

log = logging.getLogger("bbc.veo")

VEO_MODEL_STANDARD = "veo-3.1-generate-preview"
VEO_MODEL_FAST = "veo-3.1-fast-generate-preview"
VEO_MODEL_LEGACY = "veo-2.0-generate-exp"

DEFAULT_POLL_INTERVAL_SEC = 15
DEFAULT_MAX_WAIT_SEC = 600


def _get_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=settings.gemini_api_key)


def _resolve_model(fast: bool = False) -> str:
    if settings.veo_model:
        return settings.veo_model
    return VEO_MODEL_FAST if fast else VEO_MODEL_STANDARD


def _build_config(
    *,
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
    duration_seconds: int = 8,
    negative_prompt: str = NEGATIVE_PROMPT_VIDEO,
    last_frame_bytes: bytes | None = None,
    last_frame_mime: str = "image/jpeg",
) -> types.GenerateVideosConfig:
    """Build Veo config — omit generate_audio (not supported on Developer API)."""
    kwargs: dict = {
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "negative_prompt": negative_prompt,
    }
    if last_frame_bytes:
        kwargs["last_frame"] = types.Image(
            image_bytes=last_frame_bytes,
            mime_type=last_frame_mime,
        )
    return types.GenerateVideosConfig(**kwargs)


def _operation_done(operation) -> bool:
    if getattr(operation, "done", None) is True:
        return True
    if callable(getattr(operation, "done", None)):
        return bool(operation.done())
    return bool(getattr(operation, "result", None))


def _poll_operation_sync(client: genai.Client, operation, poll_interval: int, max_wait: int):
    elapsed = 0
    while not _operation_done(operation):
        if elapsed >= max_wait:
            raise TimeoutError(f"Veo generation timed out after {max_wait}s")
        time.sleep(poll_interval)
        elapsed += poll_interval
        operation = client.operations.get(operation)
        log.info("Veo polling... %ds", elapsed)
    if getattr(operation, "error", None):
        raise RuntimeError(f"Veo generation failed: {operation.error}")
    return operation


def _mime_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def _download_video_bytes(client: genai.Client, video_obj) -> bytes:
    if video_obj is None:
        raise RuntimeError("No video object in Veo response")

    raw_bytes = getattr(video_obj, "video_bytes", None)
    if raw_bytes:
        return bytes(raw_bytes)

    uri = getattr(video_obj, "uri", None) or getattr(video_obj, "gcs_uri", None)
    if not uri:
        raise RuntimeError("Veo response has no video_bytes or uri")

    if uri.startswith("gs://"):
        downloaded = client.files.download(file=uri)
        if isinstance(downloaded, bytes):
            return downloaded
        if hasattr(downloaded, "read"):
            return downloaded.read()
        return bytes(downloaded)

    headers = {}
    if settings.gemini_api_key and "generativelanguage.googleapis.com" in uri:
        headers["x-goog-api-key"] = settings.gemini_api_key

    with httpx.Client(timeout=120, follow_redirects=True) as http:
        resp = http.get(uri, headers=headers)
        resp.raise_for_status()
        return resp.content


def _extract_video_bytes(client: genai.Client, operation) -> bytes:
    result = operation.result
    if not result or not getattr(result, "generated_videos", None):
        raise RuntimeError("Veo operation completed without generated_videos")

    generated = result.generated_videos[0]
    video_obj = getattr(generated, "video", None)
    return _download_video_bytes(client, video_obj)


def _generate_video_sync(
    prompt: str,
    *,
    image_bytes: bytes | None = None,
    image_mime: str = "image/jpeg",
    last_frame_bytes: bytes | None = None,
    last_frame_mime: str = "image/jpeg",
    model: str | None = None,
    fast: bool = False,
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
    duration_seconds: int = 8,
    generate_audio: bool = False,
    poll_interval: int = DEFAULT_POLL_INTERVAL_SEC,
    max_wait: int = DEFAULT_MAX_WAIT_SEC,
) -> bytes:
    client = _get_client()
    model_name = model or _resolve_model(fast=fast)
    config = _build_config(
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration_seconds,
        last_frame_bytes=last_frame_bytes,
        last_frame_mime=last_frame_mime,
    )

    kwargs: dict = {
        "model": model_name,
        "prompt": prompt,
        "config": config,
    }
    if image_bytes:
        kwargs["image"] = types.Image(image_bytes=image_bytes, mime_type=image_mime)

    log.info("Veo generate_videos model=%s prompt_len=%d image=%s", model_name, len(prompt), bool(image_bytes))
    operation = client.models.generate_videos(**kwargs)
    operation = _poll_operation_sync(client, operation, poll_interval, max_wait)
    return _extract_video_bytes(client, operation)


async def generate_video(
    prompt: str | None = None,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    custom_prompt: str = "",
    image_path: str | Path | None = None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    last_frame_path: str | Path | None = None,
    output_path: str | Path | None = None,
    model: str | None = None,
    fast: bool = False,
    aspect_ratio: str = "16:9",
    resolution: str = "1080p",
    duration_seconds: int = 8,
    generate_audio: bool = False,
    poll_interval: int = DEFAULT_POLL_INTERVAL_SEC,
    max_wait: int = DEFAULT_MAX_WAIT_SEC,
) -> bytes:
    """
    Generate video via Gemini Veo. Returns MP4 bytes.

    Provide `prompt` OR (`category` + `subcategory`) OR `custom_prompt`.
    Optional `image_path` / `image_bytes` for image-to-video.
    """
    resolved_prompt = prompt or resolve_video_prompt(category, subcategory, custom_prompt)

    img_bytes = image_bytes
    mime = image_mime or "image/jpeg"
    if image_path and not img_bytes:
        path = Path(image_path)
        img_bytes = path.read_bytes()
        mime = _mime_for_path(path)

    last_bytes = None
    last_mime = "image/jpeg"
    if last_frame_path:
        lf = Path(last_frame_path)
        last_bytes = lf.read_bytes()
        last_mime = _mime_for_path(lf)

    video_bytes = await asyncio.to_thread(
        _generate_video_sync,
        resolved_prompt,
        image_bytes=img_bytes,
        image_mime=mime,
        last_frame_bytes=last_bytes,
        last_frame_mime=last_mime,
        model=model,
        fast=fast,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration_seconds,
        generate_audio=generate_audio,
        poll_interval=poll_interval,
        max_wait=max_wait,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(video_bytes)
        log.info("Saved video: %s (%s bytes)", out, f"{len(video_bytes):,}")

    return video_bytes


async def generate_video_from_deal_image(
    deal_image_path: str | Path,
    category: str,
    subcategory: str,
    output_path: str | Path,
    *,
    fast: bool = True,
    custom_motion: str = "",
) -> bytes:
    """Image-to-video for an existing branded deal banner."""
    prompt = resolve_video_prompt(category, subcategory, custom_motion)
    return await generate_video(
        prompt=prompt,
        image_path=deal_image_path,
        output_path=output_path,
        fast=fast,
    )
