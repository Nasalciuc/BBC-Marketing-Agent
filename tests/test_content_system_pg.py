"""
Content system on a real Postgres: the migrations, the content_app role, checkpoints, leases, effects.

Runs only with CONTENT_TEST_DB_URL = a throwaway Postgres superuser URL (CI starts one); every test
creates its own database, applies migrations 000→011 exactly as they are in the repo, and connects
as content_app — the same least-privilege role production uses.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest

from content_system.db import Database
from content_system.engine import decide, start, view
from content_system.graph import build_graph
from content_system.manifest import compose_caption, load_week
from content_system.store import EffectBusy, LeaseBusy, PgStore
from services.design_system import Style
from tests.content_fakes import CHAT, FAST_RETRY, ROOT, make_services, week42

ADMIN_URL = os.environ.get("CONTENT_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="CONTENT_TEST_DB_URL not set")

MIGRATIONS = (
    "000_baseline_schema.sql", "005_review_tracking.sql", "006_chat_context.sql",
    "008_campaign_scheduled_day.sql", "009_campaign_design_fields.sql",
    "010_rls_and_campaign_statuses.sql", "011_content_system.sql", "012_assistant.sql", "013_plans.sql",
)
APP_PASSWORD = "content-test-password"


def run(coro):
    """psycopg's async mode can't use Windows' default Proactor loop."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)


def _url(base: str, *, dbname: str | None = None, user: str | None = None, password: str | None = None) -> str:
    p = urlsplit(base)
    host = p.hostname or "localhost"
    netloc = host + (f":{p.port}" if p.port else "")
    u = user if user is not None else p.username
    pw = password if password is not None else p.password
    if u:
        netloc = quote(u) + (f":{quote(pw)}" if pw else "") + "@" + netloc
    return urlunsplit((p.scheme, netloc, f"/{dbname}" if dbname else p.path, p.query, ""))


@pytest.fixture
def pg():
    name = f"cs_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
        for role in ("anon", "authenticated", "service_role"):
            admin.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') "
                          f"then create role {role} nologin; end if; end $$")
    admin_db = _url(ADMIN_URL, dbname=name)
    with psycopg.connect(admin_db, autocommit=True) as conn:
        for f in MIGRATIONS:
            conn.execute((ROOT / "migrations" / f).read_text(encoding="utf-8"))
        conn.execute((ROOT / "migrations" / "011_content_system.sql").read_text(encoding="utf-8"))  # idempotent
        conn.execute((ROOT / "migrations" / "012_assistant.sql").read_text(encoding="utf-8"))       # idempotent
        conn.execute((ROOT / "migrations" / "013_plans.sql").read_text(encoding="utf-8"))           # idempotent
        conn.execute(f"alter role content_app with login password '{APP_PASSWORD}'")
    yield {"app": _url(admin_db, user="content_app", password=APP_PASSWORD), "admin": admin_db}
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


