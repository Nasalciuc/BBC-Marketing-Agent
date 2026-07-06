"""
BBC Reasoning Agent — ReAct loop pentru intent ANALYZE.
Claude primește 5 tool-uri READ-ONLY și răspunde la întrebări despre
campanii, broadcast-uri, joburi, contacte și context web.

Read-only prin design: niciun tool nu scrie în Supabase.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

log = logging.getLogger("bbc.reasoning")

MAX_ITERATIONS = 10
TIMEOUT_SECONDS = 120

AGENT_TOOLS: list[dict] = [
    {
        "name": "query_campaigns",
        "description": (
            "Query the campaigns table (read-only). Returns campaigns with "
            "campaign_id, event_name, city, category, status, price, created dates. "
            "Filter by status (draft/approved/rejected/sent) and/or category. "
            "Use for questions like 'câte campanii avem', 'care postări au fost respinse'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter: draft, approved, rejected, sent",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter (event, news, destination, cabin...)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 20, max 50)",
                },
            },
        },
    },
    {
        "name": "query_broadcast_stats",
        "description": (
            "Aggregate broadcast_log stats (read-only): total sent/failed per "
            "campaign and per channel. Use for performance questions like "
            "'de ce postările cu X merg prost', 'compare this month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {
                    "type": "string",
                    "description": "Optional: stats for a single campaign",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max log rows scanned (default 200)",
                },
            },
        },
    },
    {
        "name": "query_job_history",
        "description": (
            "Query job_runs table (read-only): pipeline job history with "
            "job_name, week_label, status, events_count, errors. Use for "
            "'a rulat pipeline-ul săptămâna asta?', 'ce joburi au eșuat'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_name": {"type": "string", "description": "Optional job name filter"},
                "limit": {"type": "integer", "description": "Max rows (default 10)"},
            },
        },
    },
    {
        "name": "get_contact_stats",
        "description": (
            "Contact list stats (read-only): total active contacts and breakdown "
            "by country. Use for 'câți subscriberi avem', 'din ce țări'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_web",
        "description": (
            "Search the live web via Gemini + Google Search. Use for external "
            "context: competitor moves, industry trends, event dates, anything "
            "not in our database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The web search query"},
            },
            "required": ["query"],
        },
    },
]


def _sb():
    from services.supabase_client import _get_client

    return _get_client()


async def _tool_query_campaigns(args: dict) -> str:
    client = _sb()
    if not client:
        return json.dumps({"error": "Supabase not configured"})
    limit = min(int(args.get("limit") or 20), 50)
    try:
        q = client.table("campaigns").select(
            "campaign_id,event_name,city,category,status,price,approved_at,sent_at"
        )
        if args.get("status"):
            q = q.eq("status", args["status"])
        if args.get("category"):
            q = q.eq("category", args["category"])
        rows = q.limit(limit).execute().data or []
        return json.dumps({"count": len(rows), "campaigns": rows}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _tool_query_broadcast_stats(args: dict) -> str:
    client = _sb()
    if not client:
        return json.dumps({"error": "Supabase not configured"})
    limit = min(int(args.get("limit") or 200), 1000)
    try:
        q = client.table("broadcast_log").select("campaign_id,status,channel")
        if args.get("campaign_id"):
            q = q.eq("campaign_id", args["campaign_id"])
        rows = q.limit(limit).execute().data or []

        per_campaign: dict[str, dict] = {}
        per_channel: dict[str, dict] = {}
        for r in rows:
            cid = r.get("campaign_id") or "?"
            ch = r.get("channel") or "?"
            ok = r.get("status") == "sent"
            pc = per_campaign.setdefault(cid, {"sent": 0, "failed": 0})
            pch = per_channel.setdefault(ch, {"sent": 0, "failed": 0})
            key = "sent" if ok else "failed"
            pc[key] += 1
            pch[key] += 1

        return json.dumps(
            {
                "rows_scanned": len(rows),
                "per_campaign": per_campaign,
                "per_channel": per_channel,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _tool_query_job_history(args: dict) -> str:
    client = _sb()
    if not client:
        return json.dumps({"error": "Supabase not configured"})
    limit = min(int(args.get("limit") or 10), 50)
    try:
        q = client.table("job_runs").select(
            "job_name,week_label,status,events_count,error_message,completed_at"
        )
        if args.get("job_name"):
            q = q.eq("job_name", args["job_name"])
        rows = q.order("completed_at", desc=True).limit(limit).execute().data or []
        return json.dumps({"count": len(rows), "jobs": rows}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _tool_get_contact_stats(args: dict) -> str:
    client = _sb()
    if not client:
        return json.dumps({"error": "Supabase not configured"})
    try:
        rows = (
            client.table("contacts")
            .select("country,status")
            .eq("status", "active")
            .limit(2000)
            .execute()
            .data
            or []
        )
        by_country: dict[str, int] = {}
        for r in rows:
            c = r.get("country") or "unknown"
            by_country[c] = by_country.get(c, 0) + 1
        return json.dumps(
            {"active_total": len(rows), "by_country": by_country}, ensure_ascii=False
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _tool_search_web(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "empty query"})
    try:
        from google import genai
        from google.genai import types

        from config import settings

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=settings.gemini_model,
            contents=f"Search the web and answer concisely (max 200 words): {query}",
            config=types.GenerateContentConfig(
                temperature=0.2,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        return json.dumps({"query": query, "answer": resp.text or ""}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


_TOOL_IMPL = {
    "query_campaigns": _tool_query_campaigns,
    "query_broadcast_stats": _tool_query_broadcast_stats,
    "query_job_history": _tool_query_job_history,
    "get_contact_stats": _tool_get_contact_stats,
    "search_web": _tool_search_web,
}


async def execute_tool(name: str, args: dict) -> str:
    """Execute one read-only tool by name. Returns JSON string."""
    impl = _TOOL_IMPL.get(name)
    if not impl:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        return await impl(args or {})
    except Exception as e:
        log.error("Tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})


def _build_system_prompt() -> str:
    from prompts.brand_dna import BBC_BRAND_DNA

    today = datetime.now(UTC).strftime("%B %d, %Y")
    return (
        BBC_BRAND_DNA
        + f"""

