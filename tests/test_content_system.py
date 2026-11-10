"""Content system — manifest rules, the post graph, decisions and the Telegram bridge (all in memory)."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from content_system import bridge
from content_system.engine import decide, start, view
from content_system.graph import build_graph
from content_system.manifest import (
    ManifestError,
    caption_from_edit,
    caption_problems,
    compose_caption,
    load_week,
    review_line,
    utf16_len,
)
from content_system.settings import db_url_problem, get_settings
from content_system.store import EffectBusy, LeaseBusy, MemoryStore
from content_system.telegram import TelegramError, parse_callback, review_keyboard
from prompts.system_prompts import CONTACT_LINE_LUX
from services.design_system import Style
from tests.content_fakes import CHAT, FAST_RETRY, W42, FakeTelegram, make_services, week42


def run(coro):
    return asyncio.run(coro)


def _manifest(tmp_path: Path, replace: dict[str, str]) -> Path:
    text = W42.read_text(encoding="utf-8")
    for old, new in replace.items():
        assert old in text, old
        text = text.replace(old, new, 1)
    path = tmp_path / "week.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _problems(path: Path) -> str:
    with pytest.raises(ManifestError) as e:
        load_week(path)
    return " | ".join(e.value.problems)


class Env:
    """One in-memory system: store + graph + fake Telegram/storage."""

    def __init__(self, tmp_path: Path):
        self.store = MemoryStore()
        self.services, self.tg, self.storage = make_services(tmp_path, self.store)
        self.graph = build_graph(InMemorySaver(), retry=FAST_RETRY)
        self.week = week42()

    async def send_all(self):
        return [await start(self.graph, self.services, self.week, p, "send") for p in self.week.posts]

    def cid(self, i: int) -> str:
        return self.week.posts[i - 1].campaign_id


# ── manifest ─────────────────────────────────────────────────────────────────

# md5 of public.campaigns.caption for the five W42 posts, as sent to Telegram on 9 Oct (read 15 Oct)
SENT_W42 = {
    "2026-W42-V4-001": "6c3829491e5ef0fec0da7783487ede0f",
    "2026-W42-V4-002": "3dc559651a125b92c23d88066c190a5a",
    "2026-W42-V4-003": "e00aca76c0231c276ade7fe9e48806e6",
    "2026-W42-V4-004": "68840dac774fdc43172613838601ee82",
    "2026-W42-V4-005": "01cdb1804adf042c9553ee01993c69aa",
}


def test_w42_manifest_rebuilds_the_captions_that_were_sent():
    week = week42()
    assert {p.campaign_id: hashlib.md5(p.caption.encode()).hexdigest() for p in week.posts} == SENT_W42
    assert [p.scheduled_day for p in week.posts] == ["Wed", "Thu", "Fri", "Sat", "Sun"]
    assert [p.stem for p in week.posts] == ["wed_singapore_airlines", "thu_beaune", "fri_cape_town",
                                            "sat_sao_paulo", "sun_europe"]
    assert [p.design.style.value for p in week.posts] == ["fact", "offer", "editorial", "offer", "price_hero"]


def test_manifest_day_outside_the_week(tmp_path):
    assert "is in 2026-W43, not 2026-W42" in _problems(_manifest(tmp_path, {"day = 2026-10-18": "day = 2026-10-19"}))


def test_manifest_days_out_of_order(tmp_path):
    assert "publishing order" in _problems(_manifest(tmp_path, {"day = 2026-10-15": "day = 2026-10-17"}))


def test_manifest_dollar_and_markdown_in_body(tmp_path):
    p = _manifest(tmp_path, {"a short hop away.": "a short hop away. From $2,000 *only*"})
    problems = _problems(p)
    assert "contains '$'" in problems and "contains *" in problems


def test_manifest_caption_too_long(tmp_path):
    problems = _problems(_manifest(tmp_path, {"a short hop away.": "a short hop away. " + "Long. " * 80}))
    assert "max 1024: shorten by" in problems


def test_manifest_design_fields_per_style(tmp_path):
    problems = _problems(_manifest(tmp_path, {'tag = "Hospices de Beaune · 15 Nov"\n': ""}))
    assert "design." in problems and "requires: tag" in problems


def test_manifest_week_mix(tmp_path):
    p = _manifest(tmp_path, {'style = "editorial"\nbig_headline = true\nheadline = "Cape Town. Summer, a night away."\n'
                             'line = "Business class from New York · November"':
                             'style = "price_hero"\nfollow = "Cape Town, a night away."\nline = "Business class"\n'
                             'to_iata = "CPT"'})
    assert "three priced posts in a row" in _problems(p)


def test_manifest_unknown_key(tmp_path):
    assert "Extra inputs are not permitted" in _problems(_manifest(tmp_path, {'id_tag = "V4"': 'id_tag = "V4"\nweak = 1'}))


def test_caption_rules():
    cid = "2026-W43-V4-001"
    assert caption_problems("Fine text.", cid) == []
    assert utf16_len("📋") == 2
    assert review_line(cid, 3) == f"\n\n📋 {cid} · v3"
    assert caption_from_edit("New body.", Style.EDITORIAL, "Nice") == compose_caption("New body.", Style.EDITORIAL, "Nice")
    full = compose_caption("Body.", Style.OFFER, "Paris")
    assert caption_from_edit(full, Style.OFFER, "Paris") == full
    ending_only = "Body.\n\nThe fare above is today's. Ask us for your dates."
    assert caption_from_edit(ending_only, Style.OFFER, "Paris") == ending_only + "\n\n" + CONTACT_LINE_LUX


def test_callback_data_round_trip():
    kb = review_keyboard("2026-W43-V4-001", 2)
    datas = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert datas == ["cs:a:2026-W43-V4-001:2", "cs:r:2026-W43-V4-001:2", "cs:e:2026-W43-V4-001:2"]
    assert all(len(d.encode()) <= 64 for d in datas)
    assert parse_callback(datas[0]) == ("approve", "2026-W43-V4-001", 2)
    assert parse_callback("approve_2026-W43-V4-001") is None
    assert parse_callback("cs:x:2026-W43-V4-001:2") is None


def test_db_url_rules():
    assert "not set" in db_url_problem("")
    assert "transaction pooler" in db_url_problem("postgresql://u:p@aws-1-eu-west-1.pooler.supabase.com:6543/postgres")
    assert db_url_problem("postgresql://u:p@aws-1-eu-west-1.pooler.supabase.com:5432/postgres") is None


# ── graph ────────────────────────────────────────────────────────────────────

def test_render_mode_makes_files_and_sends_nothing(tmp_path):
    env = Env(tmp_path)
    env.services.telegram = None

    async def go():
        return await asyncio.gather(*[start(env.graph, env.services, env.week, p, "render") for p in env.week.posts])

    views = run(go())
    assert [v.status for v in views] == ["rendered"] * 5
    for p in env.week.posts:
        assert (tmp_path / "2026-W42" / f"{p.stem}.jpg").exists()
        assert (tmp_path / "2026-W42" / f"{p.stem}.txt").read_text(encoding="utf-8") == p.caption
    assert env.store.campaigns == {} and env.storage.files == {}


def test_send_puts_five_posts_in_review(tmp_path):
    env = Env(tmp_path)
    views = run(env.send_all())
    assert all(v.waiting and v.status == "in_review" and v.version == 1 for v in views)
    photos = env.tg.sent("sendPhoto")
    assert len(photos) == 5
    for (_, data, _), p in zip(photos, env.week.posts, strict=True):
        assert data["caption"] == p.caption + review_line(p.campaign_id, 1)
        assert re.search(rf"/marketing/deals/{p.campaign_id}/vertical-[0-9a-f]{{12}}\.jpg$", data["photo"])
        assert data["reply_markup"] == review_keyboard(p.campaign_id, 1)
        row = env.store.campaigns[p.campaign_id]
        assert row["status"] == "draft" and row["caption"] == p.caption and row["scheduled_day"] == p.scheduled_day
        assert row["review_chat_id"] == CHAT and row["design_style"] == p.design.style.value
        assert "$" not in row["caption"]
    assert env.store.campaigns[env.cid(2)]["price"] == "$1,234"          # priced style: fare in DB, not caption
    assert env.store.campaigns[env.cid(1)]["price"] is None
    assert len([m for m in env.store.messages.values() if m["kind"] == "review"]) == 5
    assert len(env.storage.files) == 5


def test_send_twice_sends_nothing_new(tmp_path):
    env = Env(tmp_path)
    run(env.send_all())
    run(env.send_all())
    assert len(env.tg.sent("sendPhoto")) == 5 and len(env.storage.files) == 5


def test_approve_one_post_leaves_the_others_waiting(tmp_path):
    env = Env(tmp_path)

    async def go():
        await env.send_all()
        out = await decide(env.graph, env.services, env.cid(1), "approve", version=1)
        again = await decide(env.graph, env.services, env.cid(1), "approve", version=1)
        others = [await view(env.graph, env.cid(i)) for i in range(2, 6)]
        return out, again, others

    out, again, others = run(go())
    assert out.ok and out.message == "✅ Approved"
    assert not again.ok and again.message.startswith("Already approved")
    assert all(v.waiting for v in others)
    assert env.store.campaigns[env.cid(1)]["status"] == "approved"
    edits = env.tg.sent("editMessageReplyMarkup")
    assert len(edits) == 1
    assert edits[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Approved · v1 · Wed 14 Oct"


def test_reject(tmp_path):
    env = Env(tmp_path)

    async def go():
        await env.send_all()
        return await decide(env.graph, env.services, env.cid(3), "reject", version=1)

    out = run(go())
    assert out.ok and out.message == "❌ Rejected"
    assert env.store.campaigns[env.cid(3)]["status"] == "rejected"


def test_edit_makes_version_two_and_old_buttons_stop_working(tmp_path):
    env = Env(tmp_path)
    cid = env.cid(4)

    async def go():
        await env.send_all()
        first_msg = (await view(env.graph, cid)).values["review_message_id"]
        edit = await decide(env.graph, env.services, cid, "edit", caption="Interlagos, in three weeks. One flight.")
        stale = await decide(env.graph, env.services, cid, "approve", version=1)
        ok = await decide(env.graph, env.services, cid, "approve", version=2)
        return first_msg, edit, stale, ok

    first_msg, edit, stale, ok = run(go())
    assert edit.ok and edit.message == "✏️ v2 sent for review"
    assert not stale.ok and "latest is v2" in stale.message
    assert ok.ok
    v2 = env.tg.sent("sendPhoto")[-1][1]
    expected = compose_caption("Interlagos, in three weeks. One flight.", Style.OFFER, "São Paulo")
    assert v2["caption"] == expected + review_line(cid, 2)
    assert v2["photo"].startswith("F")                                   # re-sent by Telegram file id, no upload
    assert env.store.campaigns[cid]["caption"] == expected
    assert env.store.campaigns[cid]["status"] == "approved"
    marks = [c[1] for c in env.tg.sent("editMessageReplyMarkup")]
    assert {"message_id": first_msg, "text": "↪ Replaced by v2"} in [
        {"message_id": m["message_id"], "text": m["reply_markup"]["inline_keyboard"][0][0]["text"]} for m in marks]


def test_invalid_or_identical_edit_is_not_applied(tmp_path):
    env = Env(tmp_path)
    cid = env.cid(2)

    async def go():
        await env.send_all()
        bad = await decide(env.graph, env.services, cid, "edit", caption="Now from $999!")
        same = await decide(env.graph, env.services, cid, "edit", caption=env.week.posts[1].caption)
        return bad, same, await view(env.graph, cid)

    bad, same, v = run(go())
    assert not bad.ok and "contains '$'" in bad.message
    assert not same.ok and "same text" in same.message
    assert v.waiting and v.version == 1
    assert len(env.tg.sent("sendPhoto")) == 5


def test_transient_telegram_error_is_retried_and_sent_once(tmp_path):
    env = Env(tmp_path)
    env.tg.fail["sendPhoto"] = [TelegramError("sendPhoto", "Bad Gateway", transient=True)]
    views = run(env.send_all())
    assert all(v.waiting for v in views)
    assert len(env.tg.sent("sendPhoto")) == 6                            # one failed attempt + five posts
    assert len([m for m in env.store.messages.values() if m["kind"] == "review"]) == 5


def test_photo_url_refused_falls_back_to_the_file(tmp_path):
    env = Env(tmp_path)
    env.tg.fail["sendPhoto"] = [TelegramError("sendPhoto", "wrong file identifier/HTTP URL specified", transient=False)]
    views = run(env.send_all())
    assert all(v.waiting for v in views)
    first, retry = env.tg.sent("sendPhoto")[:2]
    assert first[2] is None and retry[2]["photo"][2] == "image/jpeg"


def test_a_campaign_that_is_no_longer_a_draft_is_never_overwritten(tmp_path):
    env = Env(tmp_path)
    env.store.campaigns[env.cid(5)] = {"campaign_id": env.cid(5), "status": "sent", "caption": "the real one"}
    views = run(env.send_all())
    assert views[4].status == "locked" and not views[4].waiting
    assert env.store.campaigns[env.cid(5)] == {"campaign_id": env.cid(5), "status": "sent", "caption": "the real one"}
    assert len(env.tg.sent("sendPhoto")) == 4
    assert not [f for f in env.storage.files if f.startswith(f"deals/{env.cid(5)}/")]   # not even uploaded


def test_a_failed_post_continues_where_it_stopped(tmp_path):
    env = Env(tmp_path)
    env.storage.fail = [RuntimeError("storage said no")]
    p = env.week.posts[0]
    with pytest.raises(RuntimeError, match="storage said no"):
        run(start(env.graph, env.services, env.week, p, "send"))
    assert run(view(env.graph, p.campaign_id)).status == "failed"
    v = run(start(env.graph, env.services, env.week, p, "send"))
    assert v.waiting and len(env.tg.sent("sendPhoto")) == 1


def test_memory_store_contract():
    store = MemoryStore()

    async def go():
        calls = []

        async def effect():
            calls.append(1)
            return 42

        assert await store.once("k", effect) == "42"
        assert await store.once("k", effect) == "42"
        assert calls == [1]
        store.effects["busy"] = {"status": "running", "external_id": None, "attempts": 1, "t": 1e18}
        with pytest.raises(EffectBusy):
            await store.once("busy", effect)
        assert await store.claim("u1") and not await store.claim("u1")
        await store.release("u1")
        assert await store.claim("u1")
        async with store.lease("post:x", wait=1):
            with pytest.raises(LeaseBusy):
                async with store.lease("post:x", wait=0.1):
                    pass
        async with store.lease("post:x", wait=0.1):
            pass

    run(go())


# ── Telegram bridge ──────────────────────────────────────────────────────────

@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENABLED", "true")
    get_settings.cache_clear()
    env = Env(tmp_path)
    bridge.use(bridge.Live(settings=get_settings(), graph=env.graph, services=env.services))
    yield env
    bridge.use(None)
    get_settings.cache_clear()


def _cb(data: str, message_id: int = 1, cb_id: str = "cb") -> dict:
    return {"id": cb_id, "data": data, "from": {"id": 1}, "message": {"message_id": message_id, "chat": {"id": CHAT}}}


def _toasts(tg: FakeTelegram) -> list[str]:
    return [c[1]["text"] for c in tg.sent("answerCallbackQuery")]


def test_bridge_approve_and_duplicate_update(live):
    env = live

    async def go():
        await env.send_all()
        await bridge.handle_callback(_cb(f"cs:a:{env.cid(1)}:1"), update_id=10)
        await bridge.handle_callback(_cb(f"cs:a:{env.cid(1)}:1"), update_id=10)      # same update again
        await bridge.handle_callback(_cb(f"cs:a:{env.cid(1)}:1"), update_id=11)      # a second tap

    run(go())
    assert _toasts(env.tg) == ["✅ Approved", "", "Already approved — nothing changed"]
    assert env.store.campaigns[env.cid(1)]["status"] == "approved"


def test_bridge_edit_by_reply(live):
    env = live
    cid = env.cid(3)

    async def go():
        await env.send_all()
        await bridge.handle_callback(_cb(f"cs:e:{cid}:1"), update_id=20)
        prompt = env.tg.sent("sendMessage")[-1]
        prompt_id = env.tg._next
        handled = await bridge.handle_reply(
            {"message_id": 500, "chat": {"id": CHAT}, "text": "Summer on the Cape, a night away.",
             "reply_to_message": {"message_id": prompt_id}}, update_id=21)
        unrelated = await bridge.handle_reply(
            {"message_id": 501, "chat": {"id": CHAT}, "text": "hello", "reply_to_message": {"message_id": 1}},
            update_id=22)
        return prompt, handled, unrelated, await view(env.graph, cid)

    prompt, handled, unrelated, v = run(go())
    assert prompt[1]["reply_markup"]["force_reply"] is True and cid in prompt[1]["text"]
    assert handled and not unrelated
    assert v.waiting and v.version == 2
    assert env.tg.sent("sendPhoto")[-1][1]["caption"].endswith(review_line(cid, 2))


def test_bridge_reply_with_bad_caption_explains(live):
    env = live
    cid = env.cid(2)

    async def go():
        await env.send_all()
        review_id = (await view(env.graph, cid)).values["review_message_id"]
        await bridge.handle_reply({"message_id": 600, "chat": {"id": CHAT}, "text": "Only $500 today",
                                   "reply_to_message": {"message_id": review_id}}, update_id=30)
        return await view(env.graph, cid)

    v = run(go())
    assert v.version == 1
    assert "Not applied" in env.tg.sent("sendMessage")[-1][1]["text"]


def test_a_database_that_stops_answering_never_reaches_the_webhook(live):
    """Found by CodeRabbit: the pool is open, so live() succeeds, and then claim() raises. The tap and
    the reply must be answered, and nothing may raise into the webhook."""
    env = live
    cid = env.cid(1)

    async def down(key):
        raise ConnectionError("server closed the connection unexpectedly")

    async def go():
        await env.send_all()
        review_id = (await view(env.graph, cid)).values["review_message_id"]
        real_claim, env.store.claim = env.store.claim, down
        await bridge.handle_callback(_cb(f"cs:a:{cid}:1"), update_id=70)
        handled = await bridge.handle_reply({"message_id": 700, "chat": {"id": CHAT}, "text": "A new caption.",
                                             "reply_to_message": {"message_id": review_id}}, update_id=71)
        env.store.claim = real_claim
        return handled, await view(env.graph, cid)

    handled, v = run(go())
    assert _toasts(env.tg)[-1] == "⚠️ The content system can't reach its database — try again later"
    assert handled is True and "can't reach its database" in env.tg.sent("sendMessage")[-1][1]["text"]
    assert v.waiting and v.version == 1 and env.store.campaigns[cid]["status"] == "draft"      # nothing changed


def test_a_release_that_fails_still_answers_the_tap(live, monkeypatch):
    """The decision failed and the database is why: release() fails too, and the toast still goes out."""
    env = live
    cid = env.cid(1)

    async def boom(*a, **k):
        raise RuntimeError("decide failed")

    async def down(key):
        raise ConnectionError("server closed the connection unexpectedly")

    async def go():
        await env.send_all()
        monkeypatch.setattr(bridge, "decide", boom)
        monkeypatch.setattr(env.store, "release", down)
        await bridge.handle_callback(_cb(f"cs:a:{cid}:1"), update_id=80)
        review_id = (await view(env.graph, cid)).values["review_message_id"]
        return await bridge.handle_reply({"message_id": 800, "chat": {"id": CHAT}, "text": "A new caption.",
                                          "reply_to_message": {"message_id": review_id}}, update_id=81)

    handled = run(go())
    assert _toasts(env.tg)[-1] == "⚠️ That didn't go through — tap again (the error is in the logs)"
    assert handled is True and env.tg.sent("sendMessage")[-1][1]["text"].endswith("the edit didn't go through — send it again")


def test_bridge_off_answers_and_lets_replies_through(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENABLED", "false")
    get_settings.cache_clear()
    calls = []

    async def fake_call(self, method, data, *, files=None):
        calls.append((method, data))

    monkeypatch.setattr("content_system.telegram.TelegramAPI.call", fake_call)
    monkeypatch.setattr("config.settings.telegram_bot_token", "123:abc")
    run(bridge.handle_callback(_cb("cs:a:2026-W43-V4-001:1"), update_id=1))
    assert calls[0][1]["text"].startswith("The content system is off")
    assert run(bridge.handle_reply({"chat": {"id": 1}, "reply_to_message": {"message_id": 2}, "text": "x"}, 2)) is False
    get_settings.cache_clear()


def test_webhook_routes_cs_buttons_to_the_content_system(monkeypatch):
    from fastapi.testclient import TestClient

    import webhook
    from config import settings

    seen = []

    async def fake_handle(cb, update_id):
        seen.append((cb["data"], update_id))

    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "telegram_admin_ids", "42")
    monkeypatch.setattr(bridge, "handle_callback", fake_handle)
    client = TestClient(webhook.app)
    resp = client.post("/webhook/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"}, json={
        "update_id": 7, "callback_query": {"id": "c", "data": "cs:a:2026-W43-V4-001:1", "from": {"id": 42},
                                           "message": {"message_id": 3, "chat": {"id": 42}}}})
    assert resp.status_code == 200 and seen == [("cs:a:2026-W43-V4-001:1", 7)]


def test_real_render_through_the_graph(tmp_path):
    """The CLI path: Playwright's sync renderer in a worker thread, inside the async graph."""
    pytest.importorskip("playwright.sync_api")
    import numpy as np
    from PIL import Image

    from content_system.adapters import render_files

    photos = tmp_path / "photos"
    photos.mkdir()
    Image.fromarray(np.full((1920, 1080, 3), 230, dtype="uint8")).save(photos / "europe.jpg")
    text = W42.read_text(encoding="utf-8").replace('photos = "assets/curated/week_2026_10_12"',
                                                   f'photos = "{photos.as_posix()}"')
    manifest = tmp_path / "w.toml"
    manifest.write_text(text, encoding="utf-8")
    week = load_week(manifest)
    services, _, _ = make_services(tmp_path / "out", MemoryStore())
    services.render = render_files
    v = run(start(build_graph(InMemorySaver()), services, week, week.posts[4], "render"))
    assert v.status == "rendered" and v.values["fare"] and v.values["text_bottom"] <= 0.45
    with Image.open(v.values["image_path"]) as im:
        assert im.size == (1080, 1920)


