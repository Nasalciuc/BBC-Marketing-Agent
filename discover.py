"""
BBC Marketing Agent — Weekly Deal Discovery.
Railway Cron: 0 14 * * 5 (vineri 14:00 UTC)
Pipeline: discover → verify → price → image gen → brand → upload → sheets → telegram
"""
import asyncio
import logging
import os
import tempfile
from datetime import UTC, datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bbc.discovery")


def _week_label() -> str:
    return datetime.now(UTC).strftime("%Y-W%W")


async def run_discovery():
    log.info("Starting weekly discovery...")

    from services.branding_engine import generate_branded_image
    from services.gemini_client import discover_and_verify, generate_event_image
    from services.image_enhancer import enhance_for_platform
    from services.notifier import send_error_alert
    from services.pricing_engine import calculate_price, format_price
    from services.sheets_client import save_drafts
    from services.supabase_client import (
        is_job_completed,
        record_job_complete,
        record_job_failed,
        record_job_start,
        save_campaign,
        upload_image,
    )
    from services.telegram_client import send_alert, send_deals_for_approval

    week = _week_label()

    if await is_job_completed("discovery", week):
        log.info("Discovery already completed for %s. Skipping.", week)
        return

    job_id = await record_job_start("discovery", week)

    try:
        log.info("Step 1: Discovering events...")
        events = await discover_and_verify(week_offset=1, max_events=3)
        if not events:
            log.warning("No events found!")
            await send_alert("Discovery found 0 events this week.")
            await record_job_complete(job_id, events_count=0)
            return

        campaign_base = datetime.now(UTC).strftime("%Y-W%W")

        for i, event in enumerate(events):
            campaign_id = f"{campaign_base}-{i + 1:03d}"
            event["campaign_id"] = campaign_id

            routes = event.get("routes", [{}])
            route = routes[0] if routes else {}
            from_iata = route.get("from", "JFK")
            to_iata = route.get("to", "LHR")

            price = calculate_price(from_iata, to_iata, "round_trip", "business")
            event["price"] = format_price(price) if price else "Contact us"
            event["price_raw"] = price
            event["route_str"] = f"{from_iata} → {to_iata}"
            log.info("  %s: %s = %s", event.get("name"), event["route_str"], event["price"])

            log.info("  Generating background image...")
            image_prompt = event.get(
                "image_prompt", f"Beautiful view of {event.get('city', 'destination')}"
            )
            bg_image = await generate_event_image(image_prompt)

            log.info("  Applying BBC branding...")
            bg_source = "assets/defaults/default_background.jpg"
            if bg_image:
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tmp.write(bg_image)
                tmp.close()
                bg_source = tmp.name

            try:
                branded = generate_branded_image(
                    event_name=event.get("name", "Premium Event"),
                    route=event["route_str"],
                    price=event["price"],
                    background_url_or_path=bg_source,
                )
            finally:
                if (
                    bg_image
                    and os.path.exists(bg_source)
                    and bg_source != "assets/defaults/default_background.jpg"
                ):
                    os.unlink(bg_source)

            enhanced = enhance_for_platform(branded, "whatsapp")

            image_url = await upload_image(enhanced, f"deals/{campaign_id}/landscape.jpg")
            event["image_url"] = image_url
            log.info("  Uploaded: %s", image_url)

            await save_campaign(event)

        log.info("Saving drafts to Sheets...")
        save_drafts(events)

        log.info("Sending Telegram approval requests...")
        await send_deals_for_approval(events)

        await record_job_complete(job_id, events_count=len(events))
        log.info("Discovery complete! %d deals ready for approval.", len(events))

    except Exception as e:
        log.error("Discovery FAILED: %s", e, exc_info=True)
        await record_job_failed(job_id, str(e))
        await send_error_alert(f"Discovery failed:\n{e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_discovery())