You are the BBC ANALYST — a reasoning agent for the marketing director.
Today is {today}. You answer questions about OUR marketing system using
the read-only tools provided (campaigns, broadcasts, jobs, contacts, web).

HOW TO WORK:
1. Break the question into data you need.
2. Call tools to gather REAL data — never invent numbers.
3. Reason over results; call more tools if needed (max {MAX_ITERATIONS} steps).
4. Finish with a SHORT Telegram-friendly answer (Markdown, max ~12 lines):
   lead with the answer, then 2-4 supporting facts, then one recommendation.

RULES:
- Data questions → tools first, ALWAYS.
- If a tool errors or Supabase is empty, say so plainly.
- No hedging, no filler. Numbers over adjectives.
- Romanian question → answer in Romanian. English → English."""
    )


async def run_reasoning_loop(question: str, chat_id: int | str) -> None:
    """ReAct loop: Claude + tools, progres live pe Telegram, răspuns final."""
    from anthropic import Anthropic

    from config import settings
    from services.telegram_client import send_message

    if not settings.anthropic_api_key:
        await send_message(chat_id=chat_id, text="⚠️ AI not configured.")
        return

    await send_message(chat_id=chat_id, text="🧠 *Analyzing...* querying live data")

    client = Anthropic(api_key=settings.anthropic_api_key)
    model = settings.anthropic_model or "claude-sonnet-4-6"
    messages: list[dict] = [{"role": "user", "content": question}]

    async def _loop() -> str:
        for iteration in range(1, MAX_ITERATIONS + 1):
            resp = await asyncio.to_thread(
                client.messages.create,
                model=model,
                max_tokens=1500,
                system=_build_system_prompt(),
                tools=AGENT_TOOLS,
                messages=messages,
            )

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            texts = [b.text for b in resp.content if b.type == "text"]

            if resp.stop_reason != "tool_use" or not tool_uses:
                return "\n".join(texts).strip() or "🤷 Nu am găsit un răspuns."

            messages.append({"role": "assistant", "content": resp.content})

            results = []
            for tu in tool_uses:
                log.info("Reasoning step %d: %s(%s)", iteration, tu.name, tu.input)
                await send_message(
                    chat_id=chat_id,
                    text=f"🔍 Step {iteration}: `{tu.name}`",
                )
                out = await execute_tool(tu.name, tu.input or {})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": out[:8000],
                    }
                )
            messages.append({"role": "user", "content": results})

        return "⚠️ Analiza a depășit limita de pași. Încearcă o întrebare mai specifică."

    try:
        answer = await asyncio.wait_for(_loop(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        answer = "⚠️ Analiza a durat prea mult (>120s). Încearcă o întrebare mai îngustă."
    except Exception as e:
        log.error("Reasoning loop failed: %s", e, exc_info=True)
        answer = f"⚠️ Analysis error: {e}"

    await send_message(chat_id=chat_id, text=answer)
