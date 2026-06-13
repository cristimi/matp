-- ============================================================
-- Migration 013: add ai_config_defaulted flag to tester.strategies
-- Tracks whether a from-matp import had no AI config (defaults were applied).
-- The UI uses this to show the "AI config defaulted — review before backtesting" banner.
-- ============================================================

ALTER TABLE tester.strategies
    ADD COLUMN IF NOT EXISTS ai_config_defaulted BOOLEAN NOT NULL DEFAULT FALSE;
