-- 013 — the week's plan (content_system/planning): what Betty approves before posts are made.
--
-- Supabase SQL Editor, project bbc-marketing-agent (uzfusyjboumexivywvpy) — NOT bbc-chatbot.
-- Run after 012. Idempotent. Nothing reads this table until a plan is sent (`plan --send`).

begin;

create table if not exists content.plans (
    id          text primary key,                      -- 12 hex characters, in the buttons' callback_data
    week        text not null check (week ~ '^\d{4}-W\d{2}$'),
    status      text not null default 'draft' check (status in ('draft', 'sent', 'approved', 'rejected', 'replaced')),
    posts       jsonb not null,                        -- the manifest [[post]] tables, in publishing order
    sources     jsonb not null default '[]',           -- per post, the URLs its facts come from
    manifest    text not null,                         -- the TOML that was checked
    dropped     integer[] not null default '{}',       -- posts Betty took out (1-based)
    idea_ids    bigint[] not null default '{}',        -- Betty's ideas the plan uses
    chat_id     bigint,
    created_at  timestamptz not null default now(),
    decided_at  timestamptz                            -- approved, rejected, or replaced by a newer plan
);
create index if not exists plans_week_idx on content.plans (week, created_at desc);

revoke all on content.plans from anon, authenticated;
grant select, insert, update on content.plans to content_app;

commit;
