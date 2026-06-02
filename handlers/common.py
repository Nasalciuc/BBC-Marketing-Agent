"""Handlers: /start, /status, /deals, /help + admin check."""
import logging

from config import settings

log = logging.getLogger("bbc.handlers.common")


def is_admin(user_id: int) -> bool:
    """Admin whitelist check."""
    return user_id in settings.admin_ids


async def handle_start(chat_id: int):
    """Main menu — branded, cu butoane inline."""
    from keyboards import main_menu_keyboard
    from services.telegram_client import send_message

    text = (
        "✈️ *BuyBusinessClass.com*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Premium Flight Deals Agent\n"
        "\n"
        "I find the best business class deals\n"
        "to F1, Wimbledon, Fashion Week & more.\n"
        "\n"
        "You review → approve → deals go out.\n"
        "\n"
        "Or just tell me what you need 👇"
    )
    await send_message(chat_id=chat_id, text=text, reply_markup=main_menu_keyboard())


async def handle_help(chat_id: int):
    """Help detaliat."""
    from keyboards import nav_keyboard
    from services.telegram_client import send_message

    text = (
        "📖 *Commands & Features*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🔹 Just type any destination or event:\n"
        "    `Monaco F1 JFK`\n"
        "    `Wimbledon London`\n"
        "    `Paris fashion week`\n"
        "\n"
        "🔹 Or use commands:\n"
        "    /start — Main menu\n"
        "    /status — System health\n"
        "    /deals — Recent campaigns\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📅 *Weekly Flow:*\n"
        "  Fri 14:00 → I discover events\n"
        "  You get previews with ✅ ❌ ✏️ 🔄\n"
        "  Mon 10:00 → Approved deals broadcast\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *Urgent:* Send any text → instant deal"
    )
    await send_message(chat_id=chat_id, text=text, reply_markup=nav_keyboard("cmd_help"))


async def handle_status(chat_id: int):
    """Status detaliat cu stats."""
    from keyboards import nav_keyboard
    from services.telegram_client import send_message

    lines = [
        "📊 *System Status*",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        "*Services:*",
    ]

    lines.append(f"  {'✅' if settings.anthropic_api_key else '❌'} Claude AI (text)")
    lines.append(f"  {'✅' if settings.gemini_api_key else '❌'} Gemini AI (images)")
    lines.append(f"  {'✅' if settings.supabase_url else '❌'} Supabase DB")
    lines.append(f"  {'✅' if settings.google_sheets_id else '❌'} Google Sheets")
    lines.append(f"  {'✅' if settings.twilio_account_sid else '⬜'} Twilio WhatsApp")
    lines.append(f"  {'✅' if settings.hcti_api_key else '⬜'} HCTI Branding")

    if settings.supabase_url and settings.supabase_key:
        try:
            from supabase import create_client

            sb = create_client(settings.supabase_url, settings.supabase_key)

            statuses = {}
            for s in ["draft", "approved", "rejected", "sent", "failed", "urgent_preview"]:
                try:
                    r = (
                        sb.table("campaigns")
                        .select("campaign_id", count="exact")
                        .eq("status", s)
                        .execute()
                    )
                    if r.count and r.count > 0:
                        statuses[s] = r.count
                except Exception:
                    pass

            if statuses:
                emoji_map = {
                    "draft": "📝",
                    "approved": "✅",
                    "rejected": "❌",
                    "sent": "📤",
                    "failed": "💥",
                    "urgent_preview": "⚡",
                }
                lines.extend(["", "*Campaigns:*"])
                for s, count in statuses.items():
                    lines.append(f"  {emoji_map.get(s, '❓')} {s}: *{count}*")
                lines.append("  ─────────")
                lines.append(f"  Total: *{sum(statuses.values())}*")
        except Exception as e:
            lines.append(f"\n⚠️ DB: {e}")

    lines.extend(["", "━━━━━━━━━━━━━━━━━━━━━", "🕐 Railway: *Online*"])

    await send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=nav_keyboard("cmd_status"))


async def handle_deals(chat_id: int):
    """Ultimele 10 deal-uri cu status."""
    from keyboards import nav_keyboard
    from services.telegram_client import send_message

    lines = ["📋 *Recent Deals*", "━━━━━━━━━━━━━━━━━━━━━", ""]

    if settings.supabase_url and settings.supabase_key:
        try:
            from supabase import create_client

            sb = create_client(settings.supabase_url, settings.supabase_key)
            r = (
                sb.table("campaigns")
                .select("campaign_id,event_name,route,price,status,created_at")
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )

            if r.data:
                emoji_map = {
                    "draft": "📝",
                    "approved": "✅",
                    "rejected": "❌",
                    "sent": "📤",
                    "failed": "💥",
                    "urgent_preview": "⚡",
                    "editing": "✏️",
                    "cancelled": "🚫",
                }
                for c in r.data:
                    e = emoji_map.get(c.get("status", ""), "❓")
                    name = (c.get("event_name") or "—")[:25]
                    route = c.get("route", "")
                    price = c.get("price", "")
                    date = (c.get("created_at") or "")[:10]
                    lines.append(f"{e} *{name}*")
                    if route and price:
                        lines.append(f"    {route} · {price}")
                    lines.append(f"    _{c.get('status', '?')}_ · {date}")
                    lines.append("")
            else:
                lines.extend(["_No campaigns yet._", "", "Send a destination like `Monaco F1 JFK`"])
        except Exception as e:
            lines.append(f"⚠️ {e}")
    else:
        lines.append("⬜ Database not configured")

    await send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=nav_keyboard("cmd_deals"))


async def handle_urgent_prompt(chat_id: int):
    """Instrucțiuni pentru urgent deal."""
    from keyboards import nav_keyboard
    from services.telegram_client import send_message

    text = (
        "⚡ *Create Urgent Deal*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Just type a destination or event:\n"
        "\n"
        "  `Monaco F1 JFK`\n"
        "  `Wimbledon London`\n"
        "  `JFK to Paris fashion week`\n"
        "\n"
        "I'll create a branded deal in ~30 sec.\n"
        "\n"
        "_Type below_ 👇"
    )
    await send_message(chat_id=chat_id, text=text, reply_markup=nav_keyboard("cmd_urgent"))
