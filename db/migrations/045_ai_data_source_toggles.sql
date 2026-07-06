-- Migration 045: add per-data-source toggles for target-state AI prompt fields.
-- All default FALSE — no existing strategy's prompt changes until a toggle is
-- explicitly enabled. See docs/design/ai_prompts/20_plumbing_specs.md.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS use_mtf_structure       boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_orderbook           boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_volume_profile      boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_cvd                 boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_momentum_divergence boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_volatility_regime   boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_funding_history     boolean DEFAULT false NOT NULL,
    ADD COLUMN IF NOT EXISTS use_liquidations        boolean DEFAULT false NOT NULL;

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t.col, ', ')
    INTO missing
    FROM (VALUES
        ('use_mtf_structure'), ('use_orderbook'), ('use_volume_profile'),
        ('use_cvd'), ('use_momentum_divergence'), ('use_volatility_regime'),
        ('use_funding_history'), ('use_liquidations')
    ) AS t(col)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'ai_strategy_config'
          AND column_name  = t.col
          AND column_default LIKE '%false%'
    );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 045 FAILED: missing/wrong-default columns: %', missing;
    END IF;

    RAISE NOTICE 'Migration 045 verified OK: 8 data-source toggle columns present, default=false';
END $$;
