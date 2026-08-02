-- Migration 073: per-account position mode (net | hedge).
--
-- BloFin supports two account-wide position modes, set via
-- POST /api/v1/account/set-position-mode:
--
--   net_mode         one netted position per instrument (what we have always used)
--   long_short_mode  a long leg AND a short leg per instrument, addressed by the
--                    positionSide field on every order/close/leverage/TPSL call
--
-- Hedge mode is what lets two strategies hold opposite sides of the same coin on
-- one account (the AstronomerZero social strategy runs against an AI strategy on
-- the same symbol), instead of the listener's same-symbol guard rejecting the
-- second one with opp_pos_conflict.
--
-- ── WHY A COLUMN AND NOT A LIVE EXCHANGE READ ────────────────────────────────
-- The mode is required on EVERY order the adapter sends in hedge mode ("It must
-- be sent in Hedge Mode" — API reference), so it is on the hot path of every
-- trade. Reading it from the exchange per order would add a round trip to every
-- entry, and a failed read would have no safe default: sending positionSide=net
-- to a hedge account is rejected, and sending long/short to a net account is
-- rejected too. So it is stored, and the executor's set-position-mode endpoint is
-- the only writer — it flips the mode ON the exchange, reads it back, and only
-- then persists here, so the column cannot drift from the exchange through this
-- system's own actions. A human flipping the mode in the BloFin app behind our
-- back still causes drift; GET /accounts/{id}/position-mode surfaces that.
--
-- ── WHY NOT MULTI-POSITION ───────────────────────────────────────────────────
-- BloFin's product also has "Multi-Position" (several independent positions on
-- the same pair in the SAME direction). The public API has no way to express it:
-- set-position-mode accepts only net_mode/long_short_mode, and neither
-- POST /api/v1/trade/order nor POST /api/v1/trade/close-position accepts a
-- positionId — positionId appears only in read endpoints. So the API ceiling is
-- one long + one short per instrument, which is exactly what 'hedge' means here.
-- Verified against docs.blofin.com on 2026-08-02.
--
-- ── SCOPE ────────────────────────────────────────────────────────────────────
-- Only the BloFin adapter implements 'hedge'. Binance accounts are refused
-- outright when the exchange reports dual-position mode (order-executor
-- main.py), and Hyperliquid has no equivalent. The CHECK constraint therefore
-- permits the value for any row, but the executor rejects a non-BloFin account
-- being set to 'hedge'.
--
-- Existing rows default to 'net' — the mode every account is in today, so this
-- migration changes no behaviour on its own.

ALTER TABLE public.exchange_accounts
    ADD COLUMN IF NOT EXISTS position_mode character varying(10) NOT NULL DEFAULT 'net';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'exchange_accounts_position_mode_check'
    ) THEN
        ALTER TABLE public.exchange_accounts
            ADD CONSTRAINT exchange_accounts_position_mode_check
            CHECK (position_mode::text = ANY (ARRAY['net'::text, 'hedge'::text]));
    END IF;
END $$;

COMMENT ON COLUMN public.exchange_accounts.position_mode IS
    'Exchange position mode for this account: net (one netted position per '
    'instrument) or hedge (a long leg and a short leg per instrument, addressed '
    'by positionSide on every order). Mirrors BloFin net_mode / long_short_mode. '
    'Written only by the executor after it has flipped and re-read the mode on '
    'the exchange. Only BloFin supports hedge; Binance/Hyperliquid stay net.';

DO $$
DECLARE
    n_bad integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'exchange_accounts'
           AND column_name  = 'position_mode'
           AND is_nullable  = 'NO'
    ) THEN
        RAISE EXCEPTION 'Migration 073 FAILED: exchange_accounts.position_mode missing or nullable';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'exchange_accounts_position_mode_check'
    ) THEN
        RAISE EXCEPTION 'Migration 073 FAILED: position_mode CHECK constraint missing';
    END IF;

    SELECT count(*) INTO n_bad
      FROM public.exchange_accounts
     WHERE position_mode NOT IN ('net', 'hedge');
    IF n_bad > 0 THEN
        RAISE EXCEPTION 'Migration 073 FAILED: % row(s) with an unexpected position_mode', n_bad;
    END IF;

    -- The one-open-position-per-side unique index is what makes a hedge pair
    -- representable at all: (strategy_id, symbol, side) already permits one open
    -- long and one open short. If it is ever narrowed to (strategy_id, symbol),
    -- hedge mode breaks silently, so pin it here.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname  = 'uq_strat_pos_one_open'
           AND indexdef ILIKE '%side%'
    ) THEN
        RAISE EXCEPTION 'Migration 073 FAILED: uq_strat_pos_one_open must still include side';
    END IF;

    RAISE NOTICE 'Migration 073 verified OK: exchange_accounts.position_mode present, default net';
END $$;
