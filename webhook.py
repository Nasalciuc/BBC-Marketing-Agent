"""
BBC Marketing Agent — Telegram Webhook Handler.
FastAPI minimal care:
1. Răspunde la /health (Railway healthcheck)
2. Primește callbacks Telegram (approve/reject deals)
3. Pornește MEREU, chiar dacă nu toate keys sunt setate
"""
import logging

from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bbc.webhook")

app = FastAPI(title="BBC Marketing Agent", version="1.0.0")


@app.get("/health")
async def health():
    """Railway healthcheck — MEREU returnează ok."""
    status = {"status": "ok", "service": "bbc-marketing-webhook"}

    try:
        from config import settings

        status["gemini"] = bool(settings.gemini_api_key)
        status["supabase"] = bool(settings.supabase_url and settings.supabase_key)
        status["telegram"] = bool(settings.telegram_bot_token)
        status["sheets"] = bool(settings.google_sheets_id)
    except Exception:
        status["config"] = "error loading"

    return status


@app.get("/")
async def root():
    """Root endpoint."""
    return {"service": "BBC Marketing Agent", "status": "running"}


@app.get("/debug/gemini")
async def debug_gemini(full: bool = False):
    """Test Gemini API (folosește GEMINI_API_KEY de pe Railway)."""
    import asyncio

    from config import settings

    if not settings.gemini_api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set"}

    if not full:
        def _ping():
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            r = client.models.generate_content(
                model=settings.gemini_model,
                contents="Reply with exactly: GEMINI_OK",
            )
            return (r.text or "").strip()[:200]

        try:
            text = await asyncio.to_thread(_ping)
            return {"ok": True, "mode": "ping", "model": settings.gemini_model, "text": text}
        except Exception as e:
            log.error("Gemini ping failed: %s", e)
            return {"ok": False, "error": str(e)}

    from services.gemini_client import discover_events

    try:
        events = await discover_events(week_offset=1)
        return {
            "ok": True,
            "mode": "discovery",
            "events_count": len(events),
            "events": events[:3],
        }
    except Exception as e:
        log.error("Gemini discovery failed: %s", e)
        return {"ok": False, "error": str(e)}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram webhook — dispatch la handlers."""
    try:
        from config import settings

        expected = settings.telegram_webhook_secret
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if expected and received != expected:
            log.warning("Invalid Telegram webhook secret")
            return {"ok": False}
    except Exception:
        pass

    try:
        data = await request.json()
        log.info("Telegram update received: %s", list(data.keys()))
    except Exception as e:
        log.error("Failed to parse Telegram update: %s", e)
        return {"ok": False}

    if "callback_query" in data:
        cb = data["callback_query"]
        callback_id = cb.get("id", "")
        callback_data = cb.get("data", "")
        user_id = cb.get("from", {}).get("id", 0)
        chat_id = cb.get("message", {}).get("chat", {}).get("id", 0)
        message_id = cb.get("message", {}).get("message_id", 0)

        from handlers.common import is_admin

        if not is_admin(user_id):
            from services.telegram_client import answer_callback_query

            await answer_callback_query(callback_id, "⛔ Access denied")
            return {"ok": True}

        if callback_data == "noop":
            from services.telegram_client import answer_callback_query

            await answer_callback_query(callback_id)
            return {"ok": True}

        from keyboards import parse_callback

        action, campaign_id = parse_callback(callback_data)
        log.info("Callback action=%s campaign=%s user=%s", action, campaign_id, user_id)

        if action == "approve":
            from handlers.review import handle_approve

            await handle_approve(campaign_id, callback_id, chat_id, message_id)
        elif action == "reject":
            from handlers.review import handle_reject

            await handle_reject(campaign_id, callback_id, chat_id, message_id)
        elif action == "edit":
            from handlers.review import handle_edit_caption

            await handle_edit_caption(campaign_id, callback_id, chat_id)
        elif action == "regen":
            from handlers.review import handle_regenerate

            await handle_regenerate(campaign_id, callback_id, chat_id)
        elif action == "postnow":
            from handlers.urgent import handle_post_now

            await handle_post_now(campaign_id, callback_id, chat_id, message_id)
        elif action == "schedule":
            from handlers.urgent import handle_schedule

            await handle_schedule(campaign_id, callback_id, chat_id, message_id)
        elif action == "cancel":
            from services.telegram_client import answer_callback_query

            try:
                from services.supabase_client import update_campaign_status

                await update_campaign_status(campaign_id, "cancelled")
            except Exception:
                pass
            await answer_callback_query(callback_id, "❌ Cancelled")
        else:
            log.warning("Unknown callback: %s", callback_data)

    elif "message" in data:
        msg = data["message"]
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id = msg.get("from", {}).get("id", 0)

        from handlers.common import is_admin

        if not is_admin(user_id):
            from services.telegram_client import send_message

            await send_message(chat_id=chat_id, text="⛔ Access denied.")
            return {"ok": True}

        if text.startswith("/start") or text.startswith("/help"):
            from handlers.common import handle_start

            await handle_start(chat_id)
        elif text.startswith("/status"):
            from handlers.common import handle_status

            await handle_status(chat_id)
        elif text.startswith("/cancel"):
            from services.telegram_client import send_message

            try:
                from config import settings as s

                if s.supabase_url and s.supabase_key:
                    from supabase import create_client

                    sb = create_client(s.supabase_url, s.supabase_key)
                    sb.table("campaigns").update({"status": "draft"}).eq(
                        "status", "editing"
                    ).execute()
            except Exception:
                pass
            await send_message(chat_id=chat_id, text="✅ Cancelled.")
        elif text and not text.startswith("/"):
            editing_id = None
            try:
                from config import settings as s

                if s.supabase_url and s.supabase_key:
                    from supabase import create_client

                    sb = create_client(s.supabase_url, s.supabase_key)
                    r = (
                        sb.table("campaigns")
                        .select("campaign_id")
                        .eq("status", "editing")
                        .limit(1)
                        .execute()
                    )
                    if r.data:
                        editing_id = r.data[0]["campaign_id"]
            except Exception:
                pass

            if editing_id:
                from handlers.review import handle_caption_reply

                await handle_caption_reply(editing_id, text, chat_id)
            else:
                from handlers.urgent import handle_urgent_request

                await handle_urgent_request(chat_id, text)

    return {"ok": True}
