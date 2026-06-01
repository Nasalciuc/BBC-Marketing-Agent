"""
BBC Gemini Client — Event Discovery + Image Generation.

Uses Google Gemini API (google-genai SDK) for:
1. Premium event discovery with Google Search grounding
2. Background image generation for branding

Models:
- Text/discovery: gemini-2.5-flash
- Image gen: gemini-2.5-flash-image, gemini-3.1-flash-image (+ Imagen 4 fallback)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timedelta

from google import genai
from google.genai import types

from config import settings
from prompts.system_prompts import (
    DISCOVERY_PROMPT,
    IMAGE_GEN_SYSTEM,
    VERIFICATION_PROMPT,
    normalize_discovered_event,
)

log = logging.getLogger("bbc.gemini")

IMAGE_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
]
IMAGEN_MODEL = "imagen-4.0-generate-001"


def _get_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=settings.gemini_api_key)


def _response_text(response: types.GenerateContentResponse) -> str:
    if getattr(response, "text", None):
        return response.text.strip()
    chunks: list[str] = []
    for part in getattr(response, "parts", []) or []:
        if part.text:
            chunks.append(part.text)
    if chunks:
        return "".join(chunks).strip()
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in content.parts or []:
            if part.text:
                chunks.append(part.text)
    return "".join(chunks).strip()


def _extract_image_bytes(response: types.GenerateContentResponse) -> bytes | None:
    parts = list(getattr(response, "parts", []) or [])
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if content and content.parts:
            parts.extend(content.parts)

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if not inline or not inline.mime_type or not inline.mime_type.startswith("image/"):
            continue
        data = inline.data
        if isinstance(data, str):
            return base64.b64decode(data)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    return None


def _parse_events_json(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    return data.get("events", [])


def _discover_events_sync(week_offset: int) -> list[dict]:
    today = datetime.now(UTC)
    week_start = today + timedelta(weeks=week_offset, days=-today.weekday())
    week_end = week_start + timedelta(days=6)

    prompt = DISCOVERY_PROMPT.format(
        week_start=week_start.strftime("%d %B %Y"),
        week_end=week_end.strftime("%d %B %Y"),
    )

    client = _get_client()
    log.info(
        "Discovering events for %s — %s",
        week_start.strftime("%d %b"),
        week_end.strftime("%d %b %Y"),
    )

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    events = _parse_events_json(_response_text(response))
    events = [normalize_discovered_event(e) for e in events]
    events.sort(key=lambda e: e.get("premium_score", 0), reverse=True)
    events = events[:5]

    log.info("Found %d events", len(events))
    for event in events:
        log.info(
            "  %s/10 — %s (%s)",
            event.get("premium_score", "?"),
            event.get("name", "?"),
            event.get("city", "?"),
        )
    return events


def _verify_event_sync(event: dict) -> bool:
    client = _get_client()
    prompt = VERIFICATION_PROMPT.format(
        event_name=event.get("name", ""),
        city=event.get("city", ""),
        dates_start=event.get("dates_start", ""),
        dates_end=event.get("dates_end", ""),
    )

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    answer = _response_text(response).upper()
    verified = "YES" in answer or "DA" in answer
    log.info("Verify '%s': %s (%s)", event.get("name"), "OK" if verified else "FAIL", answer[:20])
    return verified


def _generate_event_image_sync(image_prompt: str) -> bytes | None:
    client = _get_client()
    full_prompt = f"{IMAGE_GEN_SYSTEM}\n\nGenerate this image:\n{image_prompt}"

    for model_name in IMAGE_MODELS:
        try:
            log.info("Generating image with %s...", model_name)
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    temperature=0.8,
                    image_config=types.ImageConfig(aspect_ratio="16:9"),
                ),
            )
            image_bytes = _extract_image_bytes(response)
            if image_bytes:
                log.info("Image generated: %s bytes (%s)", f"{len(image_bytes):,}", model_name)
                return image_bytes
            log.warning("No image in response from %s", model_name)
        except Exception as exc:
            log.warning("Image gen failed with %s: %s", model_name, exc)

    try:
        log.info("Trying Imagen 4...")
        response = client.models.generate_images(
            model=IMAGEN_MODEL,
            prompt=image_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
            ),
        )
        if response.generated_images:
            image_bytes = response.generated_images[0].image.image_bytes
            log.info("Image generated via Imagen 4: %s bytes", f"{len(image_bytes):,}")
            return image_bytes
    except Exception as exc:
        log.warning("Imagen 4 failed: %s", exc)

    log.error("All image generation models failed")
    return None


async def discover_events(week_offset: int = 1) -> list[dict]:
    """Discover up to 5 premium events for the target week."""
    if not settings.gemini_api_key:
        log.error("GEMINI_API_KEY not set")
        return []
    try:
        return await asyncio.to_thread(_discover_events_sync, week_offset)
    except json.JSONDecodeError as exc:
        log.error("Gemini returned invalid JSON: %s", exc)
        return []
    except Exception as exc:
        log.error("Discovery failed: %s", exc)
        return []


async def verify_event(event: dict) -> bool:
    """Fact-check a single event with a second Gemini call."""
    if not settings.gemini_api_key:
        return False
    try:
        return await asyncio.to_thread(_verify_event_sync, event)
    except Exception as exc:
        log.warning("Verification failed for '%s': %s — assuming valid", event.get("name"), exc)
        return True


async def generate_event_image(image_prompt: str) -> bytes | None:
    """Generate a background image for branding."""
    if not settings.gemini_api_key:
        log.error("GEMINI_API_KEY not set")
        return None
    return await asyncio.to_thread(_generate_event_image_sync, image_prompt)


async def discover_and_verify(week_offset: int = 1, max_events: int = 3) -> list[dict]:
    """Discover events, verify each, return confirmed ones (or top unverified fallback)."""
    events = await discover_events(week_offset)
    if not events:
        return []

    verified: list[dict] = []
    for event in events:
        if await verify_event(event):
            verified.append(event)
        if len(verified) >= max_events:
            break

    if not verified:
        log.warning("No events verified — returning top %d unverified", max_events)
        return events[:max_events]
    return verified
