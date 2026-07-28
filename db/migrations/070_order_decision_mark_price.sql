-- Migration 070: decision-time exchange mark price on every order.
--
-- docs/process/reports/2026-07-28-paper-trade-forensics.md §4: entry slippage is
-- unmeasurable on 148 of 163 entries because a market order stores no intended
-- reference price. orders.price is NULL on every market order ever placed
-- (184 ai_engine, 163 tv_test, 28 tradingview, 7 social_listener — all zero), so
-- actual_fill_price has nothing to be compared against.
--
-- This column is that missing reference: the exchange mark price as observed by
-- order-listener at the moment the order was created, before it was routed.
-- Slippage is then computed AT READ TIME —
--
--     long  entry: (actual_fill_price - mark_price_at_decision) / mark_price_at_decision
--     short entry: (mark_price_at_decision - actual_fill_price) / mark_price_at_decision
--
-- — deliberately NOT stored, so a change of definition never needs a backfill.
--
-- ── WHY NOT REUSE orders.indicator_price ──────────────────────────────────────
-- The forensics report listed indicator_price as "exists but is unused". That is
-- true of the DATA (0 of 508 rows populated) and false of the CODE. It is a live
-- webhook payload field (order-listener/app/models.py) and the FIRST term of the
-- sizing reference price:
--
--     _ref_price = float(payload.indicator_price or payload.price or 0)
--
-- in webhook_handler.py, which feeds the margin-per-trade size clamp, the
-- guaranteed-SL injection and the min-order-size estimate, and is read again as an
-- entry-price fallback in three more places plus two dashboard-ui pages. Writing a
-- mark price into it would silently change position sizing and stop placement.
-- That is a trading-behaviour change; this work is telemetry only. Hence a new,
-- separate column with a name that says exactly what it holds.
--
-- ── WHAT IS AND IS NOT POPULATED ──────────────────────────────────────────────
-- Populated for every order created through the webhook path (order-listener
-- _log_order) — that is every AI-engine, TradingView, social-listener and manual
-- order, entries and exits alike.
--
-- Left NULL, by design, on synthetic close orders written after the fact by
-- reconciler.py and ai-signal-generator/app/scheduler.py when an external close is
-- discovered. Those rows record a close the exchange already performed; there was
-- no local decision instant for a mark price to belong to, and inventing one would
-- make exit-slippage figures fictional.
--
-- Also NULL whenever the executor's mark-price read failed. get_mark_price returns
-- None on failure and never raises; the order proceeds unchanged and the column
-- stays NULL. Telemetry never blocks a trade.
--
-- Existing rows stay NULL — not backfillable, no historical mark prices are kept.

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS mark_price_at_decision numeric;

COMMENT ON COLUMN public.orders.mark_price_at_decision IS
    'Exchange mark price observed by order-listener when this order was created, '
    'before routing. The reference that makes slippage measurable against '
    'actual_fill_price — compute slippage at read time, it is not stored. '
    'NULL means: order predates 2026-07-28, or it is a synthetic close order '
    'written after an external close (no local decision instant existed), or the '
    'mark-price read failed. Distinct from indicator_price, which is a webhook '
    'payload field that feeds sizing and stop placement.';

CREATE INDEX IF NOT EXISTS orders_mark_price_at_decision_idx
    ON public.orders (received_at DESC)
    WHERE mark_price_at_decision IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'orders'
           AND column_name  = 'mark_price_at_decision'
           AND data_type    = 'numeric'
    ) THEN
        RAISE EXCEPTION 'Migration 070 FAILED: orders.mark_price_at_decision missing or not numeric';
    END IF;

    -- indicator_price must survive untouched: this migration must not be mistaken
    -- for a repurposing of it.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'orders'
           AND column_name  = 'indicator_price'
    ) THEN
        RAISE EXCEPTION 'Migration 070 FAILED: orders.indicator_price was expected to still exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname  = 'orders_mark_price_at_decision_idx'
    ) THEN
        RAISE EXCEPTION 'Migration 070 FAILED: orders_mark_price_at_decision_idx missing';
    END IF;

    RAISE NOTICE 'Migration 070 verified OK: orders.mark_price_at_decision present, indicator_price untouched';
END $$;
