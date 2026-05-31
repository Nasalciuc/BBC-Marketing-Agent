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


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """
    Primește callbacks de la Telegram (approve/reject).
    Telegram trimite POST cu Update JSON când Scaler apasă un buton.
    """
    try:
        from config import settings

        expected_secret = settings.telegram_webhook_secret
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

        if expected_secret and received_secret != expected_secret:
            log.warning("Invalid Telegram webhook secret")
            return {"ok": False, "error": "invalid secret"}
    except Exception:
        pass

    try:
        data = await request.json()
        log.info("Telegram update received: %s", list(data.keys()))
    except Exception as e:
        log.error("Failed to parse Telegram update: %s", e)
        return {"ok": False, "error": "invalid json"}

    if "callback_query" in data:
        callback = data["callback_query"]
        callback_data = callback.get("data", "")
        callback_id = callback.get("id", "")
        user = callback.get("from", {}).get("first_name", "Unknown")

        log.info("Callback from %s: %s", user, callback_data)

        if "_" in callback_data:
            action, campaign_id = callback_data.split("_", 1)

            if action == "approve":
                log.info("Deal APPROVED: %s by %s", campaign_id, user)
                await _handle_approve(campaign_id, callback_id)
            elif action == "reject":
                log.info("Deal REJECTED: %s by %s", campaign_id, user)
                await _handle_reject(campaign_id, callback_id)
            else:
                log.warning("Unknown action: %s", action)
        else:
            log.warning("Unparseable callback_data: %s", callback_data)

    elif "message" in data:
        message = data["message"]
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id", "")

        log.info("Message from %s: %s", chat_id, text)

        if text == "/start":
            await _send_telegram_message(
                chat_id,
                "🏢 *BBC Marketing Agent*\n\n"
                "I'll send you deal previews for approval.\n"
                "Use the ✅/❌ buttons to approve or reject.",
            )
        elif text == "/status":
            await _send_telegram_message(chat_id, await _get_status_text())

    return {"ok": True}


async def _handle_approve(campaign_id: str, callback_id: str):
    """Procesează aprobarea unui deal."""
    try:
        await _answer_callback(callback_id, f"✅ Approved: {campaign_id}")
        log.info("Deal %s approved — status update pending implementation", campaign_id)
    except Exception as e:
        log.error("Error handling approve: %s", e)


async def _handle_reject(campaign_id: str, callback_id: str):
    """Procesează respingerea unui deal."""
    try:
        await _answer_callback(callback_id, f"❌ Rejected: {campaign_id}")
        log.info("Deal %s rejected — status update pending implementation", campaign_id)
    except Exception as e:
        log.error("Error handling reject: %s", e)


async def _answer_callback(callback_id: str, text: str):
    """Răspunde la Telegram callback query."""
    try:
        import httpx

        from config import settings

        if not settings.telegram_bot_token:
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
            )
    except Exception as e:
        log.error("Failed to answer callback: %s", e)


async def _send_telegram_message(chat_id, text: str):
    """Trimite mesaj pe Telegram."""
    try:
        import httpx

        from config import settings

        if not settings.telegram_bot_token:
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception as e:
        log.error("Failed to send message: %s", e)


async def _get_status_text() -> str:
    """Generează text status pentru /status command."""
    lines = ["📊 *BBC Marketing Agent Status*\n"]
    try:
        from config import settings

        lines.append(f"🔑 Gemini: {'✅' if settings.gemini_api_key else '❌'}")
        lines.append(f"🗄️ Supabase: {'✅' if settings.supabase_url else '❌'}")
        lines.append(f"📋 Sheets: {'✅' if settings.google_sheets_id else '❌'}")
        lines.append(f"📱 Telegram: {'✅' if settings.telegram_bot_token else '❌'}")
    except Exception:
        lines.append("⚠️ Config error")
    return "\n".join(lines)
