-- Migration 067: stop management for the social listener.
--
-- Partial profit-taking landed in 066. The other half of the same management card
-- was still dropped: post 9787-9790 said "Risk off the trade" — move the stop to
-- break even — alongside "Lock in W 64.4k", and only the trim was acted on.
--
-- Three additions:
--
-- 1. social_signal_log gains `stop_price` (a level the post names) and
--    `stop_to_breakeven` (the post asks for the stop at entry, no price given).
--    Both ride ALONGSIDE the action_type, because one card routinely trims AND
--    moves the stop — a stop is not a competing action, it is extra instruction.
-- 2. action_type gains 'STOP', for a post whose ONLY position change is the stop.
-- 3. social_position_state gains `stop_price`: the tightest stop this listener has
--    set for the stance it currently holds, so a later post can only tighten it
--    further. Cleared whenever the stance itself changes.
--
-- social_shadow_orders records what happened to the stop half of the decision
-- separately from the position half, so one row per post stays true.
--
-- All additive and nullable.

ALTER TABLE public.social_signal_log
    ADD COLUMN IF NOT EXISTS stop_price        numeric,
    ADD COLUMN IF NOT EXISTS stop_to_breakeven boolean;

COMMENT ON COLUMN public.social_signal_log.stop_price IS
    'Stop level the post names ("SL 66.2k" -> 66200). NULL when none is given.';
COMMENT ON COLUMN public.social_signal_log.stop_to_breakeven IS
    'True when the post asks for the stop at entry without naming a price — '
    '"risk off the trade", "moved to BE", "free trade".';

ALTER TABLE public.social_signal_log
    DROP CONSTRAINT IF EXISTS social_signal_action_type_chk;
ALTER TABLE public.social_signal_log
    ADD CONSTRAINT social_signal_action_type_chk
    CHECK (action_type = ANY (ARRAY['OPEN'::text, 'FLIP'::text, 'CLOSE'::text,
                                    'ADD'::text, 'TRIM'::text, 'STOP'::text,
                                    'NONE'::text]));

ALTER TABLE public.social_shadow_orders
    ADD COLUMN IF NOT EXISTS stop_price  numeric,
    ADD COLUMN IF NOT EXISTS stop_reason text;

COMMENT ON COLUMN public.social_shadow_orders.stop_price IS
    'Stop actually sent to order-listener for this post. NULL when no stop moved.';
COMMENT ON COLUMN public.social_shadow_orders.stop_reason IS
    'Outcome of the stop half of this decision, independent of the position half: '
    'ok / no_stop_instruction / stop_would_widen_risk / stop_already_crossed / '
    'stop_not_tighter / no_position_for_stop / no_entry_price / stop_send_failed.';

ALTER TABLE public.social_position_state
    ADD COLUMN IF NOT EXISTS stop_price numeric;

COMMENT ON COLUMN public.social_position_state.stop_price IS
    'Tightest stop this listener has set for the CURRENT stance. A later post may '
    'only tighten past it. NULL means the listener has not moved the stop, so the '
    'guaranteed SL order-listener injected at entry is still the one in force.';

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t || '.' || c, ', ')
      INTO missing
      FROM (VALUES
                ('social_signal_log',    'stop_price'),
                ('social_signal_log',    'stop_to_breakeven'),
                ('social_shadow_orders', 'stop_price'),
                ('social_shadow_orders', 'stop_reason'),
                ('social_position_state','stop_price')
           ) AS want(t, c)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = want.t
                  AND column_name  = want.c
           );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 067 FAILED: missing column(s) %', missing;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'social_signal_action_type_chk'
           AND pg_get_constraintdef(oid) LIKE '%STOP%'
    ) THEN
        RAISE EXCEPTION 'Migration 067 FAILED: action_type check does not allow STOP';
    END IF;

    RAISE NOTICE 'Migration 067 verified OK: social stop-management schema present';
END $$;
