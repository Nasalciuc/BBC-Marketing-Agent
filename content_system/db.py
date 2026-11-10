"""
Postgres for the content system: one async pool (search_path = content, public), the LangGraph
checkpointer on top of it, and the checks `db-setup` runs.

Connection: the Supabase *Session pooler* string (port 5432, IPv4 — Railway has no outbound IPv6
for the direct host) with the role content_app from migrations/011, not postgres.
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from content_system.settings import db_url_problem

CONTENT_TABLES = ("effects", "messages", "leases")
CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")
CAMPAIGN_COLUMNS_NEEDED = (
    "campaign_id", "event_name", "city", "dates_start", "dates_end", "category", "route", "price",
    "price_raw", "image_url", "caption", "event_context", "status", "approved_at",
    "review_chat_id", "review_message_id", "scheduled_day", "design_style", "design_tone",
)


class Database:
    def __init__(self, url: str, *, schema: str = "content", max_size: int = 4, min_size: int = 1):
        problem = db_url_problem(url)
        if problem:
            raise ValueError(problem)
        self.schema = schema
        self.pool = AsyncConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            name="content",
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                    "application_name": "bbc-content-system"},
            configure=self._configure,
            check=AsyncConnectionPool.check_connection,
            max_idle=300,
        )

    async def _configure(self, conn: Any) -> None:
        await conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(self.schema)))

    async def open(self, wait_s: float = 20) -> None:
        await self.pool.open(wait=True, timeout=wait_s)

    async def close(self) -> None:
        await self.pool.close()

    def checkpointer(self) -> AsyncPostgresSaver:
        return AsyncPostgresSaver(self.pool)

    async def setup(self) -> None:
        """Create or migrate LangGraph's checkpoint tables (idempotent). Needs CREATE on schema content."""
        await self.checkpointer().setup()

    async def verify(self) -> tuple[list[str], list[str]]:
        """(problems, notes) — problems block `send`; notes are worth reading once."""
        problems: list[str] = []
        notes: list[str] = []
        async with self.pool.connection() as conn:
            who = await (await conn.execute("select current_user as u, current_schema() as s")).fetchone()
            notes.append(f"connected as {who['u']}, schema {who['s']}")
            if who["u"] == "postgres":
                notes.append("running as postgres: use the content_app role from migrations/011 instead")
            if who["s"] != self.schema:
                problems.append(f"schema {self.schema} does not exist (apply migrations/011_content_system.sql)")
                return problems, notes
            for t in CONTENT_TABLES + CHECKPOINT_TABLES:
                row = await (await conn.execute(
                    "select to_regclass(%s) is not null as ok", (f"{self.schema}.{t}",))).fetchone()
                if not row["ok"]:
                    hint = "python -m content_system db-setup" if t in CHECKPOINT_TABLES else "migrations/011"
                    problems.append(f"{self.schema}.{t} missing ({hint})")
            rows = await (await conn.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = 'campaigns'")).fetchall()
            have = {r["column_name"] for r in rows}
            missing = [c for c in CAMPAIGN_COLUMNS_NEEDED if c not in have]
            if missing:
                problems.append("public.campaigns is missing " + ", ".join(missing)
                                + " (apply migrations 005, 008 and 009; or no SELECT grant — migrations/011)")
            priv = await (await conn.execute(
                "select has_table_privilege('public.campaigns', 'SELECT') as s, "
                "has_table_privilege('public.campaigns', 'INSERT') as i, "
                "has_table_privilege('public.campaigns', 'UPDATE') as u")).fetchone()
            if not all(priv.values()):
                problems.append("no SELECT/INSERT/UPDATE on public.campaigns (migrations/011 grants)")
            rls = await (await conn.execute(
                "select c.relrowsecurity as rls_on, exists (select 1 from pg_policies p "
                "  where p.schemaname = 'public' and p.tablename = 'campaigns' "
                "  and (current_user = any(p.roles) or 'public' = any(p.roles))) as policy "
                "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relname = 'campaigns'")).fetchone()
            if rls and rls["rls_on"] and not rls["policy"] and who["u"] != "postgres":
                problems.append("RLS is on for public.campaigns but no policy lets this role in (migrations/011)")
        return problems, notes
