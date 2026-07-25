-- Migration 063: cache of backtest re-extractions, keyed by message + extractor version.
--
-- Re-extracting a window costs real LLM spend (a 62-day window is ~2.3M tokens).
-- A run that dies partway — or a provider outage mid-run — must not force paying
-- for the whole window again. Postgres rather than a file because the container's
-- /tmp does not survive a --force-recreate redeploy, which is exactly how the
-- first attempt's results were lost.
--
-- Backtest support only: nothing in the live path reads this table. Only
-- SUCCESSFUL extractions are ever written here — a failed call must be retried.

BEGIN;

CREATE TABLE IF NOT EXISTS public.social_extraction_cache (
    source            text        NOT NULL,
    channel_msg_id    bigint      NOT NULL,
    extractor_version text        NOT NULL,
    model             text,
    posted_at         timestamptz NOT NULL,
    payload           jsonb       NOT NULL,
    cached_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, channel_msg_id, extractor_version)
);

CREATE INDEX IF NOT EXISTS ix_social_extraction_cache_window
    ON public.social_extraction_cache (source, extractor_version, posted_at DESC);

COMMIT;

-- Self-verification
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'social_extraction_cache'
    ) THEN
        RAISE EXCEPTION 'Migration 063 FAILED: social_extraction_cache missing';
    END IF;
    RAISE NOTICE 'Migration 063 verified OK: social_extraction_cache present';
END $$;
