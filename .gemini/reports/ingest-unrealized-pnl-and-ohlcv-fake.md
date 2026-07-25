# Fix: unrealized P&L never reached the prompt; `_FakeExchange` broke its own test

**Date:** 2026-07-25
**Trigger:** follow-up to `ai-strategy-sizing-and-close-gate.md`, both items flagged there as not addressed
**Services touched:** `ai-signal-generator`

## 1. `position_unrealized_pnl_pct` was always None

Both state builders seeded the field as `None` and nothing ever filled it in:

- `app/scheduler.py:197` — `'position_unrealized_pnl_pct': None,`
- `app/main.py:337` — `'position_unrealized_pnl_pct': None,`

`prompt/builder.py:45` reads that field into the position header, so every
exit-evaluation cycle told the model:

```
⚠️  ACTIVE POSITION — EXIT EVALUATION MODE
Direction:     long
Entry Price:   63000.0
Current P&L:   N/A%
```

The model was asked to decide whether to hold, trail, or close a position while being told its
own P&L was unknown. Templates lean on exactly this number — "once the move covers half the
target, adjust_stops to breakeven" is unanswerable against `N/A`.

### Why it was empty rather than wrong

The schedulers build initial state before any price is known — `ohlcv_data` is `None` at that
point and only gets populated by `node_ingest`. So the fix belongs in ingest, which is the first
place `current_price` exists, not at the seeding sites.

### Change

`app/graph/nodes/node_ingest.py`: new `_unrealized_pnl_pct(state, ohlcv_data)` helper, called
from the node's return dict. Extracted as a module-level function rather than left inline so it
is unit-testable — `_node_ingest` itself fans out into a dozen network fetches.

**Unit is the price move in the position's favour**, not the leveraged return on margin. That is
deliberate: it matches the unit the model already sets `stop_loss_pct` / `take_profit_pct` in
(`node_guard` derives stop prices as `entry × (1 ± pct/100)`), so "Current P&L" is directly
comparable to the target the model itself chose. Reporting a 20x-leveraged margin return here
would silently break every template rule that compares progress against the target.

Missing entry price, missing current price, a zero entry, or a non-numeric entry all return the
incoming value unchanged — so a bad input stays `N/A` rather than becoming a fabricated `0.0%`.

### Verification — rendered header, live container

```
=== BEFORE (value left as None, as the schedulers seeded it) ===
    Direction:     long
    Entry Price:   63000.0
    Current P&L:   N/A%

=== AFTER (ingest fills it in) ===
  [long 63000 -> 63750]
    Direction:     long
    Entry Price:   63000.0
    Current P&L:   1.19%
  [short 63000 -> 62400]
    Direction:     short
    Entry Price:   63000.0
    Current P&L:   0.952%
```

Short sign is flipped, not the magnitude: price down on a short is a gain.

New `tests/test_ingest_pnl.py`, 12 cases — long/short in profit and loss, flat at entry,
the take-profit-unit equivalence, and each degenerate input.

## 2. `_FakeExchange` was missing the ccxt surface the code uses

`tests/test_ohlcv.py::test_fetch_ohlcv_separates_closed_candles_from_live_price` was failing:

```
WARNING  app.data.ohlcv:ohlcv.py:182 fetch_ohlcv error [binance BTC/USDT 15m]:
         '_FakeExchange' object has no attribute 'fetch_markets'
E   assert None is not None
```

The fake predated the `load_markets_cached` layer (`ohlcv.py:26`). It offered `load_markets()`
and a pre-baked `.markets` dict, but the real path now calls `fetch_markets()` and hands the
result to `set_markets()`. The `AttributeError` was swallowed by `fetch_ohlcv`'s broad
`except` into a `None` return, so the assertion failed one line later.

This is a stale test double, not a product bug — the assertion it guards
(closed candles separated from the still-forming candle used for `current_price`) had simply
stopped being exercised.

### Change

`_FakeExchange` now mirrors the slice of the ccxt async API that `ohlcv.py` actually uses:
`fetch_markets()`, `set_markets()`, `.markets`, `.id`, `fetch_ohlcv()`, `close()`. The test also
clears `_markets_cache` / `_markets_locks`, which memoize at module scope — without that the
test's result depends on whether something else populated the `binance` entry first.

## Verification

```
$ docker compose run --rm --no-deps -v .../ai-signal-generator:/src -w /src ai-signal-generator \
    sh -lc 'python -m pytest tests/test_ohlcv.py -q'
.....                                                                    [100%]
5 passed in 3.99s
```

Full suite, previously `1 failed, 95 passed`:

```
$ ... python -m pytest tests/ -q
108 passed, 2 warnings in 9.02s
```

108 = 95 existing + 1 repaired + 12 new P&L cases.

Deploy, with the new code confirmed in the running container:

```
$ docker compose ps ai-signal-generator --format '{{.Service}}\t{{.Status}}'
ai-signal-generator	Up 45 seconds (healthy)
$ docker compose exec -T ai-signal-generator grep -c "_unrealized_pnl_pct" /app/app/graph/nodes/node_ingest.py
4
$ docker compose logs ai-signal-generator --since 3m | grep -iE "error|traceback"
(none)
```

## Notes

- No open positions existed at verification time, so the live `preview-prompt` endpoint would not
  render a position header. Verified instead by calling the real `_unrealized_pnl_pct` and the
  real `prompt/builder._render_header` inside the running container — the same two functions the
  scheduled cycle uses, so the rendered line above is what a live cycle will now produce.
- The seeding sites (`scheduler.py:197`, `main.py:337`) were left as `None` on purpose: they run
  before a price exists, and ingest overwrites the field. Changing them would only move the
  problem.
