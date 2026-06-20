-- Migration 023: dynamic strategy allocation
-- capital_allocation becomes a live compounding balance (+= realized pnl on close,
--   += delta on manual deposit/withdraw).
-- initial_allocation (NEW) = committed capital (seed + net manual deposits); total_return denominator.
-- allocation_peak    (NEW) = high-water mark of capital_allocation; drawdown reference.
-- Existing rows start fresh: both new columns initialised to current capital_allocation.
-- drawdown_anchor_pnl is now unused (left in place; drop deferred).

ALTER TABLE public.strategies
  ADD COLUMN IF NOT EXISTS initial_allocation NUMERIC,
  ADD COLUMN IF NOT EXISTS allocation_peak    NUMERIC;

UPDATE public.strategies
  SET initial_allocation = COALESCE(initial_allocation, capital_allocation),
      allocation_peak    = COALESCE(allocation_peak,    capital_allocation);

-- Mirror onto tester schema for init.sql parity (tester drawdown logic itself is out of scope).
ALTER TABLE tester.strategies
  ADD COLUMN IF NOT EXISTS initial_allocation NUMERIC,
  ADD COLUMN IF NOT EXISTS allocation_peak    NUMERIC;

-- tester.strategies has no capital_allocation column; initialise new columns to 0
UPDATE tester.strategies
  SET initial_allocation = COALESCE(initial_allocation, 0),
      allocation_peak    = COALESCE(allocation_peak,    0);

DO $$
DECLARE
  miss INT;
BEGIN
  SELECT COUNT(*) INTO miss
  FROM (VALUES ('public'),('tester')) AS s(sch)
  CROSS JOIN (VALUES ('initial_allocation'),('allocation_peak')) AS c(col)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = s.sch AND table_name = 'strategies' AND column_name = c.col
  );
  IF miss > 0 THEN
    RAISE EXCEPTION 'Migration 023: % expected columns missing across public/tester.strategies', miss;
  END IF;

  SELECT COUNT(*) INTO miss
  FROM public.strategies
  WHERE initial_allocation IS NULL OR allocation_peak IS NULL;
  IF miss > 0 THEN
    RAISE EXCEPTION 'Migration 023: % public.strategies rows have NULL initial_allocation/allocation_peak', miss;
  END IF;

  RAISE NOTICE 'Migration 023 verified OK';
END $$;
