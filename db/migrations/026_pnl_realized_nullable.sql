-- 026: pnl_realized NULL = "not yet booked into allocation".
-- Phase 1 booking idempotency keys on pnl_realized IS NULL, but the column was DEFAULT 0
-- (never NULL), so reconciler/native-SL closes never booked. Drop the default so unbooked
-- rows are NULL; backfill existing 0-rows to NULL. public.strategy_positions only.
ALTER TABLE public.strategy_positions ALTER COLUMN pnl_realized DROP DEFAULT;
UPDATE public.strategy_positions SET pnl_realized = NULL WHERE pnl_realized = 0;
DO $$
BEGIN
  IF (SELECT column_default FROM information_schema.columns
       WHERE table_schema='public' AND table_name='strategy_positions'
         AND column_name='pnl_realized') IS NOT NULL THEN
    RAISE EXCEPTION '026 failed: pnl_realized still has a DEFAULT';
  END IF;
  IF EXISTS (SELECT 1 FROM public.strategy_positions WHERE pnl_realized = 0) THEN
    RAISE EXCEPTION '026 failed: pnl_realized=0 rows remain (expected NULL)';
  END IF;
END $$;
