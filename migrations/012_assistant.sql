-- 012 — Betty's assistant (content_system/assistant): proposals, ideas, AI usage.
--
-- Supabase SQL Editor, project bbc-marketing-agent (uzfusyjboumexivywvpy) — NOT bbc-chatbot.
-- Run after 011. Idempotent: running it twice changes nothing. Nothing reads these tables until
-- CONTENT_ASSISTANT_ENABLED=true.

begin;

-- "✅ Aprob …" cards: the exact versions shown on the card. A tap decides each post on its own
-- version; a post that changed in the meantime is refused, with the reason. One card = one group;
-- a tap on any of its buttons closes the group.
create table if not exists content.proposals (
    id         text primary key,                      -- 12 hex characters, in the button's callback_data
    group_id   text not null,
    chat_id    bigint not null,
    action     text not null check (action in ('approve', 'reject')),
    items      jsonb not null,                        -- [{"campaign_id": "2026-W45-V4-001", "version": 2}, …]
    status     text not null default 'open' check (status in ('open', 'used', 'dismissed')),
    result     jsonb,
    created_at timestamptz not null default now(),
    used_at    timestamptz
);
create index if not exists proposals_group_idx on content.proposals (group_id);

-- Betty's ideas for the weekly plan ("💡 Idee"), read by the planning step later.
create table if not exists content.ideas (
    id         bigserial primary key,
    chat_id    bigint not null,
    user_id    bigint,
    text       text not null check (length(text) between 1 and 4000),
    status     text not null default 'new' check (status in ('new', 'planned', 'dropped')),
    created_at timestamptz not null default now()
);

-- Model calls per day and model, for the daily cost limit (CONTENT_ASSISTANT_DAILY_USD).
create table if not exists content.usage (
    day           date not null,
    model         text not null,
    calls         integer not null default 0,
    input_tokens  bigint not null default 0,
    output_tokens bigint not null default 0,
    cost_usd      numeric(12, 6) not null default 0,
    primary key (day, model)
);

revoke all on content.proposals, content.ideas, content.usage from anon, authenticated;
grant select, insert, update on content.proposals, content.ideas, content.usage to content_app;
grant usage on sequence content.ideas_id_seq to content_app;

commit;
