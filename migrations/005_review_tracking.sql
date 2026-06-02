-- Migration 005: Review tracking + caption override
-- Run in Supabase SQL Editor

-- Review tracking (pentru edit/remove keyboard după approve/reject)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS review_chat_id BIGINT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS review_message_id BIGINT;

-- Caption edit flow
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS caption_override TEXT;

-- Event context (pentru format_whatsapp_group)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS event_context TEXT;

-- WhatsApp caption from vision pipeline
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS whatsapp_caption TEXT;

-- Pipeline error tracking
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS failed_stage TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Index pentru queries rapide
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

-- Verificare
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'campaigns'
ORDER BY ordinal_position;
