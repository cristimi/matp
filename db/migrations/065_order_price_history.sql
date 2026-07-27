-- Migration 065: record every price an order has actually rested at.
--
-- A resting limit order is amended in place: order-listener's
-- /strategies/{id}/orders/amend overwrites orders.price / sl_price / tp_price /
-- exchange_order_id and leaves received_at alone. The row therefore carries the
-- LATEST price with the ORIGINAL placement time, and every consumer that joins
-- the two — the order chart above all — draws today's price back to yesterday.
--
-- eth-ai-34d2's order 86ee9b20 was amended 12 times between 2026-07-26 09:01 and
-- 2026-07-27 07:01, walking from 1869.56 up to 1901.84. The chart drew a flat
-- line at 1901.84 starting 09:01, under which the candles sat all day — reading
-- as a buy that should have filled instantly. See
-- .gemini/reports/eth-pending-order-chart-backdated-price.md.
--
-- Nothing stored the intermediate prices. They survived only in container logs
-- (~2 days) and are otherwise unrecoverable, which also made the amend
-- counterfactual in .gemini/reports/ai-limit-orders-no-amend-counterfactual.md
-- impossible to run over more than a single order.
--
-- One row per price the order has held, oldest first. seq 0 is the original
-- placement; each successful amend appends.

CREATE TABLE IF NOT EXISTS public.order_price_history (
    id                bigserial PRIMARY KEY,
    order_id          uuid NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    at                timestamptz NOT NULL DEFAULT now(),
    seq               integer NOT NULL,
    price             numeric,
    sl_price          numeric,
    tp_price          numeric,
    size              numeric,
    exchange_order_id character varying(100),
    source            character varying(20) NOT NULL
);

COMMENT ON TABLE public.order_price_history IS
    'Every price an order has rested at, oldest first. seq 0 = original placement.';
COMMENT ON COLUMN public.order_price_history.source IS
    'placement = original submit; amend = a successful amend; '
    'backfill = reconstructed by migration 065, intermediate steps unknown.';

CREATE UNIQUE INDEX IF NOT EXISTS order_price_history_order_seq_idx
    ON public.order_price_history (order_id, seq);
CREATE INDEX IF NOT EXISTS order_price_history_order_at_idx
    ON public.order_price_history (order_id, at);

-- ── Backfill ────────────────────────────────────────────────────────────────
-- seq 0 for every existing order, from raw_webhook — the one field an amend
-- never overwrites, so it is the true original intent. Priced orders only:
-- a market order has no resting price and nothing to step.

INSERT INTO public.order_price_history
    (order_id, at, seq, price, sl_price, tp_price, size, exchange_order_id, source)
SELECT o.id,
       o.received_at,
       0,
       NULLIF(o.raw_webhook->>'price', '')::numeric,
       NULLIF(o.raw_webhook->>'sl_price', '')::numeric,
       NULLIF(o.raw_webhook->>'tp_price', '')::numeric,
       NULLIF(o.raw_webhook->>'size', '')::numeric,
       NULL,
       'backfill'
FROM public.orders o
WHERE NULLIF(o.raw_webhook->>'price', '') IS NOT NULL
ON CONFLICT (order_id, seq) DO NOTHING;

-- seq 1 wherever the order's CURRENT values differ from its original — proof an
-- amend happened. Its timestamp is updated_at, the last time anything touched
-- the row. The steps in between are gone; source='backfill' is how a reader
-- knows this pair is a reconstruction and not the full walk.

INSERT INTO public.order_price_history
    (order_id, at, seq, price, sl_price, tp_price, size, exchange_order_id, source)
SELECT o.id,
       o.updated_at,
       1,
       o.price,
       o.sl_price,
       o.tp_price,
       o.size,
       o.exchange_order_id,
       'backfill'
FROM public.orders o
WHERE NULLIF(o.raw_webhook->>'price', '') IS NOT NULL
  AND o.price IS NOT NULL
  AND o.updated_at > o.received_at
  AND (
        o.price    IS DISTINCT FROM NULLIF(o.raw_webhook->>'price', '')::numeric
     OR o.sl_price IS DISTINCT FROM NULLIF(o.raw_webhook->>'sl_price', '')::numeric
     OR o.tp_price IS DISTINCT FROM NULLIF(o.raw_webhook->>'tp_price', '')::numeric
  )
ON CONFLICT (order_id, seq) DO NOTHING;
