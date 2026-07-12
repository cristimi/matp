-- Migration 053: LLM failure fallback chain + scout/premium model tiering.
--
-- ai_strategy_config:
--   llm_scout_provider / llm_scout_model — optional cheap "scout" model. NULL
--     provider = tiering disabled (existing single-call behavior unchanged).
--   premium_force_interval — every Nth cycle forces a premium call regardless
--     of scout output (guards against scout false negatives).
--   llm_fallback_chain — manual override for the failure fallback chain:
--     jsonb array of {"provider": "...", "model": "..."}. NULL = auto-derive
--     from the probe-verified models registry.
--
-- ai_signal_log:
--   llm_tier — which path produced the logged decision:
--     'premium' | 'scout' | 'scout_escalated' | 'fallback'. NULL on historical
--     rows and non-tiered strategies before this feature.
--   scout_*_tokens — scout call spend when BOTH tiers ran (scout-final cycles
--     keep their usage in the main token columns; scout columns stay NULL).
--   fallback_attempts — jsonb list of {provider, model, error} for every
--     failed LLM attempt in the cycle, for dashboard-side auditing.

BEGIN;

ALTER TABLE public.ai_strategy_config
    ADD COLUMN IF NOT EXISTS llm_scout_provider     varchar(20),
    ADD COLUMN IF NOT EXISTS llm_scout_model        varchar(50),
    ADD COLUMN IF NOT EXISTS premium_force_interval integer NOT NULL DEFAULT 12,
    ADD COLUMN IF NOT EXISTS llm_fallback_chain     jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ai_strategy_config_premium_force_interval_chk'
    ) THEN
        ALTER TABLE public.ai_strategy_config
            ADD CONSTRAINT ai_strategy_config_premium_force_interval_chk
            CHECK (premium_force_interval >= 1 AND premium_force_interval <= 1000);
    END IF;
END $$;

COMMENT ON COLUMN public.ai_strategy_config.llm_scout_provider IS
    'Optional cheap scout model provider; NULL = scout/premium tiering disabled';
COMMENT ON COLUMN public.ai_strategy_config.premium_force_interval IS
    'Every Nth cycle forces a premium call regardless of scout output (1-1000)';
COMMENT ON COLUMN public.ai_strategy_config.llm_fallback_chain IS
    'Manual fallback chain override: jsonb array of {provider, model}; NULL = auto-derive';

ALTER TABLE public.ai_signal_log
    ADD COLUMN IF NOT EXISTS llm_tier            varchar(16),
    ADD COLUMN IF NOT EXISTS scout_input_tokens  integer,
    ADD COLUMN IF NOT EXISTS scout_output_tokens integer,
    ADD COLUMN IF NOT EXISTS scout_total_tokens  integer,
    ADD COLUMN IF NOT EXISTS fallback_attempts   jsonb;

COMMENT ON COLUMN public.ai_signal_log.llm_tier IS
    'Path that produced the decision: premium | scout | scout_escalated | fallback';
COMMENT ON COLUMN public.ai_signal_log.scout_total_tokens IS
    'Scout call spend when both tiers ran; NULL when only one call happened';
COMMENT ON COLUMN public.ai_signal_log.fallback_attempts IS
    'jsonb list of {provider, model, error} for every failed LLM attempt in the cycle';

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text := '';
    col text;
BEGIN
    FOREACH col IN ARRAY ARRAY[
        'llm_scout_provider', 'llm_scout_model', 'premium_force_interval', 'llm_fallback_chain'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'ai_strategy_config'
              AND column_name  = col
        ) THEN
            missing := missing || ' ai_strategy_config.' || col;
        END IF;
    END LOOP;

    FOREACH col IN ARRAY ARRAY[
        'llm_tier', 'scout_input_tokens', 'scout_output_tokens', 'scout_total_tokens', 'fallback_attempts'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'ai_signal_log'
              AND column_name  = col
        ) THEN
            missing := missing || ' ai_signal_log.' || col;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ai_strategy_config_premium_force_interval_chk'
    ) THEN
        missing := missing || ' constraint:premium_force_interval_chk';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION 'Migration 053 FAILED: missing%', missing;
    END IF;

    RAISE NOTICE 'Migration 053 verified OK: scout tiering + fallback chain columns exist';
END $$;
