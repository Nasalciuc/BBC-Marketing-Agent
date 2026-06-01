"""Urgent posting: admin text → Gemini parse → price → brand → preview → post."""
import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime

log = logging.getLogger("bbc.handlers.urgent")


async def handle_urgent_request(chat_id: int, text: str):
    from keyboards import urgent_keyboard
    from services.telegram_client import send_message, send_photo

    await send_message(chat_id=chat_id, text=f"🔄 Processing: _{text}_")

    try:
        from config import settings

        if not settings.gemini_api_key:
            await send_message(chat_id=chat_id, text="❌ GEMINI_API_KEY not configured.")
            return

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                f'Parse travel request to JSON only:\n"{text}"\n\n'
                'Return ONLY: {"event_name":"...","from_iata":"JFK","to_iata":"LHR",'
                '"city":"London, UK","category":"travel","image_prompt":"cinematic photo description"}'
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        deal = json.loads(resp.text)
        from_iata = deal.get("from_iata", "JFK")
        to_iata = deal.get("to_iata", "LHR")
        event_name = deal.get("event_name", text[:40])

        from services.pricing_engine import calculate_price, format_price

        price_raw = calculate_price(from_iata, to_iata, "round_trip", "business")
        price_str = format_price(price_raw) if price_raw else "$2,500"

        campaign_id = f"URG-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}"

        bg_bytes = None
        try:
            from services.gemini_client import generate_event_image

            bg_bytes = await generate_event_image(
                deal.get("image_prompt", f"Beautiful {deal.get('city', '')}")
            )
        except Exception:
            log.warning("Gemini image failed — default bg")

        from services.branding_engine import generate_branded_image

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
                f"{from_iata} → {to_iata}",
                price_str,
                bg_path,
            )
        finally:
            if bg_bytes and bg_path != "assets/defaults/default_background.jpg":
                os.unlink(bg_path)

        image_url = None
        try:
            from services.supabase_client import save_campaign, upload_image

            image_url = await upload_image(branded, f"deals/{campaign_id}/landscape.jpg")
            await save_campaign(
                {
                    "campaign_id": campaign_id,
                    "name": event_name,
                    "event_name": event_name,
                    "city": deal.get("city", ""),
                    "category": deal.get("category", "travel"),
                    "route_str": f"{from_iata} → {to_iata}",
                    "route": f"{from_iata} → {to_iata}",
                    "price": price_str,
                    "price_raw": price_raw,
                    "image_url": image_url,
                    "status": "urgent_preview",
                }
            )
        except Exception as e:
            log.warning("Supabase: %s", e)

        caption = (
            f"⚡ *URGENT — {event_name}*\n"
            f"✈️ {from_iata} → {to_iata}\n"
            f"💰 *{price_str}* business RT"
        )
        if image_url:
            await send_photo(
                chat_id=chat_id,
                photo_url=image_url,
                caption=caption,
                reply_markup=urgent_keyboard(campaign_id),
            )
        else:
            await send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=urgent_keyboard(campaign_id),
            )

    except Exception as e:
        log.error("Urgent failed: %s", e, exc_info=True)
        await send_message(chat_id=chat_id, text=f"❌ Error: `{e}`")


async def handle_post_now(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import posted_keyboard
    from services.supabase_client import update_campaign_status
    from services.telegram_client import (
        answer_callback_query,
        edit_message_reply_markup,
        send_message,
    )

    await answer_callback_query(callback_id, "📤 Sending...")
    await send_message(chat_id=chat_id, text=f"📤 Broadcasting *{campaign_id}*...")

    try:
        from config import settings
        from supabase import create_client

        sb = create_client(settings.supabase_url, settings.supabase_key)
        r = sb.table("campaigns").select("*").eq("campaign_id", campaign_id).execute()
        if not r.data:
            await send_message(chat_id=chat_id, text="❌ Not found")
            return

        deal = r.data[0]
        sent, failed, group_ok = 0, 0, False

        if settings.twilio_account_sid:
            try:
                from services.supabase_client import get_active_contacts
                from services.twilio_client import broadcast_deal

                contacts = await get_active_contacts()
                if contacts:
                    res = await broadcast_deal(deal, contacts)
                    sent = res.get("sent", 0)
                    failed = res.get("failed", 0)
            except Exception as e:
                log.warning("Twilio: %s", e)

        if deal.get("image_url"):
            try:
                from prompts.system_prompts import format_whatsapp_group
                from services.waha_client import post_deal_to_group

                group_result = await post_deal_to_group(
                    image_url=deal.get("image_url", ""),
                    caption=format_whatsapp_group(deal),
                )
                group_ok = group_result.get("status") == "sent"
            except Exception as e:
                log.warning("WAHA: %s", e)

        await update_campaign_status(campaign_id, "sent")
        await edit_message_reply_markup(chat_id, message_id, posted_keyboard())
        await send_message(
            chat_id=chat_id,
            text=(
                f"✅ *{campaign_id}*\n"
                f"📨 Twilio: {sent} sent, {failed} failed\n"
                f"📱 Group: {'✅' if group_ok else '⬜ skipped'}"
            ),
        )

    except Exception as e:
        log.error("Post now: %s", e, exc_info=True)
        await send_message(chat_id=chat_id, text=f"❌ Error: `{e}`")


async def handle_schedule(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import approved_keyboard
    from services.sheets_client import update_deal_status
    from services.supabase_client import update_campaign_status
    from services.telegram_client import (
        answer_callback_query,
        edit_message_reply_markup,
        send_message,
    )

    update_deal_status(campaign_id, "approved")
    await update_campaign_status(campaign_id, "approved")
    await answer_callback_query(callback_id, "⏰ Scheduled Monday")
    await edit_message_reply_markup(chat_id, message_id, approved_keyboard(campaign_id))
    await send_message(chat_id=chat_id, text=f"⏰ *{campaign_id}* → Monday 10:00 UTC")
