-- Migration 068: scaling in (ADD), take-profit levels, and standing stop intent.
--
-- Three gaps left after 066 (partial close) and 067 (stop management):
--
-- 1. ADD was the last position change still forced non-actionable. It now sizes at
--    `add_multiple` x one standard entry (default half), capped cumulatively so
--    repeated adds cannot compound past `max_position_multiple` standard entries.
-- 2. Take-profit levels were never extracted or applied. The trader routinely posts
--    the first call as text and only shows entry/SL/TP on a chart in a LATER post,
--    so levels have to be applicable to a position we already hold.
-- 3. "Risk off the trade" was a one-shot. It is really a STANDING instruction: after
--    an ADD blends the entry price, break-even means a different number. `stop_mode`
--    records the intent so the watcher can re-assert it against the new entry.
--
-- `social_position_state.tp_price` matters for correctness, not just audit:
-- order-executor's modify-stops is cancel-then-place across EVERY trigger, so any
-- call that moves the stop must also re-send the take-profit or it is deleted. The
-- opening order's tp_price cannot serve as that memory once we start setting TPs
-- ourselves.
--
-- All additive and nullable.

ALTER TABLE public.social_signal_log
    ADD COLUMN IF NOT EXISTS take_profit_price numeric,
    ADD COLUMN IF NOT EXISTS add_multiple      numeric;

COMMENT ON COLUMN public.social_signal_log.take_profit_price IS
    'Take-profit level the post gives, in text or read off an annotated chart.';
COMMENT ON COLUMN public.social_signal_log.add_multiple IS
    'Size of an ADD as a multiple of one standard entry (margin_per_trade x '
    'leverage). NULL means the post gave no amount — the listener uses its default.';

ALTER TABLE public.social_shadow_orders
    ADD COLUMN IF NOT EXISTS tp_price  numeric,
    ADD COLUMN IF NOT EXISTS tp_reason text,
    ADD COLUMN IF NOT EXISTS add_size  numeric;

COMMENT ON COLUMN public.social_shadow_orders.tp_price IS
    'Take-profit actually sent for this post. NULL when no take-profit moved.';
COMMENT ON COLUMN public.social_shadow_orders.tp_reason IS
    'Outcome of the take-profit half of this decision: ok / no_tp_instruction / '
    'tp_wrong_side / tp_already_crossed / tp_unchanged / no_position_for_tp.';
COMMENT ON COLUMN public.social_shadow_orders.add_size IS
    'Base-asset quantity added by a scale-in, after the cumulative exposure cap.';

ALTER TABLE public.social_position_state
    ADD COLUMN IF NOT EXISTS stop_mode text,
    ADD COLUMN IF NOT EXISTS tp_price  numeric;

COMMENT ON COLUMN public.social_position_state.stop_mode IS
    'Standing stop intent for the current stance. ''breakeven'' means the trader '
    'asked to de-risk, so the watcher re-asserts the stop at the position''s entry '
    'whenever that entry moves (an ADD blends it). NULL means no standing intent — '
    'an explicitly named level is a one-shot and lives in stop_price alone.';
COMMENT ON COLUMN public.social_position_state.tp_price IS
    'Take-profit this listener last set for the current stance, so a later stop '
    'move can re-send it. modify-stops cancels every trigger and places only what '
    'it is given, so forgetting this would silently delete the take-profit.';

ALTER TABLE public.social_position_state
    DROP CONSTRAINT IF EXISTS social_state_stop_mode_chk;
ALTER TABLE public.social_position_state
    ADD CONSTRAINT social_state_stop_mode_chk
    CHECK (stop_mode IS NULL OR stop_mode = 'breakeven');

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t || '.' || c, ', ')
      INTO missing
      FROM (VALUES
                ('social_signal_log',     'take_profit_price'),
                ('social_signal_log',     'add_multiple'),
                ('social_shadow_orders',  'tp_price'),
                ('social_shadow_orders',  'tp_reason'),
                ('social_shadow_orders',  'add_size'),
                ('social_position_state', 'stop_mode'),
                ('social_position_state', 'tp_price')
           ) AS want(t, c)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = want.t
                  AND column_name  = want.c
           );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 068 FAILED: missing column(s) %', missing;
    END IF;

    RAISE NOTICE 'Migration 068 verified OK: social add/levels schema present';
END $$;
