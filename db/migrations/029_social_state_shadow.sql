-- Migration 029: per-asset state machine + shadow intended-order log. SHADOW ONLY (no execution).

CREATE TABLE IF NOT EXISTS public.social_position_state (
    source      TEXT NOT NULL,
    asset       TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'FLAT',
    last_msg_id BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, asset),
    CONSTRAINT social_state_chk CHECK (state IN ('LONG','SHORT','FLAT'))
);

CREATE TABLE IF NOT EXISTS public.social_shadow_orders (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT        NOT NULL,
    channel_msg_id  BIGINT      NOT NULL,
    posted_at       TIMESTAMPTZ,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    phase           TEXT        NOT NULL,   -- 'backfill' | 'live'
    asset           TEXT,
    action_type     TEXT,                   -- from extraction: OPEN | FLIP | CLOSE
    from_state      TEXT,
    to_state        TEXT,
    intended_signal TEXT,                   -- open_long|close_long|open_short|close_short|flip_to_long|flip_to_short|none
    reference_price NUMERIC,
    mark_price      NUMERIC,
    confidence      NUMERIC,
    decision        TEXT        NOT NULL,   -- 'acted' | 'skipped'
    reason          TEXT        NOT NULL,   -- ok|low_confidence|not_whitelisted|no_target|no_state_change|stale_price|priceless_market|no_mark|backfill_replay
    mode            TEXT        NOT NULL DEFAULT 'shadow',
    CONSTRAINT social_shadow_decision_chk CHECK (decision IN ('acted','skipped')),
    UNIQUE (source, channel_msg_id)
);

CREATE INDEX IF NOT EXISTS ix_social_shadow_decision ON public.social_shadow_orders (decision, evaluated_at DESC);

DO $$
DECLARE miss INT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='social_position_state') THEN
    RAISE EXCEPTION 'Migration 029: social_position_state missing';
  END IF;
  SELECT COUNT(*) INTO miss FROM (VALUES ('phase'),('from_state'),('to_state'),('intended_signal'),('decision'),('reason')) AS c(col)
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='social_shadow_orders' AND column_name=c.col);
  IF miss > 0 THEN RAISE EXCEPTION 'Migration 029: % shadow columns missing', miss; END IF;
  RAISE NOTICE 'Migration 029 verified OK';
END $$;
