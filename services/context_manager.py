"""
Chat Context Manager — tracks conversation state per chat.
Supabase table: chat_context.
Graceful fallback if table missing or Supabase down.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

log = logging.getLogger("bbc.context")

MAX_HISTORY = 10
CONTEXT_TIMEOUT_HOURS = 1


def _get_sb():
    """Get Supabase client or None."""
    try:
        from config import settings

        if not settings.supabase_url or not settings.supabase_key:
            return None
        from supabase import create_client

        return create_client(settings.supabase_url, settings.supabase_key)
    except Exception:
        return None


def _default_context(chat_id: int = 0) -> dict:
    """Empty context dict."""
    return {
        "chat_id": chat_id,
        "current_campaign_id": None,
        "state": "idle",
        "history": [],
        "last_interaction": datetime.now(UTC).isoformat(),
    }


async def get_context(chat_id: int) -> dict:
    """Load context from Supabase. Returns default if missing/error."""
    sb = _get_sb()
    if not sb:
        return _default_context(chat_id)
    try:
        r = sb.table("chat_context").select("*").eq("chat_id", chat_id).execute()
        if r.data:
            ctx = r.data[0]
            ctx["chat_id"] = chat_id
            if isinstance(ctx.get("history"), str):
                ctx["history"] = json.loads(ctx["history"])
            last = ctx.get("last_interaction", "")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if datetime.now(UTC) - last_dt > timedelta(hours=CONTEXT_TIMEOUT_HOURS):
                        log.info("Context stale (%s), clearing", chat_id)
                        await clear_context(chat_id)
                        return _default_context(chat_id)
                except Exception:
                    pass
            return ctx
        return _default_context(chat_id)
    except Exception as e:
        log.warning("Get context failed: %s", e)
        return _default_context(chat_id)


async def update_context(chat_id: int, **kwargs) -> None:
    """Upsert context fields."""
    sb = _get_sb()
    if not sb:
        return
    try:
        data = {
            "chat_id": chat_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "last_interaction": datetime.now(UTC).isoformat(),
            **kwargs,
        }
        if "history" in data and isinstance(data["history"], list):
            data["history"] = json.dumps(data["history"])
        sb.table("chat_context").upsert(data).execute()
    except Exception as e:
        log.warning("Update context failed: %s", e)


async def add_message(chat_id: int, role: str, text: str) -> None:
    """Append message to history (keep last MAX_HISTORY)."""
    ctx = await get_context(chat_id)
    history = ctx.get("history", [])
    if isinstance(history, str):
        history = json.loads(history)
    history.append({"role": role, "text": text[:500]})
    history = history[-MAX_HISTORY:]
    await update_context(chat_id, history=history)


async def clear_context(chat_id: int) -> None:
    """Reset to idle."""
    await update_context(
        chat_id,
        current_campaign_id=None,
        state="idle",
        history=json.dumps([]),
    )
