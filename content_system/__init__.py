"""
Content system — the weekly posts as a LangGraph workflow with durable human review.

One week = one TOML manifest (weeks/2026-W43.toml). Each post runs as its own LangGraph thread
("post:<campaign_id>"), checkpointed in Postgres (schema `content`):

    render ──► upload ──► publish ──► review ⏸ ──► approved | rejected
                             ▲           │
                             └─ edited ◄─┘   (new caption → version + 1 → new review message)

`review` pauses the thread with interrupt(); Betty's tap on Telegram resumes it, even days later
and in another process (the webhook). Each external effect (storage upload, Telegram message) is
keyed in content.effects, so retries and re-runs don't repeat it (the one narrow exception is
described in docs/CONTENT_SYSTEM.md).

Commands:  python -m content_system check|render|send|status|decide|db-setup   (see docs/CONTENT_SYSTEM.md)
"""
