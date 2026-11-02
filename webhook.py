"""
BBC Marketing Agent — Telegram Webhook Handler.
FastAPI minimal care:
1. Răspunde la /health (Railway healthcheck)
2. Primește callbacks Telegram (approve/reject deals)
3. Pornește MEREU, chiar dacă nu toate keys sunt setate
"""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs each request URL at INFO, and a Telegram URL contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("bbc.webhook")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # a redeploy: let the assistant finish the messages it already took (Railway waits before killing)
    import sys

    router = sys.modules.get("content_system.assistant.router")
    if router is not None:
        try:
            await asyncio.wait_for(router.drain(), timeout=25)
        except TimeoutError:
            log.warning("shutdown: assistant messages still running after 25 s")


app = FastAPI(
    title="BBC Marketing Agent",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Railway healthcheck — MEREU returnează ok."""
    status = {"status": "ok", "service": "bbc-marketing-webhook"}

    try:
        from config import settings

        status["gemini"] = bool(settings.gemini_api_key)
        status["anthropic"] = bool(settings.anthropic_api_key)
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


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram webhook — dispatch la handlers."""
    from config import settings

    expected = settings.telegram_webhook_secret
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected:
        log.error("TELEGRAM_WEBHOOK_SECRET is not set: refusing all Telegram updates")
        return JSONResponse({"ok": False}, status_code=503)
    if not hmac.compare_digest(received.encode(), expected.encode()):
        log.warning("Invalid Telegram webhook secret")
        return JSONResponse({"ok": False}, status_code=401)

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

        if callback_data.startswith("cs:"):
            from content_system.bridge import handle_callback

            await handle_callback(cb, data.get("update_id"))
            return {"ok": True}

        if callback_data.startswith("ca:"):
            from content_system.settings import get_settings as content_settings

            if content_settings().assistant_on:
                from content_system.assistant.router import route_card

                await route_card(cb, data.get("update_id"))
            else:
                from services.telegram_client import answer_callback_query

                await answer_callback_query(callback_id, "The assistant is off (CONTENT_ASSISTANT_ENABLED=false)")
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
            from content_system.settings import get_settings as content_settings

            if content_settings().assistant_on:
                # the old flow waits for the next text, which now goes to the assistant: don't start it
                from services.telegram_client import answer_callback_query

                await answer_callback_query(callback_id, "✏️ Scrie-mi ce schimb în text (răspunde la postare)")
            else:
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
        elif action == "cmd":
            cmd = campaign_id
            from services.telegram_client import answer_callback_query

            await answer_callback_query(callback_id)

            if cmd == "start":
                from handlers.common import handle_start

                await handle_start(chat_id)
            elif cmd == "status":
                from handlers.common import handle_status

                await handle_status(chat_id)
            elif cmd == "deals":
                from handlers.common import handle_deals

                await handle_deals(chat_id)
            elif cmd == "urgent":
                from handlers.common import handle_urgent_prompt

                await handle_urgent_prompt(chat_id)
            elif cmd == "help":
                from handlers.common import handle_help

                await handle_help(chat_id)
        else:
            log.warning("Unknown callback: %s", callback_data)

    elif "message" in data:
        msg = data["message"]
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id", 0)
        user_id = msg.get("from", {}).get("id", 0)

        from content_system.settings import get_settings as content_settings
        from handlers.common import is_admin

        if not is_admin(user_id):
            from services.telegram_client import send_message

            denied = "⛔ Access denied."
            if content_settings().assistant_on and text.startswith("/start"):
                # so a new reviewer can send Scaler the id that goes into TELEGRAM_ADMIN_IDS
                denied += f"\nYour Telegram ID is {user_id} — send it to Scaler to get access."
            await send_message(chat_id=chat_id, text=denied)
            return {"ok": True}

        if content_settings().assistant_on:
            from content_system.assistant.router import route_message

            if await route_message(msg, data.get("update_id")):
                return {"ok": True}
        elif msg.get("reply_to_message") and content_settings().enabled:
            from content_system.bridge import handle_reply

            if await handle_reply(msg, data.get("update_id")):
                return {"ok": True}

        if text.startswith("/start"):
            from handlers.common import handle_start

            await handle_start(chat_id)
        elif text.startswith("/help"):
            from handlers.common import handle_help

            await handle_help(chat_id)
        elif text.startswith("/status"):
            from handlers.common import handle_status

            await handle_status(chat_id)
        elif text.startswith("/deals"):
            from handlers.common import handle_deals

            await handle_deals(chat_id)
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
                from handlers.conversation import handle_ai_message

                await handle_ai_message(chat_id, user_id, text)

    return {"ok": True}
