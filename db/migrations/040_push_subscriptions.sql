-- 040_push_subscriptions.sql
-- Registered Android web-push endpoints for notification-service (v1: single device).

CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint      TEXT        NOT NULL UNIQUE,
    p256dh        TEXT        NOT NULL,
    auth          TEXT        NOT NULL,
    user_agent    TEXT,
    enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ
);

DO $$
DECLARE miss INT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema='public' AND table_name='push_subscriptions') THEN
    RAISE EXCEPTION 'Migration 040: push_subscriptions table missing';
  END IF;

  SELECT COUNT(*) INTO miss
  FROM (VALUES ('endpoint'),('p256dh'),('auth'),('user_agent'),
               ('enabled'),('created_at'),('last_seen_at')) AS c(col)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='push_subscriptions' AND column_name=c.col);
  IF miss > 0 THEN
    RAISE EXCEPTION 'Migration 040: % expected columns missing on push_subscriptions', miss;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'push_subscriptions_endpoint_key') THEN
    RAISE EXCEPTION 'Migration 040: endpoint UNIQUE constraint missing';
  END IF;

  RAISE NOTICE 'Migration 040 verified OK';
END $$;
