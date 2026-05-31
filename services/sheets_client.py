"""
BBC Google Sheets Client — CRUD pe "Deal Drafts" sheet.
Pornește fără crash dacă credentials nu sunt setate.
"""
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("bbc.sheets")

_gc = None
_sheet = None

COLUMNS = [
    "campaign_id",
    "event_name",
    "city",
    "dates",
    "category",
    "premium_score",
    "route",
    "price",
    "image_url",
    "story_url",
    "caption",
    "status",
    "created_at",
]


def _get_sheet():
    """Lazy init — conectează la Sheets doar la prima utilizare."""
    global _gc, _sheet
    if _sheet is not None:
        return _sheet

    from config import settings

    if not settings.google_sheets_id or not settings.google_service_account_json:
        log.warning("Google Sheets not configured — operations will be skipped")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        sa_json = settings.google_service_account_json
        if sa_json.strip().startswith("{"):
            info = json.loads(sa_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
        elif Path(sa_json).exists():
            creds = Credentials.from_service_account_file(sa_json, scopes=scopes)
        else:
            log.error("Service account not found: %s", sa_json)
            return None

        _gc = gspread.authorize(creds)
        spreadsheet = _gc.open_by_key(settings.google_sheets_id)
        _sheet = spreadsheet.worksheet("Deal Drafts")
        log.info("Connected to Sheets: %s", spreadsheet.title)
        return _sheet

    except Exception as e:
        log.error("Failed to connect to Sheets: %s", e)
        return None


def save_drafts(events: list[dict]):
    """Salvează events ca draft rows în Sheet."""
    sheet = _get_sheet()
    if not sheet:
        log.warning("Sheets not available — skipping save")
        return

    try:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for event in events:
            dates = ""
            if event.get("dates_start") and event.get("dates_end"):
                dates = f"{event['dates_start']} — {event['dates_end']}"

            route_str = event.get("route_str", "")
            if not route_str:
                routes = event.get("routes", [])
                if routes:
                    r = routes[0]
                    route_str = f"{r.get('from', '?')} → {r.get('to', '?')}"

            row = [
                event.get("campaign_id", ""),
                event.get("name", event.get("event_name", "")),
                event.get("city", ""),
                dates,
                event.get("category", ""),
                str(event.get("premium_score", "")),
                route_str,
                event.get("price", ""),
                event.get("image_url", ""),
                event.get("story_url", ""),
                event.get("caption", event.get("caption_draft", "")),
                "draft",
                now,
            ]
            rows.append(row)

        if rows:
            sheet.append_rows(rows, value_input_option="RAW")
            log.info("Saved %d drafts to Sheets", len(rows))

    except Exception as e:
        log.error("Failed to save drafts: %s", e)


def get_approved_deals() -> list[dict]:
    """Returnează deal-urile cu status 'approved'."""
    sheet = _get_sheet()
    if not sheet:
        return []

    try:
        all_records = sheet.get_all_records()
        approved = [r for r in all_records if r.get("status") == "approved"]
        log.info("Found %d approved deals", len(approved))
        return approved
    except Exception as e:
        log.error("Failed to get approved deals: %s", e)
        return []


def update_deal_status(campaign_id: str, new_status: str):
    """Actualizează status-ul unui deal în Sheet."""
    sheet = _get_sheet()
    if not sheet:
        return

    try:
        cell = sheet.find(campaign_id, in_column=1)
        if cell:
            status_col = COLUMNS.index("status") + 1
            sheet.update_cell(cell.row, status_col, new_status)
            log.info("Updated %s → %s", campaign_id, new_status)
        else:
            log.warning("Campaign %s not found in Sheet", campaign_id)
    except Exception as e:
        log.error("Failed to update deal status: %s", e)
