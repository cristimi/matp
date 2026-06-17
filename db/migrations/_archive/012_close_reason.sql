-- Migration 012: add close_reason to strategy_positions
-- Values: 'Liquidated', 'Closed on exchange', NULL (strategy/manual close)
ALTER TABLE strategy_positions ADD COLUMN IF NOT EXISTS close_reason VARCHAR(30);