def test_a_post_changed_before_reaching_telegram_starts_over(tmp_path):
    env = Env(tmp_path)
    env.storage.fail = [RuntimeError("storage down")]
    first = env.week.posts[1]
    with pytest.raises(RuntimeError):
        run(start(env.graph, env.services, env.week, first, "send"))
    fixed = load_week(_manifest(tmp_path, {"Beaune. One weekend. All of Burgundy.": "Beaune. All of Burgundy."}))
    v = run(start(env.graph, env.services, fixed, fixed.posts[1], "send"))
    assert v.waiting and not v.plan_changed
    assert v.values["design"]["headline"] == "Beaune. All of Burgundy."
    assert len(env.tg.sent("sendPhoto")) == 1


def test_a_post_changed_after_reaching_telegram_is_only_reported(tmp_path):
    env = Env(tmp_path)
    run(env.send_all())
    changed = load_week(_manifest(tmp_path, {"a short hop away.": "a short flight away."}))
    v = run(start(env.graph, env.services, changed, changed.posts[0], "send"))
    assert v.plan_changed and v.waiting and v.version == 1
    assert len(env.tg.sent("sendPhoto")) == 5


# ── failures half-way (found by the independent review) ──────────────────────

def _mark_texts(tg: FakeTelegram) -> list[str]:
    return [c[1]["reply_markup"]["inline_keyboard"][0][0]["text"] for c in tg.sent("editMessageReplyMarkup")]


