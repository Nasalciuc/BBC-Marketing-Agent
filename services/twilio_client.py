"""
BBC Twilio Client — WhatsApp broadcast 1:1.
"""
import asyncio
import logging

log = logging.getLogger("bbc.twilio")


async def broadcast_deal(deal: dict, contacts: list[dict]) -> dict:
    """Trimite deal-ul la toate contactele via Twilio WhatsApp."""
    from config import settings

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        log.warning("Twilio not configured — skipping broadcast")
        return {"sent": 0, "failed": 0, "errors": ["Twilio not configured"]}

    from twilio.rest import Client as TwilioClient

    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    results: dict = {"sent": 0, "failed": 0, "errors": []}

    route = deal.get("route", "")
    price = deal.get("price", "")

    for contact in contacts:
        phone = contact.get("phone_number", "")
        if not phone:
            continue

        try:
            msg_params: dict = {
                "from_": settings.twilio_whatsapp_from,
                "to": f"whatsapp:{phone}" if not phone.startswith("whatsapp:") else phone,
            }

            if settings.twilio_content_sid:
                msg_params["content_sid"] = settings.twilio_content_sid
                msg_params["content_variables"] = f'{{"1":"{route}","2":"{price}"}}'
            else:
                msg_params["body"] = (
                    f"✈️ {deal.get('event_name', 'Business Class Deal')}\n"
                    f"{route} from {price}\n\n"
                    f"Book: buybusinessclass.com"
                )
                if deal.get("image_url"):
                    msg_params["media_url"] = [deal["image_url"]]

            message = client.messages.create(**msg_params)
            results["sent"] += 1

            from services.supabase_client import log_broadcast

            await log_broadcast(
                campaign_id=deal.get("campaign_id", ""),
                phone_number=phone,
                status="sent",
                twilio_sid=message.sid,
            )

            await asyncio.sleep(1)

        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{phone}: {e}")
            log.error("Failed to send to %s: %s", phone, e)

    log.info("Broadcast complete: %d sent, %d failed", results["sent"], results["failed"])
    return results
