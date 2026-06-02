-- Migration 006: Chat context for AI Agent conversations
-- BBC Marketing Agent
-- Run in Supabase SQL Editor BEFORE deploying AI agent

CREATE TABLE IF NOT EXISTS chat_context (
    chat_id BIGINT PRIMARY KEY,
    current_campaign_id TEXT,
    state TEXT DEFAULT 'idle',
    history JSONB DEFAULT '[]'::jsonb,
    last_interaction TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_context_last ON chat_context(last_interaction);

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'chat_context'
ORDER BY ordinal_position;
