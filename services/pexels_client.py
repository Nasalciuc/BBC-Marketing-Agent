"""Pexels API — stock photos and videos."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

log = logging.getLogger("bbc.pexels")

BASE_URL = "https://api.pexels.com"


def _headers() -> dict[str, str]:
    if not settings.pexels_api_key:
        raise ValueError("PEXELS_API_KEY is not configured")
    return {"Authorization": settings.pexels_api_key}


def is_configured() -> bool:
    return bool(settings.pexels_api_key.strip())


async def search_videos(query: str, *, per_page: int = 5, page: int = 1) -> dict[str, Any]:
    """Search Pexels videos. Returns API JSON."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/videos/search",
            params={"query": query, "per_page": per_page, "page": page},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def search_photos(query: str, *, per_page: int = 5, page: int = 1) -> dict[str, Any]:
    """Search Pexels photos. Returns API JSON."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/v1/search",
            params={"query": query, "per_page": per_page, "page": page},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def best_video_file(video: dict[str, Any], min_height: int = 1080) -> dict[str, Any] | None:
    """Pick best HD video file from a Pexels video object."""
    files = video.get("video_files") or []
    hd = [f for f in files if f.get("height", 0) >= min_height]
    candidates = hd or files
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.get("height", 0))
