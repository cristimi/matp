-- Migration 034: backfill synthetic close orders for historical closed positions.
--
-- Step 1 (INSERT): every closed position that has no order linked via closes_position_id
--   gets one synthetic close order, and the position's closing_order_id is set to it.
--   Idempotent: the NOT EXISTS guard makes re-runs no-ops.
--
-- Step 2 (UPDATE): pre-Phase-2 reconciler synthetic close orders that were inserted with
--   size = 0 (hardcoded bug) are corrected to the linked position's actual size.
--   Scope: only orders where closes_position_id IS NOT NULL AND size = 0
--          AND signal_source = 'reconciler'. PnL is untouched.
--   Idempotent: the size = 0 predicate makes re-runs no-ops.

DO $$
DECLARE
  v_candidate_count    INT;
  v_inserted_count     INT;
  v_remaining_no_close INT;
  v_zero_size_count    INT;
  v_updated_count      INT;
  v_remaining_zero     INT;
BEGIN

  -- ── STEP 1: insert missing close orders ────────────────────────────────────

  SELECT COUNT(*) INTO v_candidate_count
  FROM strategy_positions sp
  WHERE sp.status = 'closed'
    AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.closes_position_id = sp.id);

  RAISE NOTICE 'Step 1 candidates (closed positions with no linked close order): %', v_candidate_count;

  -- Insert one synthetic close order per qualifying position, then immediately
  -- write its id back into strategy_positions.closing_order_id using a CTE.
  WITH ins AS (
    INSERT INTO orders (
      symbol, side, signal, order_type, size, platform,
      strategy_id, account_id, status, actual_fill_price,
      pnl, raw_webhook, signal_source, closes_position_id
    )
    SELECT
      sp.symbol,
      CASE WHEN sp.side = 'long' THEN 'sell' ELSE 'buy' END,
      'exchange_close',
      'market',
      sp.size,
      'exchange',
      sp.strategy_id,
      COALESCE(o_open.account_id, s.account_id),
      'filled',
      sp.closing_price,
      sp.pnl_realized,
      '{}'::jsonb,
      'reconciler',
      sp.id
    FROM strategy_positions sp
    JOIN strategies s ON s.id = sp.strategy_id
    LEFT JOIN orders o_open ON o_open.id = sp.opening_order_id
    WHERE sp.status = 'closed'
      AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.closes_position_id = sp.id)
    RETURNING id, closes_position_id
  )
  UPDATE strategy_positions sp
  SET closing_order_id = ins.id,
      updated_at       = NOW()
  FROM ins
  WHERE sp.id = ins.closes_position_id;

  GET DIAGNOSTICS v_inserted_count = ROW_COUNT;
  RAISE NOTICE 'Step 1 inserted: % close orders (closing_order_id backfilled on positions)', v_inserted_count;

  SELECT COUNT(*) INTO v_remaining_no_close
  FROM strategy_positions sp
  WHERE sp.status = 'closed'
    AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.closes_position_id = sp.id);

  RAISE NOTICE 'Step 1 remaining (still no close order): %', v_remaining_no_close;

  IF v_remaining_no_close > 0 THEN
    RAISE EXCEPTION 'Step 1 FAILED: % closed positions still have no linked close order after insert',
      v_remaining_no_close;
  END IF;

  -- ── STEP 2: fix pre-Phase-2 size=0 reconciler close orders ────────────────

  SELECT COUNT(*) INTO v_zero_size_count
  FROM orders o
  WHERE o.closes_position_id IS NOT NULL
    AND o.size = 0
    AND o.signal_source = 'reconciler';

  RAISE NOTICE 'Step 2 candidates (size=0 reconciler close orders): %', v_zero_size_count;

  UPDATE orders o
  SET size = sp.size
  FROM strategy_positions sp
  WHERE o.closes_position_id = sp.id
    AND o.closes_position_id IS NOT NULL
    AND o.size = 0
    AND o.signal_source = 'reconciler';

  GET DIAGNOSTICS v_updated_count = ROW_COUNT;
  RAISE NOTICE 'Step 2 updated: % orders', v_updated_count;

  SELECT COUNT(*) INTO v_remaining_zero
  FROM orders o
  WHERE o.closes_position_id IS NOT NULL
    AND o.size = 0
    AND o.signal_source = 'reconciler';

  RAISE NOTICE 'Step 2 remaining (size=0 reconciler close orders): %', v_remaining_zero;

  IF v_remaining_zero > 0 THEN
    RAISE EXCEPTION 'Step 2 FAILED: % size=0 reconciler close orders remain after update',
      v_remaining_zero;
  END IF;

END $$;