def test_a_retried_approve_still_ends_approved(tmp_path):
    """The approve is written, then Telegram fails once on the buttons: the retry must not call it 'already approved'."""
    env = Env(tmp_path)
    cid = env.cid(1)
    env.tg.fail["editMessageReplyMarkup"] = [TelegramError("editMessageReplyMarkup", "Bad Gateway", transient=True)]

    async def go():
        await env.send_all()
        return await decide(env.graph, env.services, cid, "approve", version=1)

    out = run(go())
    assert out.ok and out.message == "✅ Approved" and out.view.status == "approved"
    assert env.store.campaigns[cid]["status"] == "approved"
    assert set(_mark_texts(env.tg)) == {"✅ Approved · v1 · Wed 14 Oct"}


def _db_down_for(store, times: int):
    real, left = store.decide, {"n": times}

    async def flaky(*args):
        if left["n"] > 0:
            left["n"] -= 1
            raise ConnectionError("database down")
        return await real(*args)

    store.decide = flaky


def test_a_failed_approve_does_not_decide_a_different_next_tap(tmp_path):
    """Betty was told the approve didn't go through; when she taps Reject, Reject is what happens."""
    env = Env(tmp_path)
    cid = env.cid(1)

    async def go():
        await env.send_all()
        _db_down_for(env.store, 3)                      # every attempt of the approve fails
        with pytest.raises(ConnectionError):
            await decide(env.graph, env.services, cid, "approve", version=1)
        failed = await view(env.graph, cid)
        return failed, await decide(env.graph, env.services, cid, "reject", version=1)

    failed, out = run(go())
    assert failed.status == "failed"
    assert out.ok and out.message == "❌ Rejected"
    assert env.store.campaigns[cid]["status"] == "rejected"


