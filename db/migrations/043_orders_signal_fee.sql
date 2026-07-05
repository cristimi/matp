-- Migration 043: add signal_log_id and exchange_fee directly to public.orders.
-- Today, ai_reasoning/ai_confidence/exchange_fee only reach the dashboard via
-- orders -> order_execution_log -> signal_log, and order_execution_log rows are
-- written ONLY by the executor's open path — so every close/partial-close order
-- shows blank reasoning, confidence, and fee. Giving orders its own signal_log_id
-- FK and exchange_fee column lets every order (open, close, rejected) carry this
-- data directly, independent of the open-only OEL join.
-- Forward-only: nullable, no backfill of historical rows.

BEGIN;

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS signal_log_id bigint,
    ADD COLUMN IF NOT EXISTS exchange_fee  numeric;

CREATE INDEX IF NOT EXISTS idx_orders_signal_log_id ON public.orders (signal_log_id);

COMMIT;

-- Self-verification
DO $$
DECLARE
    signal_log_id_exists boolean;
    exchange_fee_exists  boolean;
    index_exists         boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'orders' AND column_name = 'signal_log_id'
    ) INTO signal_log_id_exists;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'orders' AND column_name = 'exchange_fee'
    ) INTO exchange_fee_exists;

    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'orders' AND indexname = 'idx_orders_signal_log_id'
    ) INTO index_exists;

    IF NOT signal_log_id_exists THEN
        RAISE EXCEPTION 'Migration 043 FAILED: signal_log_id column not found in orders';
    END IF;

    IF NOT exchange_fee_exists THEN
        RAISE EXCEPTION 'Migration 043 FAILED: exchange_fee column not found in orders';
    END IF;

    IF NOT index_exists THEN
        RAISE EXCEPTION 'Migration 043 FAILED: idx_orders_signal_log_id index not found';
    END IF;

    RAISE NOTICE 'Migration 043 verified OK: orders.signal_log_id and orders.exchange_fee present, index in place';
END $$;
