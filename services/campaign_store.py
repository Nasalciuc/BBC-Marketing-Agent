"""Load/save campaign metadata — Supabase with local JSON fallback."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("bbc.campaign_store")

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / "output" / "pipeline" / "campaigns"


def save_campaign_local(campaign: dict) -> None:
    """Persist campaign for regenerate when Supabase is unavailable."""
    cid = campaign.get("campaign_id")
    if not cid:
        return
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_DIR / f"{cid}.json"
    path.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved local campaign cache: %s", cid)


def load_campaign_local(campaign_id: str) -> dict | None:
    path = LOCAL_DIR / f"{campaign_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to read local campaign %s: %s", campaign_id, exc)
        return None


async def get_campaign(campaign_id: str) -> dict | None:
    """Fetch campaign from Supabase, falling back to local cache."""
    from services.supabase_client import _get_client

    client = _get_client()
    if client:
        try:
            r = client.table("campaigns").select("*").eq("campaign_id", campaign_id).execute()
            if r.data:
                return r.data[0]
        except Exception as exc:
            log.warning("Supabase get_campaign failed for %s: %s", campaign_id, exc)

    return load_campaign_local(campaign_id)
