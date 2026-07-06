"""
BBC Telegram Client — One-shot messages (nu bot persistent).
Trimite mesaje, poze, și notificări prin Telegram Bot API.
"""
import asyncio
import json
import logging

import httpx

from config import settings
from prompts.system_prompts import format_telegram_preview

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


async def send_video(
    chat_id: int | str,
    video_path: str,
    caption: str = "",
    reply_markup: dict | None = None,
) -> dict | None:
    """Send video file to Telegram chat via multipart upload."""
    from pathlib import Path as P

    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN not set")
        return None

    url = f"{BASE_URL}/bot{settings.telegram_bot_token}/sendVideo"
    video_bytes = P(video_path).read_bytes()
    filename = P(video_path).name

    data: dict = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "Markdown"
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    try:
        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.post(
                url,
                data=data,
                files={"video": (filename, video_bytes, "video/mp4")},
            )
            if resp.status_code == 200:
                log.info("Video sent to %s: %s", chat_id, filename)
                return resp.json()
            log.error("sendVideo %s: %s", resp.status_code, resp.text[:200])
            return None
    except Exception as exc:
        log.error("sendVideo error: %s", exc)
        return None


async def answer_callback_query(callback_id: str, text: str = "") -> dict | None:
    """Răspunde la callback query (elimină loading de pe buton)."""
    if not settings.telegram_bot_token:
        return None
    payload: dict = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _bot_url("answerCallbackQuery"),
                json=payload,
            )
            return resp.json()
    except Exception as e:
        log.error("Answer callback error: %s", e)
        return None


async def edit_message_reply_markup(
    chat_id: int | str,
    message_id: int,
    reply_markup: dict | None = None,
) -> dict | None:
    """Editează butoanele unui mesaj (înlocuiește keyboard sau scoate)."""
    if not settings.telegram_bot_token:
        return None
    payload: dict = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    else:
        payload["reply_markup"] = json.dumps({"inline_keyboard": []})
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_bot_url("editMessageReplyMarkup"), json=payload)
            return resp.json()
    except Exception as e:
        log.warning("Edit markup failed: %s", e)
        return None


def approval_caption(event: dict) -> str:
    """Postarea REALĂ (senior SMM text) pentru approval — nu metadata card.

    Telegram photo caption limit = 1024 chars. Fallback pe metadata preview
    doar pentru deal-urile vechi care nu au caption.
    """
    real_caption = event.get("whatsapp_caption") or event.get("caption") or ""
    campaign_line = f"\n\n📋 {event.get('campaign_id', '')}"
    if real_caption:
        if len(real_caption) + len(campaign_line) > 1024:
            return real_caption[: 1024 - len(campaign_line) - 4] + "…" + campaign_line
        return real_caption + campaign_line
    return format_telegram_preview(event)


async def send_approval_request(
    event: dict, chat_id: str | int | None = None
) -> dict | None:
    """Trimite un deal pentru aprobare cu imagine + butoane review."""
    from keyboards import review_keyboard

    campaign_id = event.get("campaign_id", "unknown")
    caption = approval_caption(event)
    keyboard = review_keyboard(campaign_id)
    target_chat = chat_id or settings.telegram_chat_id

    image_url = event.get("image_url")
    if image_url:
        result = await send_photo(
            chat_id=target_chat,
            photo_url=image_url,
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        result = await send_message(chat_id=target_chat, text=caption, reply_markup=keyboard)

    msg_id = None
    if isinstance(result, dict):
        msg_id = result.get("result", {}).get("message_id")

    return {"chat_id": target_chat, "message_id": msg_id}


async def send_deals_for_approval(
    events: list[dict], chat_id: str | int | None = None
) -> list[dict | None]:
    """Trimite toate deal-urile pentru aprobare."""
    results: list[dict | None] = []
    for event in events:
        result = await send_approval_request(event, chat_id=chat_id)
        results.append(result)
        await asyncio.sleep(0.5)
    return results


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
