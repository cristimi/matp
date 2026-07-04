-- 041_notification_log.sql
-- Audit/dedup/catch-up log for every notification-service delivery attempt.

CREATE TABLE IF NOT EXISTS public.notification_log (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type    TEXT        NOT NULL,
    dedup_key     TEXT,
    position_id   UUID,
    title         TEXT,
    body          TEXT,
    payload       JSONB,
    status        TEXT        NOT NULL,
    error         TEXT,
    device_count  INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ,
    CONSTRAINT notification_log_status_chk
        CHECK (status IN ('sent','failed','skipped'))
);

CREATE INDEX IF NOT EXISTS ix_notification_log_created_at
    ON public.notification_log (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_notification_log_event_type
    ON public.notification_log (event_type);

CREATE INDEX IF NOT EXISTS ix_notification_log_position_id
    ON public.notification_log (position_id);

CREATE INDEX IF NOT EXISTS ix_notification_log_dedup_key
    ON public.notification_log (dedup_key);

DO $$
DECLARE miss INT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema='public' AND table_name='notification_log') THEN
    RAISE EXCEPTION 'Migration 041: notification_log table missing';
  END IF;

  SELECT COUNT(*) INTO miss
  FROM (VALUES ('event_type'),('dedup_key'),('position_id'),('title'),('body'),
               ('payload'),('status'),('error'),('device_count'),
               ('created_at'),('sent_at')) AS c(col)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='notification_log' AND column_name=c.col);
  IF miss > 0 THEN
    RAISE EXCEPTION 'Migration 041: % expected columns missing on notification_log', miss;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'notification_log_status_chk') THEN
    RAISE EXCEPTION 'Migration 041: status CHECK constraint missing';
  END IF;

  IF (SELECT COUNT(*) FROM pg_indexes
      WHERE schemaname='public' AND tablename='notification_log'
        AND indexname IN ('ix_notification_log_created_at','ix_notification_log_event_type',
                           'ix_notification_log_position_id','ix_notification_log_dedup_key')) <> 4 THEN
    RAISE EXCEPTION 'Migration 041: one or more expected indexes missing';
  END IF;

  RAISE NOTICE 'Migration 041 verified OK';
END $$;
