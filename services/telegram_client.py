"""
BBC Telegram Client — One-shot messages (nu bot persistent).
Trimite mesaje, poze, și notificări prin Telegram Bot API.
"""
import asyncio
import json
import logging

import httpx

from config import settings

log = logging.getLogger("bbc.telegram")

BASE_URL = "https://api.telegram.org"


def _bot_url(method: str) -> str:
    return f"{BASE_URL}/bot{settings.telegram_bot_token}/{method}"


async def send_message(
    chat_id: str | int | None = None,
    text: str = "",
    parse_mode: str = "Markdown",
    reply_markup: dict | None = None,
) -> dict | None:
    """Trimite mesaj text pe Telegram."""
    if not settings.telegram_bot_token:
        log.warning("Telegram not configured — skipping message")
        return None

    chat_id = chat_id or settings.telegram_chat_id

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_bot_url("sendMessage"), json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.error("Telegram sendMessage failed: %s", data)
            return data
    except Exception as e:
        log.error("Telegram sendMessage error: %s", e)
        return None


async def send_photo(
    chat_id: str | int | None = None,
    photo_url: str = "",
    caption: str = "",
    parse_mode: str = "Markdown",
    reply_markup: dict | None = None,
) -> dict | None:
    """Trimite imagine pe Telegram (via URL public)."""
    if not settings.telegram_bot_token:
        log.warning("Telegram not configured — skipping photo")
        return None

    chat_id = chat_id or settings.telegram_chat_id

    payload: dict = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_bot_url("sendPhoto"), json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.error("Telegram sendPhoto failed: %s", data)
            return data
    except Exception as e:
        log.error("Telegram sendPhoto error: %s", e)
        return None


async def answer_callback_query(callback_id: str, text: str) -> dict | None:
    """Răspunde la callback query (elimină loading de pe buton)."""
    if not settings.telegram_bot_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _bot_url("answerCallbackQuery"),
                json={"callback_query_id": callback_id, "text": text},
            )
            return resp.json()
    except Exception as e:
        log.error("Answer callback error: %s", e)
        return None


async def send_approval_request(event: dict, chat_id: str | int | None = None):
    """Trimite un deal pentru aprobare cu imagine + butoane ✅❌."""
    campaign_id = event.get("campaign_id", "unknown")

    routes = event.get("routes", [{}])
    route_str = event.get("route_str", "")
    if not route_str and routes:
        r = routes[0] if isinstance(routes[0], dict) else {}
        route_str = f"{r.get('from', '?')} → {r.get('to', '?')}"

    category = event.get("category", "")
    emoji = {
        "motorsport": "🏎️",
        "tennis": "🎾",
        "football": "⚽",
        "business": "💼",
        "fashion": "👗",
        "film": "🎬",
        "art": "🎨",
        "yachting": "🛥️",
        "music": "🎵",
    }.get(category, "✈️")

    caption = (
        f"{emoji} *{event.get('name', event.get('event_name', 'Event'))}*\n"
        f"📍 {event.get('city', '?')}\n"
        f"📅 {event.get('dates_start', '?')} — {event.get('dates_end', '?')}\n"
        f"✈️ {route_str}\n"
        f"💰 *{event.get('price', '?')}* business round-trip\n"
        f"⭐ Score: {event.get('premium_score', '?')}/10\n\n"
        f"_{event.get('caption', event.get('caption_draft', ''))}_"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve_{campaign_id}"},
                {"text": "❌ Reject", "callback_data": f"reject_{campaign_id}"},
            ]
        ]
    }

    image_url = event.get("image_url")
    if image_url:
        await send_photo(
            chat_id=chat_id,
            photo_url=image_url,
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        await send_message(chat_id=chat_id, text=caption, reply_markup=keyboard)


async def send_deals_for_approval(events: list[dict]):
    """Trimite toate deal-urile pentru aprobare."""
    for event in events:
        await send_approval_request(event)
        await asyncio.sleep(0.5)


async def send_alert(message: str):
    """Trimite alertă simplă."""
    await send_message(text=f"⚠️ *BBC Marketing Agent*\n\n{message}")


async def send_broadcast_report(results: dict):
    """Trimite raport broadcast complet."""
    text = (
        f"✅ *Broadcast Complete*\n\n"
        f"📨 Sent: {results.get('sent', 0)}\n"
        f"❌ Failed: {results.get('failed', 0)}\n"
        f"📱 Group posted: {'✅' if results.get('group_posted') else '❌'}"
    )
    await send_message(text=text)
