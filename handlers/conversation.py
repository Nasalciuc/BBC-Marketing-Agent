"""
BBC AI Agent — conversație naturală cu directorul.
Claude clasifică intent → dispatch la handler-ul potrivit.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bbc.conversation")


async def handle_ai_message(chat_id: int, user_id: int, text: str):
    """Entry point pentru text liber (non-command, non-callback)."""
    from services.anthropic_client import classify_intent, rewrite_text
    from services.context_manager import add_message, clear_context, get_context, update_context
    from services.telegram_client import send_message

    context = await get_context(chat_id)
    await add_message(chat_id, "user", text)

    result = await classify_intent(text, context)
    intent = result.get("intent", "UNCLEAR")
    confidence = result.get("confidence", 0.0)
    entities = result.get("entities", {})
    response_text = result.get("response", "")

    if confidence < 0.6 or intent == "UNCLEAR":
        clarification = response_text or (
            "🤔 Nu am înțeles exact. Poți:\n"
            "• Scrie o destinație/eveniment (ex: `Monaco F1 JFK`)\n"
            "• Scrie `da` sau `nu` pentru deal-ul curent\n"
            "• Apasă un buton din meniu"
        )
        await send_message(chat_id=chat_id, text=clarification)
        await add_message(chat_id, "assistant", clarification)
        return

    if response_text and intent not in ("GREETING", "STATUS", "LIST_DEALS", "HELP"):
        await send_message(chat_id=chat_id, text=response_text)
        await add_message(chat_id, "assistant", response_text)

    if intent == "GENERATE_CONTENT":
        from services.telegram_client import send_message as _sm

        await _sm(
            chat_id=chat_id,
            text="🤖 *Generating this week's content...*\n\nSearching the web — takes 2-3 minutes. Each post arrives with Approve/Reject buttons.",
        )
        import asyncio as _asyncio

        from scripts.autonomous_agent import run_autonomous

        _asyncio.create_task(run_autonomous(count=5))
        return

    if intent == "ANALYZE":
        import asyncio as _asyncio

        from services.reasoning_agent import run_reasoning_loop

        _asyncio.create_task(run_reasoning_loop(text, chat_id))
        return

    if intent == "CREATE_DEAL":
        event_name = entities.get("event_name") or text
        from_iata = entities.get("from_iata") or "JFK"
        query = f"{from_iata} {event_name}" if from_iata != "JFK" else event_name
        from handlers.urgent import handle_urgent_request

        await handle_urgent_request(chat_id, query)
        await update_context(chat_id, state="preview")

    elif intent == "APPROVE":
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            from services.sheets_client import update_deal_status
            from services.supabase_client import update_campaign_status

            update_deal_status(campaign_id, "approved")
            await update_campaign_status(campaign_id, "approved")
            await send_message(chat_id=chat_id, text=f"✅ Approved *{campaign_id}*")
            await clear_context(chat_id)
        else:
            await send_message(chat_id=chat_id, text="✅ Ce deal aprobi? Trimite /deals.")

    elif intent == "REJECT":
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            from services.sheets_client import update_deal_status
            from services.supabase_client import update_campaign_status

            update_deal_status(campaign_id, "rejected")
            await update_campaign_status(campaign_id, "rejected")
            await send_message(chat_id=chat_id, text=f"❌ Rejected *{campaign_id}*")
            await clear_context(chat_id)
        else:
            await send_message(chat_id=chat_id, text="❌ Ce deal respingi? Trimite /deals.")

    elif intent == "POST_NOW":
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            from keyboards import urgent_keyboard

            await send_message(
                chat_id=chat_id,
                text=(
                    f"📤 Ready to broadcast *{campaign_id}*?\n"
                    "Apasă butonul pentru confirmare:"
                ),
                reply_markup=urgent_keyboard(campaign_id),
            )
        else:
            await send_message(chat_id=chat_id, text="📤 Ce deal postezi? Trimite /deals.")

    elif intent == "SCHEDULE":
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            from services.sheets_client import update_deal_status
            from services.supabase_client import update_campaign_status

            update_deal_status(campaign_id, "approved")
            await update_campaign_status(campaign_id, "approved")
            await send_message(
                chat_id=chat_id, text=f"⏰ *{campaign_id}* → Monday 10:00 UTC"
            )
            await clear_context(chat_id)
        else:
            await send_message(chat_id=chat_id, text="⏰ Ce deal programezi? Trimite /deals.")

    elif intent in ("EDIT_CAPTION", "CAPTION_UPDATE"):
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            if intent == "CAPTION_UPDATE" and context.get("state") == "editing":
                from handlers.review import handle_caption_reply

                new_text = entities.get("new_caption") or text
                await handle_caption_reply(campaign_id, new_text, chat_id)
                await update_context(chat_id, state="preview")
            else:
                current = ""
                try:
                    from config import settings
                    from supabase import create_client

                    sb = create_client(settings.supabase_url, settings.supabase_key)
                    r = (
                        sb.table("campaigns")
                        .select("caption,whatsapp_caption")
                        .eq("campaign_id", campaign_id)
                        .execute()
                    )
                    if r.data:
                        current = r.data[0].get("whatsapp_caption") or r.data[0].get("caption") or ""
                except Exception:
                    pass

                if current:
                    new_caption = await rewrite_text(current, text)
                    await send_message(
                        chat_id=chat_id, text=f"✅ New caption:\n\n{new_caption}"
                    )
                    try:
                        from config import settings
                        from supabase import create_client

                        sb = create_client(settings.supabase_url, settings.supabase_key)
                        sb.table("campaigns").update({"whatsapp_caption": new_caption}).eq(
                            "campaign_id", campaign_id
                        ).execute()
                    except Exception:
                        pass
                else:
                    await update_context(chat_id, state="editing")
                    await send_message(
                        chat_id=chat_id,
                        text=f"✏️ Trimite noul caption pentru *{campaign_id}*:",
                    )
        else:
            await send_message(chat_id=chat_id, text="✏️ Ce deal editezi? Trimite /deals.")

    elif intent == "REGENERATE":
        campaign_id = context.get("current_campaign_id")
        if campaign_id:
            from handlers.review import handle_regenerate

            await handle_regenerate(campaign_id, "", chat_id)
        else:
            await send_message(chat_id=chat_id, text="🔄 Ce deal regenerezi? Trimite /deals.")

    elif intent == "STATUS":
        from handlers.common import handle_status

        await handle_status(chat_id)

    elif intent == "LIST_DEALS":
        from handlers.common import handle_deals

        await handle_deals(chat_id)

    elif intent == "HELP":
        from handlers.common import handle_help

        await handle_help(chat_id)

    elif intent == "GREETING":
        from handlers.common import handle_start

        await handle_start(chat_id)

    elif intent == "CANCEL":
        await clear_context(chat_id)
        await send_message(chat_id=chat_id, text="✅ Cancelled.")
        from handlers.common import handle_start

        await handle_start(chat_id)
