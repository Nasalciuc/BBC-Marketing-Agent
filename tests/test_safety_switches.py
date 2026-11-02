"""PR-0 guards: late taps never change a closed post; Regenerate and client sends are off by default."""
import asyncio

import pytest

from config import settings


@pytest.fixture
def calls(monkeypatch):
    """Replace every Telegram / Supabase / Sheets / Twilio call the handlers make with recorders."""
    import services.sheets_client as sheets
    import services.supabase_client as sb
    import services.telegram_client as tg

    log: dict[str, list] = {"answer": [], "status": [], "sheet": [], "markup": [], "message": []}

    async def answer(callback_id, text=""):
        log["answer"].append(text)

    async def update_status(campaign_id, status):
        log["status"].append((campaign_id, status))

    async def edit_markup(chat_id, message_id, reply_markup=None):
        log["markup"].append(message_id)

    async def send_message(chat_id=None, text="", **kwargs):
        log["message"].append(text)

    monkeypatch.setattr(tg, "answer_callback_query", answer)
    monkeypatch.setattr(tg, "edit_message_reply_markup", edit_markup)
    monkeypatch.setattr(tg, "send_message", send_message)
    monkeypatch.setattr(sb, "update_campaign_status", update_status)

    def sheet_write(cid, status):
        log["sheet"].append((cid, status))
        return log.get("sheet_ok", True)

    monkeypatch.setattr(sheets, "update_deal_status", sheet_write)
    return log


def _status(monkeypatch, value):
    """Make get_campaign_status return `value`, or raise it when it is an exception."""
    import services.supabase_client as sb

    async def get_status(campaign_id):
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(sb, "get_campaign_status", get_status)


def _unreadable():
    from services.supabase_client import StatusUnavailable

    return StatusUnavailable("connection reset")


