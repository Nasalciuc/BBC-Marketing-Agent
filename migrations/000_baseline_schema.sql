-- 000 — baseline: the five tables exactly as they exist in production (project bbc-marketing-agent),
-- read from information_schema, pg_constraint and pg_indexes on 2026-10-15.
-- Migrations 001–004 and 007 were applied by hand and never committed; this file stands in for them.
-- Idempotent: on the live database it creates nothing. On an empty database, run it first,
-- then 005, 006, 008, 009, 010 in order.

create table if not exists public.campaigns (
    id                uuid primary key default gen_random_uuid(),
    campaign_id       text not null unique,
    event_name        text not null,
    city              text,
    dates_start       date,
    dates_end         date,
    category          text,
    premium_score     real,
    route             text,
    price             text,
    price_raw         integer,
    image_url         text,
    story_url         text,
    caption           text,
    status            text default 'draft'
                      check (status = any (array['draft', 'approved', 'rejected', 'sent', 'failed'])),
    approved_at       timestamptz,
    sent_at           timestamptz,
    contacts_total    integer default 0,
    contacts_sent     integer default 0,
    contacts_failed   integer default 0,
    created_at        timestamptz default now(),
    review_chat_id    bigint,
    review_message_id bigint,
    caption_override  text,
    event_context     text,
    failed_stage      text,
    error_message     text,
    video_request_id  text,
    video_url         text,
    video_status      text,
    video_model       text,
    video_width       integer,
    video_height      integer
);
create index if not exists idx_campaigns_status on public.campaigns (status);

create table if not exists public.contacts (
    id             uuid primary key default gen_random_uuid(),
    phone_number   text not null unique,
    full_name      text,
    country        text,
    opt_in_date    date,
    opt_in_method  text,
    status         text default 'active'
                   check (status = any (array['active', 'opted_out', 'bounced'])),
    last_sent_date date,
    last_campaign  text,
    created_at     timestamptz default now(),
    updated_at     timestamptz default now()
);
create index if not exists idx_contacts_status on public.contacts (status);
alter table public.contacts enable row level security;
do $$
begin
    if not exists (select 1 from pg_policies
                   where schemaname = 'public' and tablename = 'contacts'
                     and policyname = 'service_role_only') then
        create policy service_role_only on public.contacts
            for all to service_role using (true) with check (true);
    end if;
end $$;

create table if not exists public.job_runs (
    id            uuid primary key default gen_random_uuid(),
    job_name      text not null,
    week_label    text not null,
    status        text default 'running'
                  check (status = any (array['running', 'completed', 'failed'])),
    events_count  integer default 0,
    details       jsonb default '{}'::jsonb,
    started_at    timestamptz default now(),
    completed_at  timestamptz,
    error_message text
);
create index if not exists idx_job_runs_lookup on public.job_runs (job_name, week_label);

create table if not exists public.broadcast_log (
    id            uuid primary key default gen_random_uuid(),
    campaign_id   text not null,
    phone_number  text not null,
    status        text default 'pending'
                  check (status = any (array['pending', 'sent', 'delivered', 'read', 'failed'])),
    channel       text default 'twilio'
                  check (channel = any (array['twilio', 'waha', 'manual'])),
    twilio_sid    text,
    sent_at       timestamptz default now(),
    delivered_at  timestamptz,
    read_at       timestamptz,
    error_message text
);
create index if not exists idx_broadcast_campaign on public.broadcast_log (campaign_id);

create table if not exists public.chat_context (
    chat_id             bigint primary key,
    current_campaign_id text,
    state               text default 'idle',
    history             jsonb default '[]'::jsonb,
    last_interaction    timestamptz default now(),
    updated_at          timestamptz default now()
);
create index if not exists idx_chat_context_last on public.chat_context (last_interaction);
