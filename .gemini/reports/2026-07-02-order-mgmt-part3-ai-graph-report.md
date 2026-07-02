# Active Order Management — Part 3: `ai-signal-generator` works the range with limit orders

Scope: `ai-signal-generator` only (per prompt). `order-executor`, `order-listener`, and the dashboard
were not touched. MMR/safety-SL logic and the 3 pre-existing listener test failures were left alone
(both tracked separately). All three phases complete and verified live against the Blofin demo
account `blofin-blofin-demo-v5vr` (strategy `hype-test-7db4`, HYPE-USDT).

## Summary of changes

- `app/graph/nodes/node_analyze.py`: `LLMSignalOutput.action` extended with `place_limit_long`,
  `place_limit_short`, `cancel_order`, `amend_order`. Added `limit_price` (boundary price for
  `place_limit_*` / new price for `amend_order`) and `target_order_id` (for `cancel_order`/
  `amend_order`, sourced by the LLM from the new OPEN ORDERS context).
- `app/graph/nodes/node_guard.py`:
  - `place_limit_long/short` size off `limit_price` (not `current_price`); reject with
    `limit_price_missing` if absent/non-positive; reject with `sl_tp_pct_out_of_range` same as opens.
  - Position-conflict rule: `place_limit_*` is blocked while a position is already open (same as
    `open_long`/`open_short` — one entry attempt at a time). Independently, a resting order already on
    the same side blocks a new placement (`duplicate_resting_order_same_side`) — reads
    `state['open_orders']` (Phase 2's ingest output; safe no-op if absent/empty).
  - `cancel_order`/`amend_order`: no sizing, require `target_order_id`
    (`target_order_id_missing`); `amend_order` additionally requires `limit_price`
    (`amend_missing_price`). New `AgentState` fields `resolved_limit_price` /
    `resolved_target_order_id` carry the resolved values into dispatch.
  - `_ACTION_COOLDOWN`: `place_limit_long/short` share `cooldown_entry_minutes` with opens;
    `cancel_order`/`amend_order` have no cooldown (order management must be responsive within a cycle).
- `app/webhook/dispatcher.py`: `build_payload` sets `order_type='limit'` + `price=resolved_limit_price`
  for `place_limit_*` (signal still resolves to `open_long`/`open_short`, keeping sl/tp). Added
  `dispatch_cancel_order`/`dispatch_amend_order`, POSTing to the listener's
  `/strategies/{id}/orders/cancel`/`/orders/amend` with the strategy's `webhook_secret` as `token`.
- `app/graph/nodes/node_dispatch.py`: new branch routes `cancel_order`/`amend_order` to the dispatch
  helpers (mirrors `adjust_stops`'s log-then-dispatch shape) but — unlike `adjust_stops` — is
  suppressed under `dry_run`, consistently with how opens/closes are suppressed (log the intent,
  never hit the listener).
- `app/graph/nodes/node_ingest.py`: new `_fetch_open_orders(strategy_id)` — real
  `GET {listener}/strategies/{id}/orders` call, listener only (never the executor directly, per the
  exchange-isolation rule). Runs when `use_geometry` is set; fail-safe (`[]` +
  `data_fetch_errors` append) on error, never raises. Result → `state['open_orders']`.
- `app/prompt/builder.py`: new `_render_open_orders()` renders each resting order (`order_id`, `side`,
  `price`, `size`, `status`) plus an instruction line pointing the LLM at reusing `order_id` as
  `target_order_id` and not stacking a duplicate on an occupied side. Wired into `build_prompt`
  gated the same way as geometry (`use_geometry`), right after the GEOMETRIC PATTERN section.
- `app/graph/state.py`: added `resolved_limit_price`, `resolved_target_order_id`, `open_orders`.
- `app/main.py` / `app/scheduler.py`: initial-state builders extended with the new fields
  (`open_orders`, `resolved_limit_price`, `resolved_target_order_id`); also filled a pre-existing gap
  where `geometry_data` wasn't in the initial dict (harmless either way — `node_ingest` always sets it
  — but kept both builders consistent).
- `db/migrations/038_geometric_range_limit_orders.sql`: **UPDATE**s the existing
  `ai_prompt_templates` row `id='geometric_range'` (inserted by migration 036) — rewrites
  `system_prompt` and `description` to work the range with resting limits instead of market-fading.
  Self-verifying block. `db/init.sql` regenerated (single-row `pg_dump --data-only` splice, 1-line
  diff) and verified by loading the full file into a throwaway database.

`ai-signal-generator`'s `tests/` directory has no `pytest` in its runtime image/dependencies
(pre-existing gap, unrelated to this change — confirmed neither the Dockerfile nor
`requirements.txt` wire up a test stage). All verification below is live, not via that suite.

---

## Phase 1 — place_limit / cancel_order / amend_order plumbing

Exercised `node_guard` → `node_dispatch` directly (no LLM in the loop — the graph nodes are called
with a crafted `llm_signal`, exactly as `node_analyze`'s output would look) against `hype-test-7db4`,
`dry_run=false`.

### `place_limit_long` → resting order appears → `cancel_order` → gone

```
=== place_limit_long ===
guard: gate_passed=True reason=None resolved_size=1.25 resolved_limit_price=40.0 resolved_target_order_id=None
dispatch: webhook_fired=True webhook_status=200 signal_log_id=255

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[{"order_id":"1000131535000","symbol":"HYPE-USDT","side":"buy","price":40.0,"size":1.2,
  "filled_size":0.0,"status":"resting","created_at_ms":1783017486233}]

$ psql ... "SELECT id, strategy_id, proposed_action, confidence, gate_passed, webhook_fired,
            webhook_status, order_id, dry_run FROM ai_signal_log WHERE id=255;"
 id  |  strategy_id   | proposed_action  | confidence | gate_passed | webhook_fired | webhook_status |               order_id               | dry_run
 255 | hype-test-7db4 | place_limit_long |      0.900 | t           | t             |            200 | 4181bade-9880-490d-8bf2-d60a3f707c06 | f
```

```
=== cancel_order (target_order_id="1000131535000") ===
guard: gate_passed=True reason=None resolved_target_order_id=1000131535000
dispatch: webhook_fired=True webhook_status=200 signal_log_id=257

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[]

$ psql ... "SELECT id, status, exchange_order_id FROM orders WHERE exchange_order_id='1000131535000';"
                  id                  |  status   | exchange_order_id
 4181bade-9880-490d-8bf2-d60a3f707c06 | cancelled | 1000131535000
```

### Guard safety checks

Duplicate-side rejection (cooldown neutralized for isolation by backdating prior test-log rows'
`triggered_at`, since `cooldown_entry_minutes or 240` can't be set to a real 0 — pre-existing falsy-`0`
pattern, out of scope):

```
=== place_limit_short with existing resting sell (duplicate side) ===
guard: gate_passed=False reason=duplicate_resting_order_same_side

=== place_limit_short with existing resting BUY (different side, should pass gate) ===
guard: gate_passed=True reason=None
```

`amend_order` — moved a resting short 95 → 92:

```
=== amend_order (target_order_id=1000131535392, limit_price=92.0) ===
guard: gate_passed=True reason=None resolved_limit_price=92.0 resolved_target_order_id=1000131535392
dispatch: webhook_fired=True webhook_status=200 signal_log_id=263

$ docker compose exec -T order-listener curl -s "http://localhost:8001/strategies/hype-test-7db4/orders"
[{"order_id":"1000131535402", ..., "price":92.0, ...}]   -- new exchange id, new price confirmed
```

`dry_run=True` suppresses `cancel_order`/`amend_order` dispatch (no HTTP call to the listener):

```
=== cancel_order under dry_run=True ===
guard: gate_passed=True reason=None resolved_target_order_id=does-not-matter
dispatch: webhook_fired=False webhook_status=None signal_log_id=264
```

All test orders cancelled afterward:

```
$ docker compose exec -T order-executor curl -s ".../accounts/blofin-blofin-demo-v5vr/orders"
[]
$ psql ... "SELECT count(*) FROM orders WHERE status='pending' AND strategy_id='hype-test-7db4';"
 count
     0
```

**Phase 1 gate: confirmed.**

---

## Phase 2 — Resting orders fed into the LLM prompt context

Placed a real resting limit order for `hype-test-7db4` (buy, price=40, far below the ~66.5 market),
then ran `node_ingest` for real (real OHLCV/indicator/geometry/open-orders fetches — no mocking) with
`use_geometry=True`, then `build_prompt`:

```
=== node_ingest output ===
data_fetch_errors: []
open_orders: [{'order_id': '1000131535879', 'symbol': 'HYPE-USDT', 'side': 'buy', 'price': 40.0,
                'size': 1.0, 'filled_size': 0.0, 'status': 'resting', 'created_at_ms': 1783019878314}]
```

Excerpt of the generated prompt:

```
OPEN ORDERS (this strategy's resting limit orders):
  order_id=1000131535879  side=buy  price=40.0  size=1.0  status=resting
Use the order_id above as target_order_id for cancel_order/amend_order. Do not place a new limit on
a side that already has a resting order.
```

Order cancelled and cleanup confirmed (`[]` from listener, `orders.status='pending'` count = 0).

**Phase 2 gate: confirmed.**

---

## Phase 3 — `geometric_range` template rewritten for limit-order range working

Next free migration number confirmed via `ls db/migrations` (`037` was the last; used `038`).
`db/migrations/038_geometric_range_limit_orders.sql` **UPDATE**s the existing row (does not insert a
new template id, does not touch migration 036).

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/038_geometric_range_limit_orders.sql
BEGIN
UPDATE 1
COMMIT
NOTICE:  Migration 038 verified OK: geometric_range system_prompt rewritten for limit-order range working
DO

$ psql ... "SELECT id, name, length(system_prompt) AS prompt_len, description
            FROM ai_prompt_templates WHERE id='geometric_range';"
       id        |            name            | prompt_len |  description
 geometric_range | Geometric Range & Breakout |       5337 | Trades trendline-defined boundaries ...
                                                               by working the range with resting limit orders ...
```

`db/init.sql` regenerated for this one row (`pg_dump --data-only --table=ai_prompt_templates`, spliced
in as a 1-line diff) and verified by loading the *entire* file into a throwaway database:

```
$ docker compose exec -T postgres psql -U matp -d postgres -c "CREATE DATABASE matp_init_check;"
CREATE DATABASE
$ docker compose exec -T postgres psql -U matp -d matp_init_check -v ON_ERROR_STOP=1 < db/init.sql
... (no errors, full schema + seed data loaded)
$ psql ... -d matp_init_check -c "SELECT id, length(system_prompt) FROM ai_prompt_templates WHERE id='geometric_range';"
       id        | length
 geometric_range |   5337        -- matches the live DB exactly
$ docker compose exec -T postgres psql -U matp -d postgres -c "DROP DATABASE matp_init_check;"
DROP DATABASE
```

Full generated prompt for a synthetic `geometric_range` state (ascending triangle, `fit_quality=
strong`, one resting buy order) — both the GEOMETRIC PATTERN and OPEN ORDERS sections present, new
template body rendering correctly:

```
GEOMETRIC PATTERN:
Detected Shape:       Ascending Triangle
Fit Quality:          strong
Upper Boundary:       68.0
Lower Boundary:       61.5
Upper Touches:        3
Lower Touches:        4
Position in Range:    15.0%  (0=at lower boundary, 100=at upper)
Pattern Age:          34 bars
Convergence Rate:     +0.35% of price per bar (boundaries closing in)

OPEN ORDERS (this strategy's resting limit orders):
  order_id=1000131535879  side=buy  price=61.5  size=1.0  status=resting
Use the order_id above as target_order_id for cancel_order/amend_order. Do not place a new limit on
a side that already has a resting order.

STRATEGY INSTRUCTIONS:
You are a quantitative crypto analyst specializing in geometry-driven range and breakout strategies
on perpetual futures. You work the range with RESTING LIMIT ORDERS rather than market-fading it —
each cycle you review the GEOMETRIC PATTERN and OPEN ORDERS sections and choose exactly ONE action:
place a resting limit, amend a resting limit, cancel a resting limit, market-trade a confirmed
breakout, or hold.

PHASE 1 — PATTERN VALIDITY: ...
PHASE 2 — WORKING THE RANGE WITH RESTING LIMITS (channels): ...
PHASE 3 — RE-FIT: AMEND A STALE BOUNDARY ORDER: ...
PHASE 4 — CONVERGING SHAPES (triangles and wedges): ...
PHASE 5 — BREAKOUT OVERRIDE (overrides Phases 2-4 entirely): ...
CONFIDENCE CALIBRATION: ...
```

(Full prompt is ~1,800 estimated tokens; see the phase-3 test transcript above for the complete text
— truncated here for report length.)

No app code changed in this phase (DB-only), so no redeploy was strictly required, but
`ai-signal-generator` was confirmed healthy after Phase 1/2's redeploys and remained healthy through
Phase 3:

```
$ docker compose ps ai-signal-generator
matp-ai-signal-generator-1   ...   Up (healthy)
$ docker compose exec -T ai-signal-generator python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8005/health').read())"
b'{"status":"ok","service":"ai-signal-generator"}'
```

**Phase 3 gate: confirmed.**

---

## Deploy verification

```
$ ./scripts/redeploy.sh ai-signal-generator
...
✓ ai-signal-generator redeployed.
$ docker compose ps ai-signal-generator
matp-ai-signal-generator-1   ...   Up (healthy)
$ docker compose exec -T ai-signal-generator python3 -c "..."
b'{"status":"ok","service":"ai-signal-generator"}'
```

## Final cleanup verification

```
$ psql ... "SELECT count(*) FROM orders WHERE status='pending';"
 count
     0
$ docker compose exec -T order-executor curl -s ".../accounts/blofin-blofin-demo-v5vr/orders"
[]
$ docker compose exec -T order-executor curl -s ".../accounts/hyperliquid-hyperliquid-hqdy/orders"
[]
$ docker compose exec -T ai-signal-generator ls /app | grep -i phase
no leftover test scripts in container
```

`git status --short` shows exactly the intended file set: the 9 `ai-signal-generator` source files,
`db/init.sql`, and the new migration — no stray edits, no leftover scratch files under version
control.

## Explicitly out of scope (per prompt)

`order-executor`, `order-listener`, and the dashboard were not touched. MMR/safety-SL logic was not
touched. Dual-sided bracket logic (placing both boundary limits in one cycle) was intentionally not
built — kept the existing one-action-per-cycle model, per the prompt's design frame. The pre-existing
`cooldown_key or 240` falsy-`0` pattern in `node_guard.py` (a configured `0`-minute cooldown silently
falls back to 240) was noticed during Phase 1 testing but left unfixed — pre-existing behavior,
unrelated to this change, and not present in the new action branches beyond inheriting the same
helper.
