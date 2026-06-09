-- Migration 009: Add AI reasoning and confidence columns to signal_log
-- These are populated when signal_source = 'ai_engine'
ALTER TABLE signal_log
  ADD COLUMN IF NOT EXISTS ai_reasoning  TEXT,
  ADD COLUMN IF NOT EXISTS ai_confidence NUMERIC(4,3);

COMMENT ON COLUMN signal_log.ai_reasoning IS
  'LLM reasoning text from AI signal generator. NULL for non-AI signals.';
COMMENT ON COLUMN signal_log.ai_confidence IS
  'LLM confidence score (0.0-0.95) from AI signal generator. NULL for non-AI signals.';
