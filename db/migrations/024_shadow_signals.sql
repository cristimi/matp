-- Migration 024: shadow-mode signal capture for TV-elimination shadow-diff.
-- shadow_signals: what the deterministic signal-engine WOULD emit (not executed).
-- strategies.local_signal_mode: off | shadow | live  (cutover flag, default off).

CREATE TABLE IF NOT EXISTS public.shadow_signals (
    id               bigserial PRIMARY KEY,
    strategy_id      varchar(100) NOT NULL,
    signal_source    varchar(100) NOT NULL,
    symbol           varchar(50)  NOT NULL,
    side             varchar(10)  NOT NULL,
    signal           varchar(20)  NOT NULL,
    signal_bar_time  timestamptz  NOT NULL,
    bar_close_price  numeric,
    bracket_spec     jsonb        DEFAULT '{}'::jsonb NOT NULL,
    generated_at     timestamptz  DEFAULT now() NOT NULL,
    mode             varchar(10)  DEFAULT 'shadow' NOT NULL,
    matched_order_id uuid,
    match_status     varchar(20),
    diff_notes       text,
    UNIQUE (strategy_id, signal, signal_bar_time)
);
CREATE INDEX IF NOT EXISTS idx_shadow_signals_strat_bar
    ON public.shadow_signals (strategy_id, signal_bar_time);

ALTER TABLE public.strategies
    ADD COLUMN IF NOT EXISTS local_signal_mode varchar(10) DEFAULT 'off' NOT NULL;
ALTER TABLE tester.strategies
    ADD COLUMN IF NOT EXISTS local_signal_mode varchar(10) DEFAULT 'off' NOT NULL;

DO $$
DECLARE miss INT;
BEGIN
  SELECT COUNT(*) INTO miss FROM (
    SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables
       WHERE table_schema='public' AND table_name='shadow_signals')
    UNION ALL
    SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
       WHERE table_schema='public' AND table_name='strategies' AND column_name='local_signal_mode')
  ) x;
  IF miss > 0 THEN RAISE EXCEPTION 'Migration 024 verification failed (% missing objects)', miss; END IF;
  RAISE NOTICE 'Migration 024 verified OK';
END $$;
