-- Migration 022: add reconcile divergence tracking columns to strategy_positions
--
-- When the reconciler observes exchange_size > db_size it cannot grow the DB row,
-- but it now marks the position as divergent so the dashboard can surface it.
--
-- reconcile_divergent     : TRUE when exchange size exceeded DB size on last pass
-- reconcile_exchange_size : the exchange size observed at that moment
-- reconcile_divergence_at : timestamp of first detection (preserved across passes)

ALTER TABLE strategy_positions
    ADD COLUMN IF NOT EXISTS reconcile_divergent     BOOLEAN   NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reconcile_exchange_size NUMERIC,
    ADD COLUMN IF NOT EXISTS reconcile_divergence_at TIMESTAMP WITH TIME ZONE;
