-- Migration 074: social_position_state becomes one row per LEG, not one per asset.
--
-- The listener modelled the channel as a single stance per asset: FLAT | LONG |
-- SHORT, with LONG->SHORT expressed as a flip. That is a faithful model of a net
-- account and an unfaithful model of the trader, who can be long and short the same
-- coin at once. Migration 073 gave BloFin accounts the ability to hold both legs;
-- this gives the listener the ability to record them.
--
-- ── SHAPE ────────────────────────────────────────────────────────────────────
-- Before: (source, asset)        -> state in (FLAT, LONG, SHORT)
-- After:  (source, asset, side)  -> state in (OPEN, FLAT), side in (LONG, SHORT)
--
-- Each leg carries its OWN stop_price / tp_price / stop_mode / last_msg_id. That is
-- the point: a long leg's break-even stop and a short leg's are different numbers,
-- and the old single row could only hold one of them.
--
-- A missing row means that leg is flat, exactly as a missing row meant FLAT before.
-- So FLAT rows are dropped rather than doubled into two flat legs — they carried
-- nothing but a last_msg_id that is also in social_shadow_orders.
--
-- ── MIGRATING THE LIVE ROW ───────────────────────────────────────────────────
-- A row saying state='SHORT' means "the short leg is open", so it becomes
-- (side='SHORT', state='OPEN') and keeps its levels. Nothing is inferred about the
-- other leg: it gets no row, i.e. flat, which is what it was.
--
-- ── WHY NOT A NEW TABLE ──────────────────────────────────────────────────────
-- The old table's whole content is one row per open stance. Rewriting it in place
-- keeps the levels attached to the position they belong to; a new table would have
-- meant either a dual-read window or losing the live BTC short's audit trail.

-- Column first, populated from the old state so nothing is guessed.
ALTER TABLE public.social_position_state
    ADD COLUMN IF NOT EXISTS side text;

UPDATE public.social_position_state
   SET side = state
 WHERE side IS NULL AND state IN ('LONG', 'SHORT');

-- Flat stances hold no position and therefore no leg.
DELETE FROM public.social_position_state
 WHERE side IS NULL OR state = 'FLAT';

-- The old CHECK only permits FLAT/LONG/SHORT, so it has to go before `state` can
-- be rewritten to OPEN — dropping it after the UPDATE fails on the UPDATE itself.
ALTER TABLE public.social_position_state
    DROP CONSTRAINT IF EXISTS social_state_chk;

-- Every surviving row is an open leg.
UPDATE public.social_position_state SET state = 'OPEN' WHERE state <> 'OPEN';

ALTER TABLE public.social_position_state
    ALTER COLUMN side SET NOT NULL;

ALTER TABLE public.social_position_state
    ADD CONSTRAINT social_state_chk
    CHECK (state = ANY (ARRAY['OPEN'::text, 'FLAT'::text]));

ALTER TABLE public.social_position_state
    DROP CONSTRAINT IF EXISTS social_position_state_side_chk;
ALTER TABLE public.social_position_state
    ADD CONSTRAINT social_position_state_side_chk
    CHECK (side = ANY (ARRAY['LONG'::text, 'SHORT'::text]));

ALTER TABLE public.social_position_state
    DROP CONSTRAINT IF EXISTS social_position_state_pkey;
ALTER TABLE public.social_position_state
    ADD CONSTRAINT social_position_state_pkey PRIMARY KEY (source, asset, side);

COMMENT ON COLUMN public.social_position_state.side IS
    'Which leg this row is: LONG or SHORT. One row per open leg per asset — a '
    'missing row means that leg is flat. Two rows for one asset means the channel '
    'is recorded as holding both sides, which only a hedge-mode account can honour.';

COMMENT ON COLUMN public.social_position_state.state IS
    'OPEN or FLAT for THIS leg. Rows are normally deleted rather than set FLAT; the '
    'value exists so a leg can be closed without losing its row mid-transaction.';

DO $$
DECLARE
    n_bad     integer;
    n_legs    integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='social_position_state'
           AND column_name='side' AND is_nullable='NO'
    ) THEN
        RAISE EXCEPTION 'Migration 074 FAILED: social_position_state.side missing or nullable';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='social_position_state_pkey'
           AND pg_get_constraintdef(oid) ILIKE '%side%'
    ) THEN
        RAISE EXCEPTION 'Migration 074 FAILED: primary key does not include side';
    END IF;

    SELECT count(*) INTO n_bad FROM public.social_position_state
     WHERE side NOT IN ('LONG','SHORT') OR state NOT IN ('OPEN','FLAT');
    IF n_bad > 0 THEN
        RAISE EXCEPTION 'Migration 074 FAILED: % row(s) with an unexpected side/state', n_bad;
    END IF;

    -- The trims ledger is already per-leg (side column + side CHECK). If that ever
    -- stopped being true, a parked trim could not say which leg it belonged to.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='social_pending_trims'
           AND column_name='side'
    ) THEN
        RAISE EXCEPTION 'Migration 074 FAILED: social_pending_trims.side is required for per-leg trims';
    END IF;

    SELECT count(*) INTO n_legs FROM public.social_position_state;
    RAISE NOTICE 'Migration 074 verified OK: % open leg(s) carried over', n_legs;
END $$;
