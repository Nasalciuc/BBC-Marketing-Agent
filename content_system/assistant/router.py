"""
What the webhook calls when CONTENT_ASSISTANT_ENABLED=true (after its admin check):

  route_card(cb, update_id)        "ca:…" buttons on the assistant's cards — no AI
  route_message(msg, update_id)    True when handled here: the menu, /start, /help, replies to our
                                   messages, and any other text → the assistant, in the background

Everything here answers Betty itself and never raises into the webhook.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from content_system import bridge
from content_system.assistant import texts
from content_system.assistant.posts import recent
from content_system.assistant.proposals import handle_card, propose

log = logging.getLogger("bbc.content.assistant.router")

_assistant: Any = None
_assistant_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def use(assistant: Any) -> None:
    """Tests set the assistant (with fake models) explicitly."""
    global _assistant
    _assistant = assistant


async def assistant() -> Any:
    global _assistant
    if _assistant is None:
        async with _assistant_lock:
            if _assistant is None:
                from content_system.assistant.agent import Assistant

                _assistant = Assistant(await bridge.live())
    return _assistant


def background(coro: Any) -> asyncio.Task:
    """Run without making Telegram wait (it retries a webhook that answers slowly)."""
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    """Wait for the background work (tests; a graceful shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _say(lv: Any, chat_id: int, text: str, *, reply_to: int | None = None, markup: dict | None = None) -> Any:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if markup:
        data["reply_markup"] = markup
    return await lv.services.telegram.call("sendMessage", data)


def is_idea_prompt(replied: dict) -> bool:
    sender = replied.get("from") or {}
    bot_id = bridge._bot_id()
    mine = sender.get("is_bot") and (bot_id is None or sender.get("id") == bot_id)
    return bool(mine and (replied.get("text") or "").startswith(texts.IDEA_MARK))


async def menu(lv: Any, chat_id: int, text: str, reply_to: int | None) -> bool:
    """The fixed buttons and commands. No AI."""
    keyboard = texts.menu_keyboard()
    if text.startswith("/start"):
        await _say(lv, chat_id, texts.WELCOME, markup=keyboard)
    elif text.startswith("/help") or text == texts.MENU_HELP:
        await _say(lv, chat_id, texts.HELP, markup=keyboard)
    elif text == texts.MENU_IDEA:
        await _say(lv, chat_id, texts.IDEA_PROMPT,
                   markup={"force_reply": True, "input_field_placeholder": "Ideea ta"})
    elif text in (texts.MENU_WEEK, texts.MENU_TODO):
        posts = await recent(lv.graph, lv.services.store)
        if not posts:
            await _say(lv, chat_id, texts.NO_POSTS, markup=keyboard)
        elif text == texts.MENU_WEEK:
            from content_system.assistant.posts import overview

            await _say(lv, chat_id, overview(posts), markup=keyboard)
        else:
            waiting = [p for p in posts if p.waiting]
            if not waiting:
                await _say(lv, chat_id, texts.NOTHING_WAITING, markup=keyboard)
            else:
                await propose(lv.services.store, lv.services.telegram, chat_id, "approve",
                              sorted(waiting, key=lambda p: p.day), intro=f"{texts.MENU_TODO}: {len(waiting)}")
    else:
        return False
    return True


async def route_card(cb: dict, update_id: int | None) -> None:
    try:
        lv = await bridge.live()
    except Exception:
        log.exception("content system unavailable")
        await bridge._answer(cb.get("id", ""), texts.DB_DOWN)
        return
    await handle_card(lv, cb, update_id)


async def route_message(msg: dict, update_id: int | None) -> bool:
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return False
    text = (msg.get("text") or "").strip()
    here = msg.get("message_id")
    replied = msg.get("reply_to_message") or None
    if text.startswith("/") and not text.startswith(("/start", "/help")):
        if replied and text.startswith("/cancel"):
            return await bridge.handle_reply(msg, update_id)
        return False                                  # /status, /deals, /cancel: the old handlers
    try:
        lv = await bridge.live()
    except Exception:
        log.exception("content system unavailable")
        await bridge._say(chat_id, texts.DB_DOWN, here)
        return True
    focus = None
    try:
        if replied:
            if await bridge.handle_reply(msg, update_id):      # a reply to "✏️ … · vN": the exact caption
                return True
            if is_idea_prompt(replied):
                if text and (update_id is None or await lv.services.store.claim(f"tg:update:{update_id}")):
                    await lv.services.store.add_idea(chat_id, (msg.get("from") or {}).get("id"), text[:4000])
                    await _say(lv, chat_id, texts.IDEA_SAVED, reply_to=here, markup=texts.menu_keyboard())
                return True
            mark = bridge.ours(replied)
            if mark:
                focus = mark[0]
            elif replied.get("message_id") is not None:
                ref = await lv.services.store.find_message(chat_id, replied["message_id"])
                focus = ref["campaign_id"] if ref else None
        if await menu(lv, chat_id, text, here):
            return True
    except Exception:
        log.exception("assistant routing failed")
        await bridge._say(chat_id, texts.DB_DOWN, here, lv.services.telegram)
        return True
    if not text:
        await _say(lv, chat_id, "Deocamdată înțeleg doar text. Pentru poze, scrie-mi ce vrei și îi trimit lui Scaler.",
                   reply_to=here)
        return True

    async def run() -> None:
        try:
            a = await assistant()
        except Exception:
            log.exception("assistant unavailable")
            await bridge._say(chat_id, texts.AI_DOWN, here, lv.services.telegram)
            return
        await a.handle(chat_id, (msg.get("from") or {}).get("id"), text, focus=focus, update_id=update_id,
                       message_id=here)

    background(run())
    return True
