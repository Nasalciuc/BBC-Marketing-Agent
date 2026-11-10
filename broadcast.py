"""
BBC Marketing Agent — Weekly Deal Broadcast.
Railway Cron: 0 10 * * 1 (luni 10:00 UTC)
Pipeline: read approved → Twilio broadcast → WAHA group → report
"""
import asyncio
import logging
from datetime import UTC, datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs each request URL at INFO, and a Telegram URL contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("bbc.broadcast")


def _week_label() -> str:
    return datetime.now(UTC).strftime("%Y-W%W")


async def run_broadcast():
    from config import settings

    if not settings.broadcast_enabled:
        log.warning("Broadcast is off (BROADCAST_ENABLED=false): nothing sent")
        return

    log.info("Starting weekly broadcast...")

    from services.notifier import send_error_alert
    from services.sheets_client import get_approved_deals, update_deal_status
    from services.supabase_client import (
        StatusUnavailable,
        get_active_contacts,
        get_approved_campaigns,
        get_campaign_status,
        is_job_completed,
        record_job_complete,
        record_job_failed,
        record_job_start,
        reserve_campaign_for_sending,
    )
    from services.telegram_client import send_alert, send_broadcast_report
    from prompts.system_prompts import format_whatsapp_group
    from services.twilio_client import broadcast_deal
    from services.waha_client import post_deal_to_group

    week = _week_label()

    if await is_job_completed("broadcast", week):
        log.info("Broadcast already completed for %s. Skipping.", week)
        return

    job_id = await record_job_start("broadcast", week)

    try:
        approved = get_approved_deals()
        if not approved:
            approved_sb = await get_approved_campaigns()
            if approved_sb:
                approved = approved_sb

        if not approved:
            log.warning("No approved deals. Skipping broadcast.")
            await send_alert("No deals approved this week. Broadcast skipped.")
            await record_job_complete(job_id, events_count=0)
            return

        contacts = await get_active_contacts()
        log.info("Broadcasting %d deals to %d contacts", len(approved), len(contacts))

        total_results: dict = {"sent": 0, "failed": 0, "group_posted": False, "skipped": 0}

        for deal in approved:
            campaign_id = deal.get("campaign_id", "")
            if campaign_id:
                # The Sheet can still say "approved" after Post NOW or Cancel. Supabase decides: the
                # campaign is marked sent in one guarded update before anything goes to a client, so
                # it goes out at most once. A deal that is only in the Sheet is sent as before.
                try:
                    status = await get_campaign_status(campaign_id)
                    reserved = status is None or await reserve_campaign_for_sending(campaign_id) is not None
                except StatusUnavailable as e:
                    log.error("campaign %s: status unreadable (%s): not sent", campaign_id, e)
                    total_results["skipped"] += 1
                    continue
                if not reserved:
                    log.warning("campaign %s is already sent or cancelled: not sent again", campaign_id)
                    total_results["skipped"] += 1
                    if status in ("sent", "cancelled"):
                        update_deal_status(campaign_id, status)       # put the Sheet back in step
                    continue

            if contacts:
                results = await broadcast_deal(deal, contacts)
                total_results["sent"] += results["sent"]
                total_results["failed"] += results["failed"]

            caption = format_whatsapp_group(deal)
            group_result = await post_deal_to_group(
                image_url=deal.get("image_url", ""),
                caption=caption,
            )
            if group_result.get("status") == "sent":
                total_results["group_posted"] = True

            if campaign_id:
                update_deal_status(campaign_id, "sent")       # Supabase was marked before the send

        await send_broadcast_report(total_results)
        await record_job_complete(job_id, events_count=len(approved), details=total_results)
        log.info(
            "Broadcast complete! Sent: %d, Failed: %d",
            total_results["sent"],
            total_results["failed"],
        )

    except Exception as e:
        log.error("Broadcast FAILED: %s", e, exc_info=True)
        await record_job_failed(job_id, str(e))
        await send_error_alert(f"Broadcast failed:\n{e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_broadcast())
