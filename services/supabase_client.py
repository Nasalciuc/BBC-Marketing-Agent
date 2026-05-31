"""
BBC Supabase Client — Storage upload + DB operations.
Pornește fără crash dacă SUPABASE_URL/KEY nu sunt setate.
"""
import logging
from datetime import UTC, datetime

log = logging.getLogger("bbc.supabase")

_client = None


def _get_client():
    """Lazy init — nu conectează la import time."""
    global _client
    if _client is None:
        from config import settings

        if not settings.supabase_url or not settings.supabase_key:
            log.warning("Supabase not configured — operations will be skipped")
            return None
        from supabase import create_client

        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


async def upload_image(image_bytes: bytes, path: str) -> str | None:
    """Upload imagine pe Supabase Storage → returnează public URL."""
    client = _get_client()
    if not client:
        log.warning("Supabase not available — skipping upload %s", path)
        return None

    try:
        from config import settings

        bucket = settings.supabase_storage_bucket

        client.storage.from_(bucket).upload(
            path=path,
            file=image_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"},
        )

        public_url = f"{settings.supabase_url}/storage/v1/object/public/{bucket}/{path}"
        log.info("Uploaded: %s → %s", path, public_url)
        return public_url

    except Exception as e:
        log.error("Upload failed (%s): %s", path, e)
        return None


async def record_job_start(job_name: str, week_label: str) -> str | None:
    """Înregistrează începutul unui job. Returnează ID-ul."""
    client = _get_client()
    if not client:
        return None
    try:
        result = client.table("job_runs").insert(
            {
                "job_name": job_name,
                "week_label": week_label,
                "status": "running",
            }
        ).execute()
        job_id = result.data[0]["id"] if result.data else None
        log.info("Job started: %s %s (id=%s)", job_name, week_label, job_id)
        return job_id
    except Exception as e:
        log.error("Failed to record job start: %s", e)
        return None


async def record_job_complete(
    job_id: str, events_count: int = 0, details: dict | None = None
):
    """Marchează jobul ca terminat cu succes."""
    client = _get_client()
    if not client or not job_id:
        return
    try:
        client.table("job_runs").update(
            {
                "status": "completed",
                "events_count": events_count,
                "details": details or {},
                "completed_at": datetime.now(UTC).isoformat(),
            }
        ).eq("id", job_id).execute()
    except Exception as e:
        log.error("Failed to record job complete: %s", e)


async def record_job_failed(job_id: str, error: str):
    """Marchează jobul ca eșuat."""
    client = _get_client()
    if not client or not job_id:
        return
    try:
        client.table("job_runs").update(
            {
                "status": "failed",
                "error_message": error[:500],
                "completed_at": datetime.now(UTC).isoformat(),
            }
        ).eq("id", job_id).execute()
    except Exception as e:
        log.error("Failed to record job failure: %s", e)


async def is_job_completed(job_name: str, week_label: str) -> bool:
    """Verifică dacă jobul a rulat deja cu succes (idempotency)."""
    client = _get_client()
    if not client:
        return False
    try:
        result = (
            client.table("job_runs")
            .select("id")
            .eq("job_name", job_name)
            .eq("week_label", week_label)
            .eq("status", "completed")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        log.error("Failed to check job status: %s", e)
        return False


async def save_campaign(event: dict) -> bool:
    """Salvează un deal/campaign în Supabase."""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("campaigns").upsert(
            {
                "campaign_id": event.get("campaign_id"),
                "event_name": event.get("name", event.get("event_name", "")),
                "city": event.get("city"),
                "dates_start": event.get("dates_start"),
                "dates_end": event.get("dates_end"),
                "category": event.get("category"),
                "premium_score": event.get("premium_score"),
                "route": event.get("route_str", event.get("route", "")),
                "price": event.get("price"),
                "price_raw": event.get("price_raw"),
                "image_url": event.get("image_url"),
                "story_url": event.get("story_url"),
                "caption": event.get("caption", event.get("caption_draft", "")),
                "status": event.get("status", "draft"),
            }
        ).execute()
        return True
    except Exception as e:
        log.error("Failed to save campaign: %s", e)
        return False


async def get_approved_campaigns() -> list[dict]:
    """Returnează campaign-uri aprobate."""
    client = _get_client()
    if not client:
        return []
    try:
        result = client.table("campaigns").select("*").eq("status", "approved").execute()
        return result.data or []
    except Exception as e:
        log.error("Failed to get approved campaigns: %s", e)
        return []


async def update_campaign_status(campaign_id: str, status: str):
    """Actualizează statusul unui campaign."""
    client = _get_client()
    if not client:
        return
    try:
        update_data: dict = {"status": status}
        if status == "approved":
            update_data["approved_at"] = datetime.now(UTC).isoformat()
        elif status == "sent":
            update_data["sent_at"] = datetime.now(UTC).isoformat()
        client.table("campaigns").update(update_data).eq("campaign_id", campaign_id).execute()
    except Exception as e:
        log.error("Failed to update campaign status: %s", e)


async def get_active_contacts() -> list[dict]:
    """Returnează contactele active pentru broadcast."""
    client = _get_client()
    if not client:
        return []
    try:
        result = (
            client.table("contacts")
            .select("phone_number, full_name, country")
            .eq("status", "active")
            .execute()
        )
        return result.data or []
    except Exception as e:
        log.error("Failed to get contacts: %s", e)
        return []


async def log_broadcast(
    campaign_id: str,
    phone_number: str,
    status: str,
    channel: str = "twilio",
    twilio_sid: str | None = None,
    error: str | None = None,
):
    """Loghează o trimitere de mesaj."""
    client = _get_client()
    if not client:
        return
    try:
        client.table("broadcast_log").insert(
            {
                "campaign_id": campaign_id,
                "phone_number": phone_number,
                "status": status,
                "channel": channel,
                "twilio_sid": twilio_sid,
                "error_message": error,
            }
        ).execute()
    except Exception as e:
        log.error("Failed to log broadcast: %s", e)
