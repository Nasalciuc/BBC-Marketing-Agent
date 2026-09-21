-- Migration 008: scheduled_day on campaigns (W39 vertical week Mon→Sun)
-- Additive. Run in Supabase SQL Editor — project bbc-marketing-agent (uzfusyjboumexivywvpy)

alter table campaigns add column if not exists scheduled_day text;
