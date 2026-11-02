"""
Telegram → content system. webhook.py calls these after its admin check:

  handle_callback(cb, update_id)   buttons "cs:a|r|e:<campaign_id>:<version>"
  handle_reply(msg, update_id)     a reply to one of our review / edit messages = a new caption

Both answer Betty themselves and never raise into the webhook. With CONTENT_ENABLED=false the
buttons answer "off" and replies fall through to the old handlers.

A reply is recognised by the line every message of ours carries ("📋 <id> · v<n>" at the end of a
review caption, "✏️ <id> · v<n>" at the start of an edit prompt) on a message sent by this bot —
so it still works for a review message whose id was never recorded, and when the database is down
the reply gets an answer instead of falling through to the AI conversation handler.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from content_system.engine import decide, view
from content_system.settings import ContentSettings, get_settings
from content_system.store import LeaseBusy
from content_system.telegram import TelegramAPI, parse_callback

log = logging.getLogger("bbc.content.bridge")
_ID = r"(\d{4}-W\d{2}-[A-Z0-9]{1,6}-\d{3}) · v(\d{1,3})"
_REVIEW_MARK = re.compile("📋 " + _ID + r"\s*$")
_PROMPT_MARK = re.compile("^✏️ " + _ID)

EDIT_PROMPT = (
    "✏️ {cid} · v{version}\n"
    "Reply to this message with the new caption.\n\n"
    "Send just the text: the fare line and the contact line are added for you "
    "(or paste the whole caption, contact line included).\n"
    "No $ (the fare stays on the image) and none of _ * ` ["
)


@dataclass
class Live:
    settings: ContentSettings
    graph: Any
    services: Any        # graph.Services
    close: Any = None    # async callable


_live: Live | None = None
_live_lock = asyncio.Lock()


def use(live: Live | None) -> None:
    """Tests (and a future app lifespan) set the runtime explicitly."""
    global _live
    _live = live


async def _build() -> Live:
    from config import settings as app
    from content_system.adapters import get_image, put_image, render_files
    from content_system.db import Database
    from content_system.graph import Services, build_graph
    from content_system.store import PgStore

    cs = get_settings()
    db = Database(cs.db_url, schema=cs.db_schema, max_size=cs.db_pool_max)
    await db.open(wait_s=10)
    services = Services(
        store=PgStore(db.pool), render=render_files, output_root=Path(cs.output_root),
        put_image=put_image, get_image=get_image,
        telegram=TelegramAPI(app.telegram_bot_token), chat_id=app.telegram_chat_id,
    )
    return Live(settings=cs, graph=build_graph(db.checkpointer()), services=services, close=db.close)


async def live() -> Live:
    global _live
    if _live is None:
        async with _live_lock:
            if _live is None:
                _live = await _build()
    return _live


def _app_token() -> str:
    from config import settings as app

    return app.telegram_bot_token


def _bot_id() -> int | None:
    head = _app_token().split(":", 1)[0]
    return int(head) if head.isdigit() else None


def ours(replied: dict) -> tuple[str, int] | None:
    """(campaign_id, version) when `replied` is a review or edit message sent by this bot, else None."""
    sender = replied.get("from") or {}
    bot_id = _bot_id()
    if not sender.get("is_bot") or (bot_id is not None and sender.get("id") != bot_id):
        return None
    m = _REVIEW_MARK.search(replied.get("caption") or "") or _PROMPT_MARK.search(replied.get("text") or "")
    return (m.group(1), int(m.group(2))) if m else None


async def _answer(callback_id: str, text: str, telegram: Any = None) -> None:
    try:
        telegram = telegram or TelegramAPI(_app_token())
        await telegram.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
    except Exception as e:  # noqa: BLE001 — the toast is cosmetic
        log.warning("answerCallbackQuery failed: %s", e)


async def _say(chat_id: int, text: str, reply_to: int | None = None, telegram: Any = None) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    try:
        telegram = telegram or TelegramAPI(_app_token())
        await telegram.call("sendMessage", data)
    except Exception as e:  # noqa: BLE001
        log.warning("sendMessage failed: %s", e)


async def _edit_prompt(lv: Live, cb: dict, campaign_id: str) -> str:
    current = await view(lv.graph, campaign_id)
    if not current.exists:
        return f"{campaign_id} was not sent by the content system"
    if not current.waiting:
        return f"Already {current.status} — edits are closed"
    message = cb.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or lv.services.chat_id
    sent = await lv.services.telegram.call("sendMessage", {
        "chat_id": chat_id,
        "text": EDIT_PROMPT.format(cid=campaign_id, version=current.version),
        "reply_markup": {"force_reply": True, "input_field_placeholder": "New caption"},
        "reply_parameters": {"message_id": message.get("message_id"), "allow_sending_without_reply": True}
        if message.get("message_id") else None,
    })
    await lv.services.store.add_message(sent["chat"]["id"], sent["message_id"], campaign_id,
                                        current.version, "edit_prompt")
    return "✏️ Reply to my message with the new caption"


async def handle_callback(cb: dict, update_id: int | None) -> None:
    callback_id = cb.get("id", "")
    if not get_settings().enabled:
        await _answer(callback_id, "The content system is off (CONTENT_ENABLED=false)")
        return
    parsed = parse_callback(cb.get("data", ""))
    if parsed is None:
        await _answer(callback_id, "Unknown button")
        return
    action, campaign_id, version = parsed
    try:
        lv = await live()
    except Exception:
        log.exception("content system unavailable")
        await _answer(callback_id, "⚠️ The content system can't reach its database — try again later")
        return
    key = f"tg:update:{update_id}" if update_id is not None else None
    if key and not await lv.services.store.claim(key):
        await _answer(callback_id, "", lv.services.telegram)       # Telegram delivered this tap twice
        return
    try:
        if action == "edit":
            text = await _edit_prompt(lv, cb, campaign_id)
        else:
            out = await decide(lv.graph, lv.services, campaign_id, action, version=version,
                               lock_timeout=lv.settings.lock_timeout_s)
            text = out.message
    except LeaseBusy:
        text = "⏳ Another tap on this post is still running — try again in a few seconds"
        if key:
            await lv.services.store.release(key)
    except Exception:
        log.exception("content decision failed: %s %s v%s", action, campaign_id, version)
        text = "⚠️ That didn't go through — tap again (the error is in the logs)"
        if key:
            await lv.services.store.release(key)
    await _answer(callback_id, text, lv.services.telegram)


async def handle_reply(msg: dict, update_id: int | None) -> bool:
    """True when the message was a reply to one of our messages (handled here)."""
    if not get_settings().enabled:
        return False
    chat_id = (msg.get("chat") or {}).get("id")
    replied = msg.get("reply_to_message") or {}
    if chat_id is None or replied.get("message_id") is None:
        return False
    here = msg.get("message_id")
    mark = ours(replied)
    try:
        lv = await live()
    except Exception:
        log.exception("content system unavailable")
        if mark is None:
            return False
        await _say(chat_id, f"{mark[0]}: ⚠️ the content system can't reach its database — "
                            "send the caption again in a minute", here)
        return True
    if mark is not None:
        campaign_id = mark[0]
    else:   # not recognisable by its text: maybe a message we recorded
        try:
            ref = await lv.services.store.find_message(chat_id, replied["message_id"])
        except Exception:
            log.exception("content system unavailable")
            return False
        if ref is None:
            return False
        campaign_id = ref["campaign_id"]
    tg = lv.services.telegram
    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        if text.startswith("/cancel"):
            await _say(chat_id, f"{campaign_id}: edit cancelled — nothing changed", here, tg)
            return True
        return False
    if not text:
        await _say(chat_id, "Send the new caption as text.", here, tg)
        return True
    key = f"tg:update:{update_id}" if update_id is not None else None
    if key and not await lv.services.store.claim(key):
        return True
    try:
        out = await decide(lv.graph, lv.services, campaign_id, "edit", caption=text,
                           lock_timeout=lv.settings.lock_timeout_s)
        if not out.ok:
            await _say(chat_id, f"{campaign_id}: {out.message}", here, tg)
    except LeaseBusy:
        if key:
            await lv.services.store.release(key)
        await _say(chat_id, f"{campaign_id}: another change is still running — send it again in a few seconds",
                   here, tg)
    except Exception:
        log.exception("content edit failed for %s", campaign_id)
        if key:
            await lv.services.store.release(key)
        await _say(chat_id, f"{campaign_id}: the edit didn't go through — send it again", here, tg)
    return True
