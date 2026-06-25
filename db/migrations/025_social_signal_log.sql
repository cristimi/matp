-- 025_social_signal_log.sql
-- Audit/eyeball store for social copy-trading ingestion (Telegram source).
-- READ+PARSE dry-run only. No execution wiring reads from or writes to this table yet.

CREATE TABLE IF NOT EXISTS public.social_signal_log (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT        NOT NULL,
    channel_msg_id    BIGINT      NOT NULL,
    posted_at         TIMESTAMPTZ NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_text          TEXT,
    preview_text      TEXT,
    x_url             TEXT,
    is_actionable     BOOLEAN     NOT NULL,
    action_type       TEXT        NOT NULL,
    asset             TEXT,
    direction         TEXT,
    reference_price   NUMERIC,
    confidence        NUMERIC,
    in_whitelist      BOOLEAN     NOT NULL DEFAULT FALSE,
    model             TEXT,
    extractor_version TEXT,
    raw_llm_json      JSONB,
    CONSTRAINT social_signal_action_type_chk
        CHECK (action_type IN ('OPEN','FLIP','CLOSE','ADD','TRIM','NONE')),
    CONSTRAINT social_signal_direction_chk
        CHECK (direction IS NULL OR direction IN ('LONG','SHORT'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_social_signal_source_msg
    ON public.social_signal_log (source, channel_msg_id);

CREATE INDEX IF NOT EXISTS ix_social_signal_actionable
    ON public.social_signal_log (is_actionable, posted_at DESC);

DO $$
DECLARE miss INT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema='public' AND table_name='social_signal_log') THEN
    RAISE EXCEPTION 'Migration 025: social_signal_log table missing';
  END IF;

  SELECT COUNT(*) INTO miss
  FROM (VALUES ('source'),('channel_msg_id'),('is_actionable'),('action_type'),
               ('reference_price'),('in_whitelist'),('raw_llm_json')) AS c(col)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='social_signal_log' AND column_name=c.col);
  IF miss > 0 THEN
    RAISE EXCEPTION 'Migration 025: % expected columns missing on social_signal_log', miss;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes
                 WHERE schemaname='public' AND indexname='uq_social_signal_source_msg') THEN
    RAISE EXCEPTION 'Migration 025: dedup unique index missing';
  END IF;

  RAISE NOTICE 'Migration 025 verified OK';
END $$;
