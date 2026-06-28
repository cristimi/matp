-- Migration 030: Drop dead columns from public schema
-- Drops columns that are either never written (avg_fill_price) or belong to the
-- broken daily-signal-cap feature (signals_today, max_daily_signals) or are
-- fully retired with no live readers (drawdown_anchor_pnl, win_count, loss_count,
-- platform_override, blofin_token).
-- Does NOT touch tester.* schema — that is a separate later pass.

BEGIN;

-- public.strategies: seven dead columns
ALTER TABLE public.strategies
    DROP COLUMN IF EXISTS drawdown_anchor_pnl,
    DROP COLUMN IF EXISTS win_count,
    DROP COLUMN IF EXISTS loss_count,
    DROP COLUMN IF EXISTS platform_override,
    DROP COLUMN IF EXISTS blofin_token,
    DROP COLUMN IF EXISTS signals_today,
    DROP COLUMN IF EXISTS max_daily_signals;

-- public.order_execution_log: avg_fill_price never written (stuck at default 0)
ALTER TABLE public.order_execution_log
    DROP COLUMN IF EXISTS avg_fill_price;

COMMIT;

-- Self-verification: assert every dropped column is gone
DO $$
DECLARE
    dead_cols TEXT[] := ARRAY[
        'public.strategies.drawdown_anchor_pnl',
        'public.strategies.win_count',
        'public.strategies.loss_count',
        'public.strategies.platform_override',
        'public.strategies.blofin_token',
        'public.strategies.signals_today',
        'public.strategies.max_daily_signals',
        'public.order_execution_log.avg_fill_price'
    ];
    col TEXT;
    tbl TEXT;
    colname TEXT;
    exists_count INT;
BEGIN
    FOREACH col IN ARRAY dead_cols LOOP
        tbl     := split_part(col, '.', 2);
        colname := split_part(col, '.', 3);
        SELECT COUNT(*) INTO exists_count
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = tbl
          AND column_name  = colname;
        IF exists_count > 0 THEN
            RAISE EXCEPTION 'Migration 030 FAILED: column %.% still exists', tbl, colname;
        END IF;
    END LOOP;
    RAISE NOTICE 'Migration 030 verified OK';
END $$;
