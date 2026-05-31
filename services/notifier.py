"""
BBC Notifier — Email backup notifications.
"""
import logging

log = logging.getLogger("bbc.notifier")


async def send_email(subject: str, body: str) -> bool:
    """Trimite email de notificare/alertă."""
    from config import settings

    if not settings.resend_api_key:
        log.info("Email not configured — would send: %s", subject)
        return False

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.email_from or "BBC Agent <noreply@buybusinessclass.com>",
                    "to": [settings.email_to] if settings.email_to else [],
                    "subject": subject,
                    "text": body,
                },
            )
            if resp.status_code == 200:
                log.info("Email sent: %s", subject)
                return True
            log.error("Email failed: %s", resp.text)
            return False
    except Exception as e:
        log.error("Email error: %s", e)
        return False


async def send_error_alert(error_message: str):
    """Trimite alertă pe AMBELE canale: Telegram + Email."""
    from services.telegram_client import send_alert

    await send_alert(error_message)
    await send_email(
        subject="⚠️ BBC Marketing Agent Error",
        body=error_message,
    )
