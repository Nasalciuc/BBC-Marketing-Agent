"""Review flow: approve, reject, edit caption, regenerate image."""
import asyncio
import logging
import os
import tempfile

log = logging.getLogger("bbc.handlers.review")

# A post that reached clients, or was closed, is never changed by a late or double tap.
LOCKED_STATUSES = {"sent", "cancelled"}

NOT_READ = "⚠️ Couldn't read the post's status: nothing changed. Try again."
SHEET_FAILED = "⚠️ The Google Sheet did not update: nothing changed. Try again."
DB_FAILED = "⚠️ The database did not update: the post is as it was. Try again."


async def set_review_status(campaign_id: str, target: str) -> tuple[str, str | None]:
    """Approve or reject a post in the Sheet and in Supabase — never one that is sent or cancelled.

    Returns (outcome, status before):
      "done"         both stores now say `target`;
      "same"         it already was `target` — the Sheet is written again, in case an earlier write failed;
      "locked"       it is sent or cancelled — nothing touched;
      "unreadable"   its status can't be read — nothing touched;
      "sheet_failed" the Sheet write failed — Supabase is left as it was, so tapping again redoes both;
      "db_failed"    the Supabase update changed nothing (an error, or it was sent meanwhile — then
                     "locked") — the Sheet is put back, so tapping again redoes both.
    Buttons and text intents both go through here, so neither can bypass the guard.
    """
    from services.sheets_client import update_deal_status
    from services.supabase_client import StatusUnavailable, get_campaign_status, update_campaign_status

    try:
        current = await get_campaign_status(campaign_id)
    except StatusUnavailable:
        return "unreadable", None
    if current in LOCKED_STATUSES:
        return "locked", current
    if not update_deal_status(campaign_id, target):
        return "sheet_failed", current
    if current == target:
        return "same", current
    if await update_campaign_status(campaign_id, target):
        return "done", current
    # nothing changed in Supabase: put the Sheet back so the two stores agree, then say why
    try:
        now = await get_campaign_status(campaign_id)
    except StatusUnavailable:
        now = current
    if not update_deal_status(campaign_id, now or current or "draft"):
        log.error("Sheet left at %s for %s while Supabase says %s", target, campaign_id, now)
    if now in LOCKED_STATUSES:
        return "locked", now
    return "db_failed", current


def review_reply(outcome: str, current: str | None, done: str) -> str:
    """What to tell the person after set_review_status."""
    if outcome == "done":
        return done
    if outcome in ("same", "locked"):
        return f"Already {current}: nothing changed"
    return {"unreadable": NOT_READ, "db_failed": DB_FAILED}.get(outcome, SHEET_FAILED)


async def handle_approve(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import approved_keyboard
    from services.telegram_client import answer_callback_query, edit_message_reply_markup

    outcome, current = await set_review_status(campaign_id, "approved")
    await answer_callback_query(callback_id, review_reply(outcome, current, "✅ Approved"))
    if outcome == "done":
        log.info("Approved: %s", campaign_id)
        await edit_message_reply_markup(chat_id, message_id, approved_keyboard(campaign_id))


async def handle_reject(
    campaign_id: str, callback_id: str, chat_id: int, message_id: int
):
    from keyboards import rejected_keyboard
    from services.telegram_client import answer_callback_query, edit_message_reply_markup

    outcome, current = await set_review_status(campaign_id, "rejected")
    await answer_callback_query(callback_id, review_reply(outcome, current, "❌ Rejected"))
    if outcome == "done":
        log.info("Rejected: %s", campaign_id)
        await edit_message_reply_markup(chat_id, message_id, rejected_keyboard())


async def handle_edit_caption(campaign_id: str, callback_id: str, chat_id: int):
    from services.supabase_client import FINAL_STATUSES, StatusUnavailable, get_campaign_status
    from services.telegram_client import answer_callback_query, send_message

    try:
        current = await get_campaign_status(campaign_id)
    except StatusUnavailable:
        await answer_callback_query(callback_id, NOT_READ)
        return
    if current in LOCKED_STATUSES:
        await answer_callback_query(callback_id, f"Already {current}: nothing changed")
        return

    try:
        from config import settings
        from supabase import create_client

        if settings.supabase_url and settings.supabase_key:
            sb = create_client(settings.supabase_url, settings.supabase_key)
            sb.table("campaigns").update({"status": "editing"}).eq(
                "campaign_id", campaign_id
            ).not_.in_("status", list(FINAL_STATUSES)).execute()
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

        from services.supabase_client import FINAL_STATUSES

        sb = create_client(settings.supabase_url, settings.supabase_key)
        r = sb.table("campaigns").update(
            {
                "caption_override": new_caption,
                "status": "draft",
            }
        ).eq("campaign_id", campaign_id).not_.in_("status", list(FINAL_STATUSES)).execute()
        if not r.data:
            await send_message(
                chat_id=chat_id,
                text=f"❌ *{campaign_id}* is sent or cancelled, or does not exist: caption not changed.",
            )
            return
        await send_message(chat_id=chat_id, text=f"✅ Caption updated for *{campaign_id}*.")
    except Exception as e:
        log.error("Caption update: %s", e)
        await send_message(chat_id=chat_id, text=f"❌ Error: {e}")


async def handle_regenerate(campaign_id: str, callback_id: str, chat_id: int):
    from services.branding_engine import generate_branded_image
    from services.supabase_client import upload_image
    from services.telegram_client import answer_callback_query, send_approval_request, send_message

    from config import settings

    if not settings.legacy_regen_enabled:
        await answer_callback_query(callback_id, "🔄 Regenerate is off for curated posts")
        return

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
