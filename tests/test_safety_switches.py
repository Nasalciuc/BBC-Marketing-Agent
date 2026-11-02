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
    monkeypatch.setattr(sheets, "update_deal_status", lambda cid, s: log["sheet"].append((cid, s)))
    return log


def _status(monkeypatch, value):
    import services.supabase_client as sb

    async def get_status(campaign_id):
        return value

    monkeypatch.setattr(sb, "get_campaign_status", get_status)


@pytest.mark.parametrize("current", ["sent", "cancelled", "approved"])
def test_approve_never_changes_a_closed_or_approved_post(monkeypatch, calls, current):
    from handlers.review import handle_approve

    _status(monkeypatch, current)
    asyncio.run(handle_approve("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [] and calls["sheet"] == []
    assert calls["answer"] == [f"Already {current}: nothing changed"]


@pytest.mark.parametrize("current", ["draft", "rejected", None])
def test_approve_goes_through_from_draft_rejected_or_unknown(monkeypatch, calls, current):
    from handlers.review import handle_approve

    _status(monkeypatch, current)
    asyncio.run(handle_approve("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == [("2026-W42-V4-001", "approved")]


@pytest.mark.parametrize("current", ["sent", "cancelled", "rejected"])
def test_reject_never_changes_a_closed_or_rejected_post(monkeypatch, calls, current):
    from handlers.review import handle_reject

    _status(monkeypatch, current)
    asyncio.run(handle_reject("2026-W42-V4-001", "cb", 1, 2))
    assert calls["status"] == []


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


def test_status_updates_never_touch_a_sent_or_cancelled_campaign(monkeypatch):
    """The guard is part of the query, so text intents and re-runs cannot bypass it either."""
    import services.supabase_client as sb

    log: list[tuple] = []

    class Query:
        def __getattr__(self, name):
            if name == "not_":
                log.append(("not_",))
                return self

            def step(*args):
                log.append((name, *args))
                return self

            return step

    class Client:
        def table(self, name):
            log.append(("table", name))
            return Query()

    monkeypatch.setattr(sb, "_get_client", lambda: Client())
    asyncio.run(sb.update_campaign_status("2026-W42-V4-001", "approved"))
    assert ("eq", "campaign_id", "2026-W42-V4-001") in log
    assert log[log.index(("not_",)) + 1] == ("in_", "status", ["sent", "cancelled"])


def test_httpx_does_not_log_request_urls():
    """Telegram Bot API URLs carry the bot token, and httpx logs every URL at INFO."""
    import logging

    import webhook  # noqa: F401 — importing it applies the logging setup

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
