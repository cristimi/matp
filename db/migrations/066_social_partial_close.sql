-- Migration 066: partial profit-taking (TRIM) for the social listener.
--
-- Until now the social listener could only open, flip and fully close. Every post
-- that managed an open trade — "took half off", "Lock in W 64.4k" — was extracted,
-- forced non-actionable (ADD/TRIM), and discarded by the state machine as
-- `no_target`. See .gemini/reports/social-listener-partial-tp-not-taken.md.
--
-- Three additions:
--
-- 1. social_signal_log gains `size_fraction` (how much of the position comes off)
--    and `trigger_price` (the price the trader names for the trim, if any).
-- 2. social_shadow_orders gains the same fraction plus the absolute `close_size`
--    actually sent, and its decision domain gains 'pending' — a trim whose level
--    the market has not reached yet is neither acted nor skipped.
-- 3. social_pending_trims parks those not-yet-reached trims so a watcher can fire
--    them when the mark crosses the level, instead of taking profit at the wrong
--    price or dropping the instruction entirely.
--
-- All additive and nullable: existing rows are untouched and keep reading exactly
-- as they did.

ALTER TABLE public.social_signal_log
    ADD COLUMN IF NOT EXISTS size_fraction numeric,
    ADD COLUMN IF NOT EXISTS trigger_price numeric;

COMMENT ON COLUMN public.social_signal_log.size_fraction IS
    'Fraction of the position a TRIM takes off, 0..1, as stated by the post. '
    'NULL means the post did not say — the listener applies its default.';
COMMENT ON COLUMN public.social_signal_log.trigger_price IS
    'Price the post names for a TRIM ("Lock in W 64.4k" -> 64400). NULL means the '
    'trim is presented as happening now, at market.';

ALTER TABLE public.social_shadow_orders
    ADD COLUMN IF NOT EXISTS size_fraction numeric,
    ADD COLUMN IF NOT EXISTS close_size    numeric;

COMMENT ON COLUMN public.social_shadow_orders.size_fraction IS
    'Fraction of the open position this trim decision closes, after clamping.';
COMMENT ON COLUMN public.social_shadow_orders.close_size IS
    'Absolute base-asset quantity actually sent for a partial close. NULL for '
    'every other decision, and for a trim that never emitted.';

ALTER TABLE public.social_shadow_orders
    DROP CONSTRAINT IF EXISTS social_shadow_decision_chk;
ALTER TABLE public.social_shadow_orders
    ADD CONSTRAINT social_shadow_decision_chk
    CHECK (decision = ANY (ARRAY['acted'::text, 'skipped'::text, 'pending'::text]));

CREATE TABLE IF NOT EXISTS public.social_pending_trims (
    id             bigserial PRIMARY KEY,
    source         text        NOT NULL,
    channel_msg_id bigint      NOT NULL,
    asset          text        NOT NULL,
    side           text        NOT NULL,
    size_fraction  numeric     NOT NULL,
    trigger_price  numeric     NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    status         text        NOT NULL DEFAULT 'pending',
    resolved_at    timestamptz,
    resolution     text,
    CONSTRAINT social_pending_trim_side_chk
        CHECK (side = ANY (ARRAY['LONG'::text, 'SHORT'::text])),
    CONSTRAINT social_pending_trim_status_chk
        CHECK (status = ANY (ARRAY['pending'::text, 'fired'::text,
                                   'cancelled'::text, 'expired'::text])),
    CONSTRAINT uq_social_pending_trim UNIQUE (source, channel_msg_id)
);

COMMENT ON TABLE public.social_pending_trims IS
    'A partial profit-take the trader named a price for that the market had not '
    'reached when the post was judged. The listener watches the mark and fires the '
    'partial close when it crosses. Cancelled if the recorded stance leaves that '
    'side; expired by TTL so an unreached level cannot fire days later.';

CREATE INDEX IF NOT EXISTS ix_social_pending_trims_open
    ON public.social_pending_trims (asset, side)
    WHERE status = 'pending';

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t || '.' || c, ', ')
      INTO missing
      FROM (VALUES
                ('social_signal_log',   'size_fraction'),
                ('social_signal_log',   'trigger_price'),
                ('social_shadow_orders','size_fraction'),
                ('social_shadow_orders','close_size')
           ) AS want(t, c)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = want.t
                  AND column_name  = want.c
           );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 066 FAILED: missing column(s) %', missing;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'social_pending_trims'
    ) THEN
        RAISE EXCEPTION 'Migration 066 FAILED: social_pending_trims not created';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'social_shadow_decision_chk'
           AND pg_get_constraintdef(oid) LIKE '%pending%'
    ) THEN
        RAISE EXCEPTION 'Migration 066 FAILED: decision check does not allow pending';
    END IF;

    RAISE NOTICE 'Migration 066 verified OK: social partial-close schema present';
END $$;