def test_the_same_tap_again_completes_a_failed_approve(tmp_path):
    env = Env(tmp_path)
    cid = env.cid(2)

    async def go():
        await env.send_all()
        _db_down_for(env.store, 3)
        with pytest.raises(ConnectionError):
            await decide(env.graph, env.services, cid, "approve", version=1)
        return await decide(env.graph, env.services, cid, "approve", version=1)

    out = run(go())
    assert out.ok and out.message == "✅ Approved"
    assert env.store.campaigns[cid]["status"] == "approved"


def _refuse_photos(tg: FakeTelegram, n: int) -> None:
    tg.fail["sendPhoto"] = [TelegramError("sendPhoto", "Bad Request: chat not found", transient=False)
                            for _ in range(n)]


def test_an_edit_that_never_reached_telegram_is_dropped_by_an_approve(tmp_path):
    """v2 could not be sent; Betty approves what she saw (v1), and v1's text is what gets stored."""
    env = Env(tmp_path)
    p = env.week.posts[3]

    async def go():
        await env.send_all()
        _refuse_photos(env.tg, 2)                      # by file id, then the file itself
        with pytest.raises(TelegramError):
            await decide(env.graph, env.services, p.campaign_id, "edit", caption="Interlagos. Three weeks.")
        return await decide(env.graph, env.services, p.campaign_id, "approve", version=1)

    out = run(go())
    assert out.ok and out.message == "✅ Approved" and out.view.version == 1
    row = env.store.campaigns[p.campaign_id]
    assert row["status"] == "approved" and row["caption"] == p.caption
    assert not [k for k in env.store.effects if k.startswith(f"review:{p.campaign_id}:v2")]   # refused send left no trace


