-- 011 — the content system (content_system/, LangGraph): a schema and a database role of its own.
--
-- Supabase SQL Editor, project bbc-marketing-agent (uzfusyjboumexivywvpy) — NOT bbc-chatbot.
-- Run after 008, 009 and 010. Idempotent: running it twice changes nothing.
--
-- Why a separate role: the webhook needs a Postgres connection for the LangGraph checkpoints.
-- With the postgres password on Railway, one leak would open the whole database (auth, storage,
-- every schema). content_app can use schema `content` and read/write public.campaigns — nothing else.
--
-- After this file, the person (not Cursor) gives the role a password, in the SQL Editor:
--     alter role content_app with login password '<new password>';
-- and saves the Session pooler string with that role and password as the Railway variable
-- CONTENT_DB_URL. The LangGraph tables themselves are created by: python -m content_system db-setup

begin;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'content_app') then
        create role content_app nologin;
    end if;
end $$;

create schema if not exists content;
revoke all on schema content from public;
grant usage, create on schema content to content_app;

-- External effects (storage upload, Telegram message) done at most once per key.
create table if not exists content.effects (
    key         text primary key,
    status      text not null check (status in ('running', 'done', 'failed')),
    external_id text,
    attempts    integer not null default 1,
    error       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Every review / edit-prompt message sent on Telegram → its post and version.
create table if not exists content.messages (
    chat_id     bigint  not null,
    message_id  bigint  not null,
    campaign_id text    not null,
    version     integer not null,
    kind        text    not null check (kind in ('review', 'edit_prompt')),
    created_at  timestamptz not null default now(),
    primary key (chat_id, message_id)
);
create index if not exists messages_campaign_idx on content.messages (campaign_id);

-- One decision at a time per post; a lease expires by itself if its process dies.
create table if not exists content.leases (
    thread_id  text primary key,
    holder     uuid not null,
    expires_at timestamptz not null
);

revoke all on all tables in schema content from anon, authenticated;
grant select, insert, update, delete on content.effects, content.messages, content.leases to content_app;

-- public.campaigns: the row the bot, /deals and the publishing step read. The database itself
-- enforces the rule "never touch a campaign that is no longer a draft": content_app inserts only
-- drafts, and updates only rows that are still draft/editing (to approve, reject or edit them).
grant usage on schema public to content_app;
grant select, insert, update on public.campaigns to content_app;
do $$
begin
    if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'campaigns'
                     and policyname = 'content_app_select') then
        create policy content_app_select on public.campaigns
            for select to content_app using (true);
    end if;
    if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'campaigns'
                     and policyname = 'content_app_insert') then
        create policy content_app_insert on public.campaigns
            for insert to content_app with check (status = 'draft');
    end if;
    if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'campaigns'
                     and policyname = 'content_app_update') then
        create policy content_app_update on public.campaigns
            for update to content_app
            using (status in ('draft', 'editing'))
            with check (status in ('draft', 'editing', 'approved', 'rejected'));
    end if;
end $$;

commit;
