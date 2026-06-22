-- 027: drop public.strategies.webhook_enabled (orphaned gate removed in Phase 2).
-- Apply only AFTER deploying code that no longer references the column.
ALTER TABLE public.strategies DROP COLUMN IF EXISTS webhook_enabled;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='public' AND table_name='strategies'
               AND column_name='webhook_enabled') THEN
    RAISE EXCEPTION '027 failed: public.strategies.webhook_enabled still present';
  END IF;
END $$;
