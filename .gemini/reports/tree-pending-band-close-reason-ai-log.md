# Follow-up: pending-order band fix, close_reason gap, AI Signal Log rework

Follow-up to `tree-llm-pending-orders.md`. Three separate fixes/changes in this pass.

## 1. Yellow band moved to the pending-order card, not the strategy card

Original implementation put an extra yellow strip on the strategy card's left accent bar
whenever it had pending orders. Per feedback, that band belongs on the pending order itself,
not the whole strategy card.

- `dashboard-ui/src/pages/StrategyTree.tsx`: removed the "Pending-orders band" div from
  `StrategyCard`. `PendingOrderCard` now has its own left accent band (`position: relative`,
  absolute 4px yellow strip), matching the app's existing card-accent-bar convention, replacing
  its previous full yellow border-box style.
- The yellow status dot on the strategy card (green if open position, else yellow if only
  pending orders) is unchanged — that one *is* strategy-level, per the original request.

## 2. close_reason: fixed the NULL gap + humanized display

Investigation found `close_reason` was NULL for the three most common live-trading close
paths (manual "Close Position" button, TradingView `flat` signal, `close_long`/`close_short`
signals) — only auto-disable, flip-close, and reconciler-detected exchange closes ever set it.
Since the Tree page only rendered the "Close reason" row when truthy, most closed positions
showed no reason at all.

- `order-listener/app/webhook_handler.py`:
  - `POST /positions/{id}/close` (manual close) → `reason="manual_close"`
  - `target_position=flat` signal handler → `reason="signal_flat"`
  - `close_long`/`close_short` signal handler → `reason="signal_close"`
- `dashboard-ui/src/pages/StrategyTree.tsx`: added `CLOSE_REASON_LABELS` + `formatCloseReason()`
  humanizing all known codes (`manual_close` → "Manual close", `flatten_on_disable` → "Strategy
  stopped", `flip_close` → "Position flipped", etc.), falling back to `Unknown` instead of
  hiding the row, and to `snake_case → "snake case"` for any future unmapped code.
- Explicitly out of scope (per user decision): distinguishing SL-hit / TP-hit / manual-on-exchange
  for reconciler-detected closes — both exchange adapters only expose a liquidation flag today;
  telling those apart would require cross-referencing each exchange's TP/SL order history against
  the close timestamp, which is real, separate work.

## 3. AI Signal Log: LLM-failure visibility + mobile layout

- `dashboard-ui/src/pages/AiSignalLog.tsx`:
  - Traced the "BLOCKED" gate badge to `gate_rejection_reason` values set in
    `ai-signal-generator/app/graph/nodes/node_guard.py`. When the LLM call itself fails
    (`node_analyze.py` sets `llm_signal: None` on parse failure or exception), `node_guard.py:40`
    rejects with reason `'llm_failed'` — previously indistinguishable from a normal gate rejection
    (confidence too low, cooldown active, etc.), all rendered as generic red "BLOCKED".
  - `GateBadge` now renders a distinct amber "LLM FAILED" badge specifically for
    `gate_rejection_reason === 'llm_failed'`; all other rejection reasons keep "BLOCKED".
  - Added `LlmChip` (provider/model pill) to each row, populated from `row.llm_provider` /
    `row.llm_model` (already returned by the API, previously only shown in the expanded detail).
  - Redesigned the summary row from one cramped horizontal line into two: line 1 is
    strategy + action badge + timestamp + chevron; line 2 is gate badge + confidence bar + LLM
    chip + webhook/trigger info (wraps if needed). Fixes overflow on phone-width screens.

## Verification

Python syntax check:
```
$ python3 -m py_compile order-listener/app/webhook_handler.py
py_compile OK
```

Typecheck (dashboard-ui, run after each edit round):
```
npx tsc --noEmit → no output, exit 0   (x3 passes, one per edit round)
```

Redeploys:
```
./scripts/redeploy.sh order-listener  → Up, health: starting → healthy
  $ docker compose exec nginx wget -qO- http://order-listener:8001/health
  {"status":"ok","service":"order-listener"}

./scripts/redeploy.sh dashboard-ui    → asset index-BBg5H4Eq.js (band + close_reason fixes)
./scripts/redeploy.sh dashboard-ui    → asset index-URPx0Fkb.js (AI Signal Log rework, superseding build)
  $ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
  index-URPx0Fkb.js
  $ docker compose exec -T dashboard-ui grep -rl 'LLM FAILED' /usr/share/nginx/html
  /usr/share/nginx/html/assets/index-URPx0Fkb.js
```

Live-data trace for the "pending order not visible" report (read-only investigation, DB queries
via `docker compose exec postgres psql`): confirmed the ETH AI Geometric Range strategy's
resting buy-limit order (`f7539182…`, Hyperliquid oid `56272857224`) was correctly served by the
new `pending_orders` field while it was actually resting (11:03–12:06 UTC on 2026-07-10), was
successfully amended in place at 12:00 (`amend_order`, `orders.price` updated, still `pending`),
and was then marked `cancelled` by the reconciler's pending-order sweep (`reconciler.py:473-535`)
at 12:06 after it stopped appearing in the exchange's open-orders list with no corresponding
position size increase — standard fill-vs-cancel disambiguation, not a bug in the new feature.
No code changed for this item; reported to user as investigation only.

Not yet done: an actual browser visual pass on any of these three changes (band placement,
close-reason text, AI Signal Log two-line layout) — confirmed via bundle string checks and API
data, not a screenshot/UI walkthrough.