@pytest.mark.parametrize("current", ["sent", "cancelled"])
def test_approve_never_changes_a_closed_post(monkeypatch, calls, current):
    from handlers.review import handle_approve

    _status(monkeypatch, current)
    asyncio.run(handle_approve("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [] and calls["sheet"] == []
    assert calls["answer"] == [f"Already {current}: nothing changed"]


def test_approving_again_only_puts_the_sheet_back_in_step(monkeypatch, calls):
    """Found in review: a repeated tap used to return early, so a Sheet write that failed stayed wrong."""
    from handlers.review import handle_approve

    _status(monkeypatch, "approved")
    asyncio.run(handle_approve("2026-W42-V4-001", "cb", 1, 2))
    assert calls["sheet"] == [("2026-W42-V4-001", "approved")]
    assert calls["status"] == [] and calls["markup"] == []
    assert calls["answer"] == ["Already approved: nothing changed"]


@pytest.mark.parametrize("current", ["draft", "rejected", None])
def test_approve_goes_through_from_draft_rejected_or_unknown(monkeypatch, calls, current):
    from handlers.review import handle_approve

    _status(monkeypatch, current)
    asyncio.run(handle_approve("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [("2026-W42-V4-001", "approved")]


@pytest.mark.parametrize("current", ["sent", "cancelled"])
def test_reject_never_changes_a_closed_post(monkeypatch, calls, current):
    from handlers.review import handle_reject

    _status(monkeypatch, current)
    asyncio.run(handle_reject("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [] and calls["sheet"] == []


def test_a_failed_sheet_write_leaves_supabase_as_it_was(monkeypatch, calls):
    """Found in review: Supabase said rejected while the Sheet still said approved (eligible to send)."""
    from handlers.review import SHEET_FAILED, handle_reject

    _status(monkeypatch, "approved")
    calls["sheet_ok"] = False
    asyncio.run(handle_reject("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [] and calls["answer"] == [SHEET_FAILED]
    calls["sheet_ok"] = True                                   # tapping again redoes both
    asyncio.run(handle_reject("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [("2026-W42-V4-001", "rejected")]


@pytest.mark.parametrize("handler", ["handle_approve", "handle_reject"])
def test_an_unreadable_status_changes_nothing(monkeypatch, calls, handler):
    """Found in review: a failed read looked like 'no row', so the Sheet was changed for a sent post."""
    import handlers.review as review

    _status(monkeypatch, _unreadable())
    asyncio.run(getattr(review, handler)("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [] and calls["sheet"] == []
    assert calls["answer"] == [review.NOT_READ]


@pytest.mark.parametrize("current", ["sent", "cancelled"])
def test_edit_caption_never_reopens_a_closed_post(monkeypatch, calls, current):
    """Found in review: Edit wrote 'editing' straight to the table, past the guard."""
    from handlers.review import handle_edit_caption

    def must_not_connect(*args, **kwargs):
        raise AssertionError("Edit wrote to a closed post")

    import supabase

    monkeypatch.setattr(supabase, "create_client", must_not_connect)
    _status(monkeypatch, current)
    asyncio.run(handle_edit_caption("URG-1", "cb", 1))
    assert calls["answer"] == [f"Already {current}: nothing changed"] and calls["message"] == []


@pytest.mark.parametrize("current", ["sent", "cancelled"])
def test_schedule_never_reopens_a_closed_post(monkeypatch, calls, current):
    from handlers.urgent import handle_schedule

    _status(monkeypatch, current)
    asyncio.run(handle_schedule("URG-1", "cb", 1, 2))
    assert calls["status"] == [] and calls["sheet"] == [] and calls["message"] == []


@pytest.mark.parametrize("intent", ["APPROVE", "REJECT", "SCHEDULE"])
def test_text_intents_never_change_a_sent_post(monkeypatch, calls, intent):
    """'da' / 'nu' / 'programează' typed in the chat go through the same guard as the buttons."""
    import services.anthropic_client as ai
    import services.context_manager as ctx
    from handlers.conversation import handle_ai_message

    async def classify(text, context):
        return {"intent": intent, "confidence": 0.95, "entities": {}, "response": ""}

    async def get_context(chat_id):
        return {"current_campaign_id": "2026-W42-V4-001"}

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(ai, "classify_intent", classify)
    monkeypatch.setattr(ctx, "get_context", get_context)
    for name in ("add_message", "clear_context", "update_context"):
        monkeypatch.setattr(ctx, name, nothing)
    _status(monkeypatch, "sent")
    asyncio.run(handle_ai_message(1, 7, "da"))
    assert calls["status"] == [] and calls["sheet"] == []
    assert calls["message"] == ["Already sent: nothing changed"]


def test_regenerate_is_off_by_default(monkeypatch, calls):
    from handlers.review import handle_regenerate

    monkeypatch.setattr(settings, "legacy_regen_enabled", False)
    asyncio.run(handle_regenerate("2026-W42-V4-001", "cb", 1))
    assert calls["answer"] == ["🔄 Regenerate is off for curated posts"]
    assert calls["message"] == []


def test_regenerate_button_follows_the_switch(monkeypatch):
    from keyboards import review_keyboard

    def labels():
        return [b["text"] for row in review_keyboard("X")["inline_keyboard"] for b in row]

    monkeypatch.setattr(settings, "legacy_regen_enabled", False)
    assert "🔄 Regenerate" not in labels()
    monkeypatch.setattr(settings, "legacy_regen_enabled", True)
    assert "🔄 Regenerate" in labels()


def test_broadcast_is_off_by_default(monkeypatch):
    import services.supabase_client as sb
    from broadcast import run_broadcast

    async def must_not_run(*args, **kwargs):
        raise AssertionError("broadcast touched the database while switched off")

    monkeypatch.setattr(settings, "broadcast_enabled", False)
    monkeypatch.setattr(sb, "is_job_completed", must_not_run)
    asyncio.run(run_broadcast())


def test_post_now_is_off_by_default(monkeypatch, calls):
    from handlers.urgent import handle_post_now

    monkeypatch.setattr(settings, "broadcast_enabled", False)
    asyncio.run(handle_post_now("URG-1", "cb", 1, 2))
    assert calls["answer"] == ["📤 Sending to clients is off"]
    assert calls["status"] == []


def _reserve(monkeypatch, row):
    import services.supabase_client as sb

    reserved: list[str] = []

    async def reserve(campaign_id):
        reserved.append(campaign_id)
        return row

    monkeypatch.setattr(sb, "reserve_campaign_for_sending", reserve)
    return reserved


def test_post_now_sends_nothing_for_a_sent_or_cancelled_campaign(monkeypatch, calls):
    """Found in review: Post NOW after Cancel (or a second tap) sent first and checked after."""
    import services.supabase_client as sb
    from handlers.urgent import handle_post_now

    async def must_not_send(*args, **kwargs):
        raise AssertionError("contacts were read for a campaign that must not be sent")

    monkeypatch.setattr(settings, "broadcast_enabled", True)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC-test")
    monkeypatch.setattr(sb, "get_active_contacts", must_not_send)
    reserved = _reserve(monkeypatch, None)              # the guarded update matched no row
    asyncio.run(handle_post_now("URG-1", "cb", 1, 2))
    assert reserved == ["URG-1"] and calls["markup"] == []
    assert calls["answer"] == ["Already sent or cancelled: nothing was sent"]


def test_post_now_marks_the_campaign_sent_before_sending(monkeypatch, calls):
    from handlers.urgent import handle_post_now

    monkeypatch.setattr(settings, "broadcast_enabled", True)
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    reserved = _reserve(monkeypatch, {"campaign_id": "URG-1", "status": "sent"})
    asyncio.run(handle_post_now("URG-1", "cb", 1, 2))
    assert reserved == ["URG-1"] and calls["answer"] == ["📤 Sending..."]
    assert calls["markup"] == [2] and calls["status"] == []      # no second status write afterwards


class _Query:
    """Records the query-builder calls a Supabase helper makes."""

    def __init__(self, log, data=None, error=None):
        self.log, self.data, self.error = log, data, error

    def __getattr__(self, name):
        if name == "not_":
            self.log.append(("not_",))
            return self

        def step(*args):
            self.log.append((name, *args))
            if name == "execute":
                if self.error:
                    raise self.error
                return type("Result", (), {"data": self.data or []})()
            return self

        return step


def _client(monkeypatch, log, **kwargs):
    import services.supabase_client as sb

    class Client:
        def table(self, name):
            log.append(("table", name))
            return _Query(log, **kwargs)

    monkeypatch.setattr(sb, "_get_client", lambda: Client())


def test_status_updates_never_touch_a_sent_or_cancelled_campaign(monkeypatch):
    """The guard is part of the query, so text intents and re-runs cannot bypass it either."""
    import services.supabase_client as sb

    log: list[tuple] = []
    _client(monkeypatch, log)
    asyncio.run(sb.update_campaign_status("2026-W42-V4-001", "approved"))
    assert ("eq", "campaign_id", "2026-W42-V4-001") in log
    assert log[log.index(("not_",)) + 1] == ("in_", "status", ["sent", "cancelled"])


def test_reserving_for_sending_is_one_guarded_update(monkeypatch):
    import services.supabase_client as sb

    log: list[tuple] = []
    _client(monkeypatch, log, data=[{"campaign_id": "URG-1", "status": "sent"}])
    row = asyncio.run(sb.reserve_campaign_for_sending("URG-1"))
    assert row["status"] == "sent"
    update = next(entry for entry in log if entry[0] == "update")
    assert update[1]["status"] == "sent" and "sent_at" in update[1]
    assert log[log.index(("not_",)) + 1] == ("in_", "status", ["sent", "cancelled"])

    log.clear()
    _client(monkeypatch, log, data=[])                       # already sent or cancelled
    assert asyncio.run(sb.reserve_campaign_for_sending("URG-1")) is None


def test_a_failed_status_read_is_not_mistaken_for_a_missing_row(monkeypatch):
    import services.supabase_client as sb

    _client(monkeypatch, [], data=[])
    assert asyncio.run(sb.get_campaign_status("NOPE")) is None
    _client(monkeypatch, [], error=ConnectionError("reset"))
    with pytest.raises(sb.StatusUnavailable):
        asyncio.run(sb.get_campaign_status("2026-W42-V4-001"))


def test_sheet_write_reports_failure_only_when_configured_and_failing(monkeypatch):
    import services.sheets_client as sheets

    monkeypatch.setattr(sheets, "_get_sheet", lambda: None)
    monkeypatch.setattr(settings, "google_sheets_id", "")
    assert sheets.update_deal_status("X", "approved") is True      # not configured: nothing to keep in step
    monkeypatch.setattr(settings, "google_sheets_id", "sheet-id")
    monkeypatch.setattr(settings, "google_service_account_json", "{}")
    assert sheets.update_deal_status("X", "approved") is False     # configured but unreachable

    class Broken:
        def find(self, *args, **kwargs):
            raise ConnectionError("quota")

    monkeypatch.setattr(sheets, "_get_sheet", lambda: Broken())
    assert sheets.update_deal_status("X", "approved") is False


def test_httpx_does_not_log_request_urls():
    """Telegram Bot API URLs carry the bot token, and httpx logs every URL at INFO."""
    import logging

    import webhook  # noqa: F401 — importing it applies the logging setup

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