def test_after_a_dropped_edit_the_next_edit_gets_a_new_version_number(tmp_path):
    env = Env(tmp_path)
    cid = env.cid(4)

    async def go():
        await env.send_all()
        _refuse_photos(env.tg, 2)
        with pytest.raises(TelegramError):
            await decide(env.graph, env.services, cid, "edit", caption="First try.")
        return await decide(env.graph, env.services, cid, "edit", caption="Second try.")

    out = run(go())
    assert out.ok and out.message == "✏️ v3 sent for review"
    assert env.tg.sent("sendPhoto")[-1][1]["caption"].endswith(review_line(cid, 3))


def test_a_refused_first_send_leaves_no_trace(tmp_path):
    """Telegram refused the message (wrong chat, say): nothing reached Telegram, so a fixed manifest starts over."""
    env = Env(tmp_path)
    p = env.week.posts[0]
    _refuse_photos(env.tg, 2)
    with pytest.raises(TelegramError):
        run(start(env.graph, env.services, env.week, p, "send"))
    assert run(env.store.reached_telegram(p.campaign_id)) is False


BOT = 4242


@pytest.fixture
def bot_token(monkeypatch):
    monkeypatch.setattr("config.settings.telegram_bot_token", f"{BOT}:test-token")


def test_ours_recognises_only_this_bots_review_and_edit_messages(bot_token):
    review = {"from": {"id": BOT, "is_bot": True}, "caption": "Text.\n\n📋 2026-W43-V4-002 · v3"}
    prompt = {"from": {"id": BOT, "is_bot": True}, "text": "✏️ 2026-W43-V4-002 · v3\nReply to this message …"}
    assert bridge.ours(review) == ("2026-W43-V4-002", 3)
    assert bridge.ours(prompt) == ("2026-W43-V4-002", 3)
    assert bridge.ours({**review, "from": {"id": 999, "is_bot": True}}) is None      # another bot
    assert bridge.ours({**review, "from": {"id": BOT, "is_bot": False}}) is None    # a person quoting it
    assert bridge.ours({"from": {"id": BOT, "is_bot": True}, "caption": "📰 2026-W43 · 5 posts"}) is None


