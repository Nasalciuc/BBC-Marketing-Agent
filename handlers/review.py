"""Review flow: approve, reject, edit caption, regenerate image."""
import asyncio
import logging
import os
import tempfile

log = logging.getLogger("bbc.handlers.review")


async def handle_approve(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import approved_keyboard
    from services.sheets_client import update_deal_status
    from services.supabase_client import update_campaign_status
    from services.telegram_client import answer_callback_query, edit_message_reply_markup

    log.info("Approved: %s", campaign_id)
    update_deal_status(campaign_id, "approved")
    await update_campaign_status(campaign_id, "approved")
    await answer_callback_query(callback_id, "✅ Approved")
    await edit_message_reply_markup(chat_id, message_id, approved_keyboard(campaign_id))


async def handle_reject(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import rejected_keyboard
    from services.sheets_client import update_deal_status
    from services.supabase_client import update_campaign_status
    from services.telegram_client import answer_callback_query, edit_message_reply_markup

    log.info("Rejected: %s", campaign_id)
    update_deal_status(campaign_id, "rejected")
    await update_campaign_status(campaign_id, "rejected")
    await answer_callback_query(callback_id, "❌ Rejected")
    await edit_message_reply_markup(chat_id, message_id, rejected_keyboard())


async def handle_edit_caption(campaign_id: str, callback_id: str, chat_id: int):
    from services.telegram_client import answer_callback_query, send_message

    try:
        from config import settings
        from supabase import create_client

        if settings.supabase_url and settings.supabase_key:
            sb = create_client(settings.supabase_url, settings.supabase_key)
            sb.table("campaigns").update({"status": "editing"}).eq(
                "campaign_id", campaign_id
            ).execute()
    except Exception as e:
        log.error("Edit flag: %s", e)

    await answer_callback_query(callback_id, "✏️ Send new caption")
    await send_message(
        chat_id=chat_id,
        text=f"✏️ Send new caption for *{campaign_id}*\nOr /cancel to keep original.",
    )


async def handle_caption_reply(campaign_id: str, new_caption: str, chat_id: int):
    from services.telegram_client import send_message

    try:
        from config import settings
        from supabase import create_client

        sb = create_client(settings.supabase_url, settings.supabase_key)
        sb.table("campaigns").update(
            {
                "caption_override": new_caption,
                "status": "draft",
            }
        ).eq("campaign_id", campaign_id).execute()
        await send_message(chat_id=chat_id, text=f"✅ Caption updated for *{campaign_id}*.")
    except Exception as e:
        log.error("Caption update: %s", e)
        await send_message(chat_id=chat_id, text=f"❌ Error: {e}")


async def handle_regenerate(campaign_id: str, callback_id: str, chat_id: int):
    from services.branding_engine import generate_branded_image
    from services.supabase_client import upload_image
    from services.telegram_client import answer_callback_query, send_approval_request, send_message

    await answer_callback_query(callback_id, "🔄 Regenerating...")
    await send_message(chat_id=chat_id, text=f"🔄 Regenerating *{campaign_id}*...")

    try:
        from config import settings
        from supabase import create_client

        sb = create_client(settings.supabase_url, settings.supabase_key)
        r = sb.table("campaigns").select("*").eq("campaign_id", campaign_id).execute()
        if not r.data:
            await send_message(chat_id=chat_id, text=f"❌ Not found: {campaign_id}")
            return

        campaign = r.data[0]
        event_name = campaign.get("event_name", "")

        bg_bytes = None
        try:
            from services.gemini_client import generate_event_image

            bg_bytes = await generate_event_image(
                f"Professional photo of {event_name}, cinematic, no text"
            )
        except Exception:
            log.warning("Gemini image gen failed")

        bg_path = "assets/defaults/default_background.jpg"
        if bg_bytes:
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(bg_bytes)
            tmp.close()
            bg_path = tmp.name

        try:
            branded = await asyncio.to_thread(
                generate_branded_image,
                event_name,
                campaign.get("route", "JFK → LHR"),
                campaign.get("price", "$2,500"),
                bg_path,
            )
        finally:
            if bg_bytes and bg_path != "assets/defaults/default_background.jpg":
                os.unlink(bg_path)

        image_url = await upload_image(branded, f"deals/{campaign_id}/regen.jpg")
        if image_url:
            sb.table("campaigns").update({"image_url": image_url}).eq(
                "campaign_id", campaign_id
            ).execute()

        event = {
            **campaign,
            "name": event_name,
            "campaign_id": campaign_id,
            "image_url": image_url,
        }
        await send_approval_request(event, chat_id=chat_id)
        await send_message(chat_id=chat_id, text="✅ New image ready!")

    except Exception as e:
        log.error("Regen failed: %s", e, exc_info=True)
        await send_message(chat_id=chat_id, text=f"❌ Error: `{e}`")
