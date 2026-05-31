"""
BBC WAHA Client — WhatsApp group posting via WAHA API.
"""
import logging

import httpx

from config import settings

log = logging.getLogger("bbc.waha")


async def post_deal_to_group(
    image_url: str, caption: str, group_id: str | None = None
) -> dict:
    """Trimite imagine + caption în grupul WhatsApp BBC."""
    if not settings.waha_url or not settings.waha_api_key:
        log.warning("WAHA not configured — skipping group post")
        return {"status": "skipped", "reason": "WAHA not configured"}

    group_id = group_id or settings.whatsapp_group_id
    if not group_id:
        log.warning("WhatsApp group ID not set — skipping")
        return {"status": "skipped", "reason": "no group ID"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            health = await client.get(
                f"{settings.waha_url}/api/sessions",
                headers={"X-Api-Key": settings.waha_api_key},
            )
            sessions = health.json()
            if not any(
                s.get("status") == "WORKING" for s in sessions if isinstance(s, dict)
            ):
                log.error("WAHA session not connected!")
                return {"status": "disconnected"}

            resp = await client.post(
                f"{settings.waha_url}/api/sendImage",
                headers={"X-Api-Key": settings.waha_api_key},
                json={
                    "session": "default",
                    "chatId": group_id,
                    "file": {"url": image_url},
                    "caption": caption,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            log.info("Posted to group: %s", result.get("id", "ok"))
            return {"status": "sent", "message_id": result.get("id")}

    except Exception as e:
        log.error("WAHA post failed: %s", e)
        return {"status": "failed", "error": str(e)}


async def check_waha_health() -> bool:
    """Verifică dacă WAHA e conectat."""
    if not settings.waha_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.waha_url}/api/sessions",
                headers={"X-Api-Key": settings.waha_api_key},
            )
            sessions = resp.json()
            return any(
                s.get("status") == "WORKING" for s in sessions if isinstance(s, dict)
            )
    except Exception:
        return False