def test_a_reply_to_a_review_message_that_was_never_recorded_still_works(live, bot_token):
    """Telegram accepted sendPhoto but the answer was lost: the retry sends a second copy. Replies to the first work."""
    env = live
    cid = env.cid(1)
    real = env.tg.call
    lost = {"done": False}

    async def lossy(method, data, *, files=None):
        result = await real(method, data, files=files)
        if method == "sendPhoto" and not lost["done"]:
            lost["done"] = True
            raise TelegramError("sendPhoto", "ReadTimeout", transient=True)   # it did go out
        return result

    env.tg.call = lossy

    async def go():
        await env.send_all()
        orphan = env.tg.sent("sendPhoto")[0]
        orphan_id = 1001                                     # FakeTelegram's first message id
        assert await env.store.find_message(CHAT, orphan_id) is None
        handled = await bridge.handle_reply(
            {"message_id": 700, "chat": {"id": CHAT}, "text": "A new body.",
             "reply_to_message": {"message_id": orphan_id, "from": {"id": BOT, "is_bot": True},
                                  "caption": orphan[1]["caption"]}}, update_id=40)
        return handled, await view(env.graph, cid)

    handled, v = run(go())
    assert handled and v.waiting and v.version == 2


def test_a_reply_while_the_database_is_down_gets_an_answer(tmp_path, monkeypatch, bot_token):
    monkeypatch.setenv("CONTENT_ENABLED", "true")
    get_settings.cache_clear()
    said = []

    async def fake_call(self, method, data, *, files=None):
        said.append((method, data))

    async def no_db():
        raise ConnectionError("pool timeout")

    monkeypatch.setattr("content_system.telegram.TelegramAPI.call", fake_call)
    monkeypatch.setattr(bridge, "_build", no_db)
    bridge.use(None)
    ours_reply = {"message_id": 5, "chat": {"id": CHAT}, "text": "New text",
                  "reply_to_message": {"message_id": 9, "from": {"id": BOT, "is_bot": True},
                                       "caption": "…\n\n📋 2026-W43-V4-001 · v1"}}
    other_reply = {**ours_reply, "reply_to_message": {"message_id": 9, "from": {"id": 7, "is_bot": False},
                                                      "text": "hi"}}
    assert run(bridge.handle_reply(ours_reply, 1)) is True
    assert "can't reach its database" in said[-1][1]["text"]
    assert run(bridge.handle_reply(other_reply, 2)) is False                 # the old handlers get it
    get_settings.cache_clear()
