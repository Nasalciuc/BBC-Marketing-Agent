-- 010 — two fixes found in the live database on 2026-10-15.
--
-- 1) Row Level Security was OFF on campaigns, job_runs, broadcast_log and chat_context.
--    Anyone holding the project's anon key could read and change them through the REST API:
--    statuses, captions, image URLs. The backend uses the service_role key, which bypasses
--    RLS, so the app keeps working exactly as before. Same policy as contacts already has.
--
-- 2) campaigns_status_check allowed only draft/approved/rejected/sent/failed, while the bot
--    writes 'editing' (Edit caption), 'cancelled' (Cancel) and 'urgent_preview' (urgent
--    deals). Those updates failed and the errors were swallowed: Edit caption never entered
--    edit mode, Cancel did nothing, urgent deals were never saved.

begin;

alter table public.campaigns     enable row level security;
alter table public.job_runs      enable row level security;
alter table public.broadcast_log enable row level security;
alter table public.chat_context  enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array['campaigns', 'job_runs', 'broadcast_log', 'chat_context'] loop
        if not exists (select 1 from pg_policies
                       where schemaname = 'public' and tablename = t
                         and policyname = 'service_role_only') then
            execute format(
                'create policy service_role_only on public.%I for all to service_role '
                'using (true) with check (true)', t);
        end if;
    end loop;
end $$;

alter table public.campaigns drop constraint if exists campaigns_status_check;
alter table public.campaigns add constraint campaigns_status_check check (status = any (array[
    'draft', 'editing', 'approved', 'rejected', 'urgent_preview', 'sent', 'failed', 'cancelled'
]));

commit;
