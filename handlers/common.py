"""Handlers: /start, /status, admin check."""
import logging

log = logging.getLogger("bbc.handlers.common")


def is_admin(user_id: int) -> bool:
    """Admin whitelist check."""
    from config import settings

    return user_id in settings.admin_ids


async def handle_start(chat_id: int):
    from services.telegram_client import send_message

    await send_message(
        chat_id=chat_id,
        text=(
            "🏢 *BBC Marketing Agent*\n\n"
            "*Weekly auto flow:*\n"
            "📡 Fri 14:00 — discover premium events\n"
            "📱 Review here with ✅ ❌ ✏️ 🔄\n"
            "📤 Mon 10:00 — broadcast approved deals\n\n"
            "*Urgent posting:*\n"
            "Send any destination or event name\n"
            "→ instant branded deal + post\n\n"
            "Commands: /status  /help"
        ),
    )


async def handle_status(chat_id: int):
    from config import settings
    from services.telegram_client import send_message

    lines = ["📊 *BBC Marketing Agent*\n"]
    lines.append(f"Gemini: {'✅' if settings.gemini_api_key else '❌'}")
    lines.append(f"Supabase: {'✅' if settings.supabase_url else '❌'}")
    lines.append(f"Sheets: {'✅' if settings.google_sheets_id else '❌'}")
    lines.append(f"Twilio: {'✅' if settings.twilio_account_sid else '❌'}")

    if settings.supabase_url and settings.supabase_key:
        try:
            from supabase import create_client

            sb = create_client(settings.supabase_url, settings.supabase_key)
            r = (
                sb.table("campaigns")
                .select("campaign_id,event_name,status")
                .order("created_at", desc=True)
                .limit(8)
                .execute()
            )
            if r.data:
                emojis = {
                    "draft": "📝",
                    "approved": "✅",
                    "rejected": "❌",
                    "sent": "📤",
                    "failed": "💥",
                    "editing": "✏️",
                    "urgent_preview": "⚡",
                    "cancelled": "🚫",
                }
                lines.append("\n📋 *Recent deals:*")
                for c in r.data:
                    e = emojis.get(c.get("status", ""), "❓")
                    name = (c.get("event_name") or "?")[:28]
                    lines.append(f"  {e} {name} — _{c.get('status')}_")
            else:
                lines.append("\n_No campaigns yet._")
        except Exception as ex:
            lines.append(f"\n⚠️ DB: {ex}")

    await send_message(chat_id=chat_id, text="\n".join(lines))
