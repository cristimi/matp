-- Migration 062: record whether an extraction saw the X post's chart image.
-- The reposts from the channel are TradingView screenshots whose annotations
-- ("Flipped longs into Shorts", entry levels) carry the actual position change;
-- the text-only extractor was blind to them. has_image marks rows where the
-- vision path ran, image_sha identifies the exact image (Telegram re-encodes,
-- so this is the sha of the bytes we actually sent to the model).

BEGIN;

ALTER TABLE public.social_signal_log
    ADD COLUMN IF NOT EXISTS has_image boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS image_sha text;

COMMIT;

-- Self-verification
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t.col, ', ')
    INTO missing
    FROM (VALUES ('has_image'), ('image_sha')) AS t(col)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'social_signal_log'
          AND column_name  = t.col
    );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 062 FAILED: missing columns: %', missing;
    END IF;

    RAISE NOTICE 'Migration 062 verified OK: image columns present on social_signal_log';
END $$;
