-- Migration 033: extend strategy_source comment to include social and internal values
-- strategy_source is a plain VARCHAR with no CHECK constraint, so no rows change.
-- This formalises 'social' and 'internal' as valid values alongside existing ones.

BEGIN;

COMMENT ON COLUMN public.strategies.strategy_source IS
  'Signal source: tradingview | ai_engine | social | internal | manual';

COMMIT;

-- Self-verification
DO $$
DECLARE
    comment_text TEXT;
BEGIN
    SELECT description INTO comment_text
    FROM pg_description
    JOIN pg_class       ON pg_class.oid       = pg_description.objoid
    JOIN pg_attribute   ON pg_attribute.attrelid = pg_class.oid
                       AND pg_attribute.attnum    = pg_description.objsubid
    WHERE pg_class.relname     = 'strategies'
      AND pg_attribute.attname = 'strategy_source';

    IF comment_text IS NULL OR (comment_text NOT LIKE '%social%' OR comment_text NOT LIKE '%internal%') THEN
        RAISE EXCEPTION 'Migration 033 FAILED: column comment missing social/internal';
    END IF;
    RAISE NOTICE 'Migration 033 verified OK';
END $$;
