"""
Everything the graph writes outside its own checkpoints, behind one interface:

  once(key, fn)     run an external effect (upload, Telegram message) at most once per key;
                    an attempt that certainly did not happen (forget_on) leaves no trace
  claim / release   drop Telegram updates delivered twice
  lease(thread)     one decision at a time per post, across processes; expires on its own if a
                    process dies holding it (no session state, so it works through any pooler)
  campaigns         public.campaigns — the row the bot, /deals and the publishing step read
  messages          which Telegram message belongs to which post and version
  proposals         the assistant's "✅ Aprob …" cards, with the exact versions shown (migrations/012)
  ideas, usage      Betty's ideas for the plan; model calls per day, for the daily cost limit

PgStore is production (schema `content` + public.campaigns). MemoryStore mirrors it for
`render` and the tests; tests/test_content_system_pg.py runs the same contract on both.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

log = logging.getLogger("bbc.content.store")

DECIDABLE = ("draft", "editing")        # statuses a content-system post can still change from
STALE_EFFECT_S = 120.0                  # a 'running' effect older than this was abandoned by a crash
LEASE_TTL_S = 600.0                     # a lease left by a dead process frees itself after this
PROPOSAL_TTL_S = 48 * 3600.0            # an "✅ Aprob …" card older than this no longer decides anything


class EffectBusy(RuntimeError):
    """The same effect is running right now somewhere else (retryable)."""


class LeaseBusy(RuntimeError):
    """Another decision on the same post is still running."""


CAMPAIGN_COLUMNS = (
    "campaign_id", "event_name", "city", "dates_start", "dates_end", "category", "route",
    "price", "price_raw", "image_url", "caption", "event_context",
    "scheduled_day", "design_style", "design_tone",
)


class MemoryStore:
    def __init__(self) -> None:
        self.effects: dict[str, dict[str, Any]] = {}
        self.campaigns: dict[str, dict[str, Any]] = {}
        self.messages: dict[tuple[int, int], dict[str, Any]] = {}
        self.leases: dict[str, tuple[str, float]] = {}
        self.threads: set[str] = set()          # stands in for the checkpoints table
        self.proposals: dict[str, dict[str, Any]] = {}
        self.ideas: list[dict[str, Any]] = []
        self.usage: dict[tuple[date, str], dict[str, Any]] = {}

    async def once(self, key: str, fn: Callable[[], Awaitable[Any]], *,
                   forget_on: Callable[[Exception], bool] | None = None) -> Any:
        e = self.effects.get(key)
        if e and e["status"] == "done":
            return e["external_id"]
        if e and e["status"] == "running" and time.monotonic() - e["t"] < STALE_EFFECT_S:
            raise EffectBusy(key)
        attempts = e["attempts"] + 1 if e else 1
        self.effects[key] = {"status": "running", "external_id": None, "attempts": attempts, "t": time.monotonic()}
        try:
            result = await fn()
        except BaseException as exc:
            if forget_on is not None and isinstance(exc, Exception) and forget_on(exc):
                self.effects.pop(key, None)      # it certainly did not happen: as if never tried
            else:
                self.effects[key] |= {"status": "failed", "error": str(exc)[:500]}
            raise
        self.effects[key] |= {"status": "done", "external_id": None if result is None else str(result)}
        return self.effects[key]["external_id"]

    async def claim(self, key: str) -> bool:
        if key in self.effects:
            return False
        self.effects[key] = {"status": "done", "external_id": None, "attempts": 1, "t": time.monotonic()}
        return True

    async def release(self, key: str) -> None:
        self.effects.pop(key, None)

    @asynccontextmanager
    async def lease(self, thread_id: str, wait: float = 15.0) -> AsyncIterator[None]:
        holder, deadline = uuid.uuid4().hex, time.monotonic() + wait
        while True:
            cur = self.leases.get(thread_id)
            if cur is None or cur[1] < time.monotonic():
                self.leases[thread_id] = (holder, time.monotonic() + LEASE_TTL_S)
                break
            if time.monotonic() > deadline:
                raise LeaseBusy(thread_id)
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            if self.leases.get(thread_id, (None,))[0] == holder:
                del self.leases[thread_id]

    async def upsert_campaign(self, row: dict[str, Any]) -> bool:
        cur = self.campaigns.get(row["campaign_id"])
        if cur and cur["status"] not in DECIDABLE:
            return False
        self.campaigns[row["campaign_id"]] = {**(cur or {"status": "draft"}), **row}
        return True

    async def set_review(self, campaign_id: str, chat_id: int, message_id: int) -> None:
        cur = self.campaigns.get(campaign_id)
        if cur and cur["status"] in DECIDABLE:
            cur |= {"review_chat_id": chat_id, "review_message_id": message_id}

    async def decide(self, campaign_id: str, status: str, caption: str) -> bool:
        cur = self.campaigns.get(campaign_id)
        if not cur or cur["status"] not in DECIDABLE:
            return False
        cur |= {"status": status, "caption": caption}
        return True

    async def campaign_status(self, campaign_id: str) -> str | None:
        cur = self.campaigns.get(campaign_id)
        return cur["status"] if cur else None

    async def add_message(self, chat_id: int, message_id: int, campaign_id: str, version: int, kind: str) -> None:
        self.messages.setdefault((int(chat_id), int(message_id)),
                                 {"campaign_id": campaign_id, "version": version, "kind": kind})

    async def find_message(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        return self.messages.get((int(chat_id), int(message_id)))

    async def foreign_campaigns(self, campaign_ids: list[str]) -> list[str]:
        return [c for c in campaign_ids if c in self.campaigns and f"post:{c}" not in self.threads]

    async def reached_telegram(self, campaign_id: str) -> bool:
        return any(k.startswith(f"review:{campaign_id}:") for k in self.effects)

    async def reviewed_posts(self, limit: int = 60) -> list[str]:
        ids = {m["campaign_id"] for m in self.messages.values() if m["kind"] == "review"}
        return sorted(ids, reverse=True)[:limit]

    # ── the assistant (migrations/012) ──
    async def add_proposal(self, proposal_id: str, group_id: str, chat_id: int, action: str,
                           items: list[dict[str, Any]]) -> None:
        self.proposals[proposal_id] = {"group_id": group_id, "chat_id": int(chat_id), "action": action,
                                       "items": [dict(i) for i in items], "status": "open", "result": None,
                                       "t": time.time()}

    async def take_proposal(self, proposal_id: str, max_age_s: float = PROPOSAL_TTL_S) -> tuple[str, dict | None]:
        """("taken", row) once — the caller then decides its items; else ("used" | "dismissed" |
        "expired" | "missing", None)."""
        p = self.proposals.get(proposal_id)
        if p is None:
            return "missing", None
        if p["status"] != "open":
            return p["status"], None
        if time.time() - p["t"] > max_age_s:
            return "expired", None
        p["status"] = "used"
        for other in self.proposals.values():
            if other["group_id"] == p["group_id"] and other["status"] == "open":
                other["status"] = "dismissed"
        return "taken", {k: v for k, v in p.items() if k != "t"}

    async def dismiss_group(self, group_id: str) -> int:
        n = 0
        for p in self.proposals.values():
            if p["group_id"] == group_id and p["status"] == "open":
                p["status"], n = "dismissed", n + 1
        return n

    async def set_proposal_result(self, proposal_id: str, result: list[dict[str, Any]]) -> None:
        self.proposals[proposal_id]["result"] = result

    async def add_idea(self, chat_id: int, user_id: int | None, text: str) -> int:
        self.ideas.append({"chat_id": chat_id, "user_id": user_id, "text": text, "status": "new"})
        return len(self.ideas)

    async def add_usage(self, model: str, calls: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        key = (datetime.now(UTC).date(), model)
        row = self.usage.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        row["calls"] += calls
        row["input_tokens"] += input_tokens
        row["output_tokens"] += output_tokens
        row["cost_usd"] += cost_usd

    async def usage_today(self) -> float:
        today = datetime.now(UTC).date()
        return sum(r["cost_usd"] for (d, _), r in self.usage.items() if d == today)


class PgStore:
    """Postgres implementation. Connections come from content_system.db.Database (search_path content, public)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def _one(self, query: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchone() if cur.description else None

    async def _all(self, query: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    # ── effects ──
    async def once(self, key: str, fn: Callable[[], Awaitable[Any]], *,
                   forget_on: Callable[[Exception], bool] | None = None) -> Any:
        fresh = await self._one(
            "insert into effects (key, status) values (%s, 'running') on conflict (key) do nothing returning key",
            (key,))
        if fresh is None:
            row = await self._one("select status, external_id from effects where key = %s", (key,))
            if row and row["status"] == "done":
                return row["external_id"]
            taken = await self._one(
                "update effects set status = 'running', attempts = attempts + 1, error = null, updated_at = now() "
                "where key = %s and (status = 'failed' or "
                "(status = 'running' and updated_at < now() - make_interval(secs => %s))) returning key",
                (key, STALE_EFFECT_S))
            if taken is None:
                raise EffectBusy(key)
        try:
            result = await fn()
        except BaseException as exc:
            try:
                if forget_on is not None and isinstance(exc, Exception) and forget_on(exc):
                    await self._one("delete from effects where key = %s", (key,))   # certainly did not happen
                else:
                    await self._one(
                        "update effects set status = 'failed', error = %s, updated_at = now() where key = %s",
                        (str(exc)[:500], key))
            except Exception:
                log.exception("could not mark effect %s failed", key)
            raise
        external_id = None if result is None else str(result)
        await self._one(
            "update effects set status = 'done', external_id = %s, error = null, updated_at = now() where key = %s",
            (external_id, key))
        return external_id

    async def claim(self, key: str) -> bool:
        row = await self._one(
            "insert into effects (key, status) values (%s, 'done') on conflict (key) do nothing returning key", (key,))
        return row is not None

    async def release(self, key: str) -> None:
        await self._one("delete from effects where key = %s", (key,))

    # ── lease ──
    @asynccontextmanager
    async def lease(self, thread_id: str, wait: float = 15.0) -> AsyncIterator[None]:
        holder, deadline = uuid.uuid4(), time.monotonic() + wait
        while True:
            row = await self._one(
                "insert into leases (thread_id, holder, expires_at) "
                "values (%s, %s, now() + make_interval(secs => %s)) "
                "on conflict (thread_id) do update set holder = excluded.holder, expires_at = excluded.expires_at "
                "where leases.expires_at < now() returning holder",
                (thread_id, holder, LEASE_TTL_S))
            if row is not None and row["holder"] == holder:
                break
            if time.monotonic() > deadline:
                raise LeaseBusy(thread_id)
            await asyncio.sleep(0.2)
        try:
            yield
        finally:
            await self._one("delete from leases where thread_id = %s and holder = %s", (thread_id, holder))

    # ── public.campaigns ──
    async def upsert_campaign(self, row: dict[str, Any]) -> bool:
        params = {c: row.get(c) for c in CAMPAIGN_COLUMNS}
        for c in ("dates_start", "dates_end"):
            if isinstance(params[c], str):
                params[c] = date.fromisoformat(params[c])
        cols = ", ".join(CAMPAIGN_COLUMNS)
        values = ", ".join(f"%({c})s" for c in CAMPAIGN_COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in CAMPAIGN_COLUMNS if c != "campaign_id")
        done = await self._one(
            f"insert into public.campaigns as c ({cols}, status) values ({values}, 'draft') "
            f"on conflict (campaign_id) do update set {updates} "
            f"where c.status in ('draft', 'editing') returning c.status",
            params)
        return done is not None

    async def set_review(self, campaign_id: str, chat_id: int, message_id: int) -> None:
        await self._one(
            "update public.campaigns set review_chat_id = %s, review_message_id = %s "
            "where campaign_id = %s and status in ('draft', 'editing')",
            (chat_id, message_id, campaign_id))

    async def decide(self, campaign_id: str, status: str, caption: str) -> bool:
        """Close the post with the caption of the version that was decided — what Betty saw is what is stored."""
        row = await self._one(
            "update public.campaigns set status = %s, caption = %s, "
            "approved_at = case when %s = 'approved' then now() else approved_at end "
            "where campaign_id = %s and status in ('draft', 'editing') returning status",
            (status, caption, status, campaign_id))
        return row is not None

    async def campaign_status(self, campaign_id: str) -> str | None:
        row = await self._one("select status from public.campaigns where campaign_id = %s", (campaign_id,))
        return row["status"] if row else None

    # ── messages ──
    async def add_message(self, chat_id: int, message_id: int, campaign_id: str, version: int, kind: str) -> None:
        await self._one(
            "insert into messages (chat_id, message_id, campaign_id, version, kind) values (%s, %s, %s, %s, %s) "
            "on conflict (chat_id, message_id) do nothing",
            (chat_id, message_id, campaign_id, version, kind))

    async def find_message(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        return await self._one(
            "select campaign_id, version, kind from messages where chat_id = %s and message_id = %s",
            (chat_id, message_id))

    async def reached_telegram(self, campaign_id: str) -> bool:
        """True once a review message was sent — or attempted — for this post."""
        row = await self._one("select exists (select 1 from effects where key like %s) as sent",
                              (f"review:{campaign_id}:%",))
        return bool(row and row["sent"])

    async def foreign_campaigns(self, campaign_ids: list[str]) -> list[str]:
        """Ids that already exist in public.campaigns but were not created by the content system."""
        rows = await self._all(
            "select c.campaign_id from public.campaigns c where c.campaign_id = any(%s) "
            "and not exists (select 1 from checkpoints k where k.thread_id = 'post:' || c.campaign_id) "
            "order by c.campaign_id",
            (campaign_ids,))
        return [r["campaign_id"] for r in rows]

    async def reviewed_posts(self, limit: int = 60) -> list[str]:
        """The posts that went to Telegram for review, newest week first."""
        rows = await self._all(
            "select campaign_id from messages where kind = 'review' group by campaign_id "
            "order by campaign_id desc limit %s", (limit,))
        return [r["campaign_id"] for r in rows]

    # ── the assistant (migrations/012) ──
    async def add_proposal(self, proposal_id: str, group_id: str, chat_id: int, action: str,
                           items: list[dict[str, Any]]) -> None:
        await self._one(
            "insert into proposals (id, group_id, chat_id, action, items) values (%s, %s, %s, %s, %s::jsonb)",
            (proposal_id, group_id, chat_id, action, json.dumps(items)))

    async def take_proposal(self, proposal_id: str, max_age_s: float = PROPOSAL_TTL_S) -> tuple[str, dict | None]:
        """Atomically ("taken", row) for the first tap; the rest of its group closes with it."""
        row = await self._one(
            "update proposals set status = 'used', used_at = now() "
            "where id = %s and status = 'open' and created_at > now() - make_interval(secs => %s) "
            "returning group_id, chat_id, action, items",
            (proposal_id, max_age_s))
        if row is not None:
            await self._one("update proposals set status = 'dismissed' where group_id = %s and status = 'open'",
                            (row["group_id"],))
            return "taken", {**row, "items": list(row["items"]), "status": "used"}
        cur = await self._one("select status from proposals where id = %s", (proposal_id,))
        if cur is None:
            return "missing", None
        return ("expired" if cur["status"] == "open" else cur["status"]), None

    async def dismiss_group(self, group_id: str) -> int:
        rows = await self._all(
            "update proposals set status = 'dismissed' where group_id = %s and status = 'open' returning id",
            (group_id,))
        return len(rows)

    async def set_proposal_result(self, proposal_id: str, result: list[dict[str, Any]]) -> None:
        await self._one("update proposals set result = %s::jsonb where id = %s", (json.dumps(result), proposal_id))

    async def add_idea(self, chat_id: int, user_id: int | None, text: str) -> int:
        row = await self._one("insert into ideas (chat_id, user_id, text) values (%s, %s, %s) returning id",
                              (chat_id, user_id, text))
        return int(row["id"])

    async def add_usage(self, model: str, calls: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        await self._one(
            "insert into usage (day, model, calls, input_tokens, output_tokens, cost_usd) "
            "values ((now() at time zone 'utc')::date, %s, %s, %s, %s, %s) "
            "on conflict (day, model) do update set calls = usage.calls + excluded.calls, "
            "input_tokens = usage.input_tokens + excluded.input_tokens, "
            "output_tokens = usage.output_tokens + excluded.output_tokens, "
            "cost_usd = usage.cost_usd + excluded.cost_usd",
            (model, calls, input_tokens, output_tokens, cost_usd))

    async def usage_today(self) -> float:
        row = await self._one(
            "select coalesce(sum(cost_usd), 0)::float8 as usd from usage where day = (now() at time zone 'utc')::date")
        return float(row["usd"]) if row else 0.0