def admin_rows(pg, query: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


async def system(pg, tmp_path):
    db = Database(pg["app"], max_size=4)
    await db.open()
    await db.setup()
    store = PgStore(db.pool)
    services, tg, storage = make_services(tmp_path, store)
    return db, services, tg, build_graph(db.checkpointer(), retry=FAST_RETRY)


def test_setup_and_verify(pg, tmp_path):
    async def go():
        db, *_ = await system(pg, tmp_path)
        try:
            return await db.verify()
        finally:
            await db.close()

    problems, notes = run(go())
    assert problems == []
    assert notes[0] == "connected as content_app, schema content"


def test_setup_and_verify_with_the_assistant_tables(pg, tmp_path):
    async def go():
        db, *_ = await system(pg, tmp_path)
        try:
            return await db.verify(assistant=True)
        finally:
            await db.close()

    assert run(go())[0] == []
    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        conn.execute("drop table content.ideas")

    problems = run(go())[0]
    assert problems == ["content.ideas missing (migrations/012)"]


def test_a_card_is_used_once_even_when_two_taps_race(pg, tmp_path):
    """migrations/012: the first tap takes the card; a parallel second tap (another worker) gets nothing."""
    async def go():
        db, services, *_ = await system(pg, tmp_path)
        store = services.store
        try:
            items = [{"campaign_id": "2026-W45-V4-001", "version": 2, "label": "Wed 04 Nov · Monaco"}]
            await store.add_proposal("aaaaaaaaaaa1", "ggggggggggg1", CHAT, "approve", items)
            await store.add_proposal("aaaaaaaaaaa2", "ggggggggggg1", CHAT, "approve", items)
            first, second = await asyncio.gather(store.take_proposal("aaaaaaaaaaa1"),
                                                 store.take_proposal("aaaaaaaaaaa1"))
            sibling = await store.take_proposal("aaaaaaaaaaa2")
            await store.add_proposal("bbbbbbbbbbb1", "hhhhhhhhhhh1", CHAT, "reject", items)
            dismissed = await store.dismiss_group("hhhhhhhhhhh1")
            after_dismiss = await store.take_proposal("bbbbbbbbbbb1")
            await store.add_proposal("ccccccccccc1", "iiiiiiiiiii1", CHAT, "approve", items)
            return first, second, sibling, dismissed, after_dismiss, store
        finally:
            await db.close()

    first, second, sibling, dismissed, after_dismiss, _ = run(go())
    outcomes = sorted([first[0], second[0]])
    assert outcomes == ["taken", "used"]
    taken = first if first[0] == "taken" else second
    assert taken[1]["action"] == "approve" and taken[1]["items"][0]["version"] == 2
    assert sibling == ("dismissed", None) and dismissed == 1 and after_dismiss == ("dismissed", None)
    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        conn.execute("update content.proposals set created_at = now() - interval '49 hours' where id = 'ccccccccccc1'")

    async def expired():
        db, services, *_ = await system(pg, tmp_path)
        try:
            return await services.store.take_proposal("ccccccccccc1"), await services.store.take_proposal("nonexistent0")
        finally:
            await db.close()

    assert run(expired()) == (("expired", None), ("missing", None))


def test_two_buttons_of_one_card_racing_decide_once(pg, tmp_path):
    """Found in review: "❌ A" and "❌ toate" tapped together both won (each row was open on its own).
    The card's rows are now taken in one transaction."""
    async def go():
        db, services, *_ = await system(pg, tmp_path)
        store, outcomes = services.store, []
        try:
            items = [{"campaign_id": "2026-W45-V4-001", "version": 1}]
            for i in range(20):
                group, one, every = f"g{i:011d}", f"a{i:011d}", f"b{i:011d}"
                await store.add_proposal(one, group, CHAT, "reject", items)
                await store.add_proposal(every, group, CHAT, "reject", items)
                got = await asyncio.gather(store.take_proposal(one), store.take_proposal(every))
                outcomes.append(tuple(sorted(o for o, _ in got)))
            return outcomes
        finally:
            await db.close()

    assert set(run(go())) == {("dismissed", "taken")}


def test_ideas_usage_and_reviewed_posts_on_postgres(pg, tmp_path):
    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        store = services.store
        try:
            n = await store.add_idea(CHAT, 1, "Gala de la Monaco în decembrie")
            await store.add_usage("claude-haiku-5-5", 2, 2200, 90, 0.000265)
            await store.add_usage("claude-haiku-5-5", 1, 1000, 10, 0.000105)
            await store.add_usage("claude-sonnet-5-5", 1, 2000, 300, 0.007)
            spent = await store.usage_today()
            week = week42()
            for p in week.posts[:2]:
                await start(graph, services, week, p, "send")
            return n, spent, await store.reviewed_posts()
        finally:
            await db.close()

    n, spent, posts = run(go())
    assert n == 1 and spent == pytest.approx(0.00737)
    assert posts == ["2026-W42-V4-002", "2026-W42-V4-001"]
    assert admin_rows(pg, "select calls, input_tokens from content.usage where model = 'claude-haiku-5-5'") \
        == [(3, 3200)]
    assert admin_rows(pg, "select text, status from content.ideas") == [("Gala de la Monaco în decembrie", "new")]


def test_content_app_can_touch_only_what_it_needs(pg):
    with psycopg.connect(pg["app"], autocommit=True) as conn:
        assert conn.execute("select count(*) from public.campaigns").fetchone() == (0,)   # RLS on, policy lets it in
        for forbidden in ("select * from public.contacts", "select * from public.job_runs",
                          "select * from public.broadcast_log", "delete from public.campaigns",
                          "create table public.x (a int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(forbidden)
    rls = admin_rows(pg, "select relname, relrowsecurity from pg_class where relname in "
                         "('campaigns', 'job_runs', 'broadcast_log', 'chat_context', 'contacts') order by relname")
    assert all(on for _, on in rls)


def test_full_week_on_postgres(pg, tmp_path):
    week = week42()
    cids = [p.campaign_id for p in week.posts]

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            for p in week.posts:
                await start(graph, services, week, p, "send")
            assert (await decide(graph, services, cids[0], "approve", version=1)).ok
            assert (await decide(graph, services, cids[1], "edit", caption="Beaune, in a month. One flight.")).ok
            assert (await decide(graph, services, cids[2], "reject", version=1)).ok
            return tg, await view(graph, cids[1])
        finally:
            await db.close()

    tg, v2 = run(go())
    rows = admin_rows(pg, "select campaign_id, status, approved_at is not null, caption, review_message_id, "
                          "scheduled_day, design_style from public.campaigns order by campaign_id")
    assert [r[1] for r in rows] == ["approved", "draft", "rejected", "draft", "draft"]
    assert [r[2] for r in rows] == [True, False, False, False, False]
    assert rows[1][3] == compose_caption("Beaune, in a month. One flight.", Style.OFFER, "Paris")
    assert rows[1][4] == v2.values["review_message_id"]
    assert [r[5] for r in rows] == ["Wed", "Thu", "Fri", "Sat", "Sun"]
    assert len(tg.sent("sendPhoto")) == 6
    effects = dict(admin_rows(pg, "select split_part(key, ':', 1), count(*) from content.effects "
                                  "where status = 'done' group by 1"))
    assert effects == {"upload": 5, "review": 6, "status": 2, "supersede": 1}
    kinds = admin_rows(pg, "select kind, count(*) from content.messages group by kind")
    assert kinds == [("review", 6)]
    assert admin_rows(pg, "select count(*) from content.leases") == [(0,)]


def test_the_webhook_resumes_what_the_cli_started(pg, tmp_path):
    week = week42()
    cid = week.posts[0].campaign_id

    async def go():
        db, services, _, graph = await system(pg, tmp_path)           # "railway run … send"
        await start(graph, services, week, week.posts[0], "send")
        await db.close()
        db2, services2, tg2, graph2 = await system(pg, tmp_path)      # the webhook, another process
        try:
            return await decide(graph2, services2, cid, "approve", version=1), tg2
        finally:
            await db2.close()

    out, tg2 = run(go())
    assert out.ok
    assert len(tg2.sent("editMessageReplyMarkup")) == 1
    assert admin_rows(pg, "select status from public.campaigns where campaign_id = %s", (cid,)) == [("approved",)]


def test_double_tap_and_parallel_posts(pg, tmp_path):
    week = week42()
    a, b = week.posts[0].campaign_id, week.posts[1].campaign_id

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            for p in week.posts[:2]:
                await start(graph, services, week, p, "send")
            taps = await asyncio.gather(decide(graph, services, a, "approve", version=1),
                                        decide(graph, services, a, "approve", version=1),
                                        decide(graph, services, b, "reject", version=1))
            return taps, tg
        finally:
            await db.close()

    taps, tg = run(go())
    assert sorted(t.ok for t in taps[:2]) == [False, True]
    assert taps[2].ok
    assert len(tg.sent("editMessageReplyMarkup")) == 2                # one per post, not per tap
    assert admin_rows(pg, "select status from public.campaigns order by campaign_id") == [("approved",), ("rejected",)]


def test_leases_and_effects(pg, tmp_path):
    async def go():
        db, services, _, _ = await system(pg, tmp_path)
        store = services.store
        try:
            async with db.pool.connection() as conn:
                await conn.execute("insert into leases values ('post:stale', gen_random_uuid(), now() - interval '1 minute'), "
                                   "('post:live', gen_random_uuid(), now() + interval '5 minutes')")
                await conn.execute("insert into effects (key, status, updated_at) values "
                                   "('old', 'running', now() - interval '10 minutes'), ('fresh', 'running', now())")
            async with store.lease("post:stale", wait=1):
                pass
            with pytest.raises(LeaseBusy):
                async with store.lease("post:live", wait=0.3):
                    pass

            async def effect():
                return "x1"

            assert await store.once("old", effect) == "x1"
            with pytest.raises(EffectBusy):
                await store.once("fresh", effect)
            assert await store.claim("tg:update:1") and not await store.claim("tg:update:1")
            await store.release("tg:update:1")
            assert await store.claim("tg:update:1")
        finally:
            await db.close()

    run(go())
    assert admin_rows(pg, "select attempts, status from content.effects where key = 'old'") == [(2, "done")]


def test_existing_campaigns_are_never_taken_over(pg, tmp_path):
    week = week42()
    first, last = week.posts[0], week.posts[4]
    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        conn.execute("insert into public.campaigns (campaign_id, event_name, status, caption) values "
                     "(%s, 'old W42 post', 'draft', 'old'), (%s, 'sent post', 'sent', 'the real one')",
                     (first.campaign_id, last.campaign_id))

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            foreign = await services.store.foreign_campaigns([p.campaign_id for p in week.posts])
            locked = await start(graph, services, week, last, "send")
            return foreign, locked, tg
        finally:
            await db.close()

    foreign, locked, tg = run(go())
    assert foreign == [first.campaign_id, last.campaign_id]
    assert locked.status == "locked" and tg.sent("sendPhoto") == []
    assert admin_rows(pg, "select caption, status from public.campaigns where campaign_id = %s",
                      (last.campaign_id,)) == [("the real one", "sent")]


def test_a_failed_post_starts_over_after_a_manifest_fix(pg, tmp_path):
    week = week42()
    post = week.posts[1]
    fixed_text = (ROOT / "weeks" / "2026-W42.toml").read_text(encoding="utf-8").replace(
        "Beaune. One weekend. All of Burgundy.", "Beaune. All of Burgundy.")
    (tmp_path / "w.toml").write_text(fixed_text, encoding="utf-8")

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            storage_fail = services.put_image

            def down(data, path):
                raise RuntimeError("storage down")

            services.put_image = down
            with pytest.raises(RuntimeError):
                await start(graph, services, week, post, "send")
            assert (await view(graph, post.campaign_id)).status == "failed"
            services.put_image = storage_fail
            fixed = load_week(tmp_path / "w.toml")
            return await start(graph, services, fixed, fixed.posts[1], "send"), tg
        finally:
            await db.close()

    v, tg = run(go())
    assert v.waiting and v.values["design"]["headline"] == "Beaune. All of Burgundy."
    assert len(tg.sent("sendPhoto")) == 1
    assert admin_rows(pg, "select count(distinct thread_id) from content.checkpoints") == [(1,)]


def test_the_database_itself_refuses_to_touch_a_closed_campaign(pg, tmp_path):
    """migrations/011 policies: content_app inserts only drafts and updates only draft/editing rows."""
    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        conn.execute("insert into public.campaigns (campaign_id, event_name, status, caption) values "
                     "('2026-W42-V4-005', 'sent post', 'sent', 'the real one')")
    with psycopg.connect(pg["app"], autocommit=True) as conn:
        assert conn.execute("update public.campaigns set caption = 'x' "
                            "where campaign_id = '2026-W42-V4-005'").rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into public.campaigns (campaign_id, event_name, status) "
                         "values ('2026-W43-V4-001', 'x', 'approved')")

    async def go():
        db, services, _, _ = await system(pg, tmp_path)
        try:
            return await services.store.upsert_campaign({"campaign_id": "2026-W42-V4-005", "event_name": "x",
                                                         "caption": "overwrite"})
        finally:
            await db.close()

    assert run(go()) is False
    assert admin_rows(pg, "select caption, status from public.campaigns where campaign_id = '2026-W42-V4-005'") \
        == [("the real one", "sent")]


@pytest.mark.parametrize("final", ["sent", "cancelled"])
def test_no_writer_can_reopen_a_sent_or_cancelled_campaign(pg, final):
    """migrations/010 trigger. Found in review: save_campaign upserts, and discover.py reuses ids within a
    week, so saving a week again would have turned a sent campaign back into a draft — sendable again."""
    with psycopg.connect(pg["admin"], autocommit=True) as conn:       # postgres: what the SQL editor and service_role do
        conn.execute("insert into public.campaigns (campaign_id, event_name, status, caption) values "
                     "('2026-W45-001', 'Monaco', %s, 'the real one'), ('2026-W45-002', 'Paris', 'draft', 'd')",
                     (final,))
        with pytest.raises(psycopg.errors.CheckViolation, match=f"is {final} and stays {final}"):
            conn.execute("insert into public.campaigns (campaign_id, event_name, status, caption) "
                         "values ('2026-W45-001', 'Monaco', 'draft', 'rediscovered') "
                         "on conflict (campaign_id) do update set status = excluded.status, caption = excluded.caption")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("update public.campaigns set status = 'approved' where campaign_id = '2026-W45-001'")
        conn.execute("update public.campaigns set status = %s where campaign_id = '2026-W45-001'", (final,))  # no change
        conn.execute("update public.campaigns set status = 'approved' where campaign_id = '2026-W45-002'")
    assert admin_rows(pg, "select campaign_id, status, caption from public.campaigns "
                          "where campaign_id in ('2026-W45-001', '2026-W45-002') order by 1") \
        == [("2026-W45-001", final, "the real one"), ("2026-W45-002", "approved", "d")]


def test_a_failed_approve_then_a_reject_on_postgres(pg, tmp_path):
    """The branch from the old pause (update_state) works on the Postgres checkpointer too."""
    week = week42()
    cid = week.posts[0].campaign_id

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            await start(graph, services, week, week.posts[0], "send")
            real = services.store.decide

            async def down(*args):
                raise ConnectionError("database down")

            services.store.decide = down
            with pytest.raises(ConnectionError):
                await decide(graph, services, cid, "approve", version=1)
            services.store.decide = real
            return await decide(graph, services, cid, "reject", version=1)
        finally:
            await db.close()

    out = run(go())
    assert out.ok and out.message == "❌ Rejected"
    assert admin_rows(pg, "select status from public.campaigns where campaign_id = %s", (cid,)) == [("rejected",)]


def test_the_assistant_on_postgres_proposes_and_a_tap_approves(pg, tmp_path, monkeypatch):
    """The whole path on Postgres: the chat's memory in the checkpointer, the card in content.proposals,
    the tap deciding public.campaigns — with a scripted model, no API call."""
    from content_system import bridge
    from content_system.assistant import router
    from content_system.assistant.agent import Assistant
    from content_system.settings import get_settings
    from tests.test_assistant import Script, Writer, call, say

    monkeypatch.setenv("CONTENT_ENABLED", "true")
    monkeypatch.setenv("CONTENT_ASSISTANT_ENABLED", "true")
    get_settings.cache_clear()

    async def go():
        db, services, tg, graph = await system(pg, tmp_path)
        try:
            week = week42()
            for p in week.posts[:2]:
                await start(graph, services, week, p, "send")
            lv = bridge.Live(settings=get_settings(), graph=graph, services=services)
            a = Assistant(lv, model=Script(replies=[call("propose_approve", 1, campaign_ids=["all"]), say("Cardul e aici.")],
                                           seen=[]),
                          fallback=Script(replies=[], seen=[]), writer=Writer())
            ctx = await a.handle(CHAT, 1, "aprob tot", update_id=1)
            card = next(c[1] for c in reversed(tg.sent("sendMessage")) if "inline_keyboard" in (c[1].get("reply_markup") or {}))
            every = card["reply_markup"]["inline_keyboard"][2][0]["callback_data"]
            from content_system.assistant.proposals import handle_card

            await handle_card(lv, {"id": "cb", "data": every, "message": {"message_id": 1, "chat": {"id": CHAT}}}, 2)
            memory = await graph.checkpointer.aget_tuple({"configurable": {"thread_id": f"chat:{CHAT}"}})
            return ctx, memory, await services.store.usage_today()
        finally:
            await db.close()

    try:
        ctx, memory, spent = run(go())
    finally:
        get_settings.cache_clear()
        router.use(None)
    assert ctx.done == ["propose:approve:2026-W42-V4-001,2026-W42-V4-002"]
    assert [m.type for m in memory.checkpoint["channel_values"]["messages"]] == ["human", "ai", "tool", "ai"]
    assert admin_rows(pg, "select campaign_id, status from public.campaigns order by 1") == \
        [("2026-W42-V4-001", "approved"), ("2026-W42-V4-002", "approved")]
    assert admin_rows(pg, "select status from content.proposals order by status") == \
        [("dismissed",), ("dismissed",), ("used",)]
    assert spent > 0


def _plan_posts():
    from tests.test_planning import a_plan

    return a_plan()


def test_the_plan_on_postgres(pg, tmp_path):
    """migrations/013: drop/keep while sent, one decision, older plans replaced, no DELETE for content_app."""
    plan = _plan_posts()

    async def go():
        db, *_ = await system(pg, tmp_path)
        try:
            assert (await db.verify(planning=True))[0] == []
            store = PgStore(db.pool)
            await store.add_plan("a1b2c3d4e5f6", plan.week, plan.posts, plan.sources, plan.manifest, plan.idea_ids)
            before = await store.set_dropped("a1b2c3d4e5f6", 2, True)          # not sent yet
            await store.plan_sent("a1b2c3d4e5f6", CHAT)
            steps = [await store.set_dropped("a1b2c3d4e5f6", 5, True), await store.set_dropped("a1b2c3d4e5f6", 2, True),
                     await store.set_dropped("a1b2c3d4e5f6", 2, True), await store.set_dropped("a1b2c3d4e5f6", 5, False),
                     await store.set_dropped("a1b2c3d4e5f6", 9, True)]
            first, second = await asyncio.gather(store.decide_plan("a1b2c3d4e5f6", "approved"),
                                                 store.decide_plan("a1b2c3d4e5f6", "rejected"))
            after = await store.set_dropped("a1b2c3d4e5f6", 1, True)
            await store.add_plan("bbbbbbbbbbbb", plan.week, plan.posts, plan.sources, plan.manifest, [])
            await store.add_plan("cccccccccccc", plan.week, plan.posts, plan.sources, plan.manifest, [])
            await store.plan_sent("bbbbbbbbbbbb", CHAT)
            replaced = await store.replace_open_plans(plan.week, "cccccccccccc")
            latest = await store.latest_plan(plan.week)
            approved = await store.latest_plan(plan.week, "approved")
            listed = await store.week_plans(plan.week)
            return before, steps, first, second, after, replaced, latest, approved, listed
        finally:
            await db.close()

    before, steps, first, second, after, replaced, latest, approved, listed = run(go())
    assert before is None
    assert steps == [[5], [2, 5], [2, 5], [2], None]
    assert [x is None for x in (first, second)].count(True) == 1          # one tap decides, the other gets nothing
    decided = first or second
    assert decided["posts"] == plan.posts and decided["dropped"] == [2] and decided["idea_ids"] == [7]
    assert after is None and replaced == ["bbbbbbbbbbbb"]
    assert latest["id"] == "cccccccccccc"
    assert (approved or {}).get("id") == ("a1b2c3d4e5f6" if decided["status"] == "approved" else None)
    assert [(r["id"], r["status"], r["n"]) for r in listed] == [
        ("a1b2c3d4e5f6", decided["status"], 5), ("bbbbbbbbbbbb", "replaced", 5), ("cccccccccccc", "draft", 5)]
    with psycopg.connect(pg["app"], autocommit=True) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from content.plans")
    with psycopg.connect(pg["admin"], autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into content.plans (id, week, posts, manifest) values ('x', '2026-47', '[]', '')")


def test_verify_asks_for_migration_013_when_planning(pg, tmp_path):
    async def go():
        db, *_ = await system(pg, tmp_path)
        try:
            return await db.verify(planning=True), await db.verify()
        finally:
            await db.close()

    with psycopg.connect(pg["admin"], autocommit=True) as conn:
        conn.execute("drop table content.plans")
    planning, plain = run(go())
    assert planning[0] == ["content.plans missing (migrations/013)"] and plain[0] == []
