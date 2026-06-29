# Strategy Tree — Phase 2 UI

## Phase 2A — Scaffold + L1 Header

### Deployed bundle

```
Asset hash: index-DsHX8WFe.js
```

grep confirm (count=1, routes exist in minified bundle):
```
docker compose exec -T dashboard-ui grep -c 'strategies/tree' /usr/share/nginx/html/assets/index-DsHX8WFe.js
→ 1
```

### Real curl: GET /strategies/tree

```json
[
  {
    "id": "hype-breakout-da2e",
    "name": "HYPE Breakout",
    "symbol": "HYPE-USDT",
    "account_label": "Blofin Demo",
    "account_exchange": "blofin",
    "account_mode": "demo",
    "enabled": true,
    "stop_reason": null,
    "capital_allocation": 200,
    "total_return": 0,
    "open_positions_count": 0,
    "open_pnl": 0
  },
  {
    "id": "tv_test_harness",
    "name": "TV Test Harness (shadow)",
    "symbol": "BTC-USDT",
    "account_label": "Blofin Demo",
    "account_exchange": "blofin",
    "account_mode": "demo",
    "enabled": true,
    "stop_reason": null,
    "capital_allocation": 291.41234552,
    "total_return": -2.86,
    "open_positions_count": 0,
    "open_pnl": 0
  },
  {
    "id": "tv-btc-test-hl-94e1",
    "name": "TV BTC Test HL",
    "symbol": "BTC-USDT",
    "account_label": "Hyperliquid",
    "account_exchange": "hyperliquid",
    "account_mode": "demo",
    "enabled": true,
    "stop_reason": null,
    "capital_allocation": 66.20809,
    "total_return": -55.86,
    "open_positions_count": 0,
    "open_pnl": 0
  },
  {
    "id": "hype-test-7db4",
    "name": "HYPE Test",
    "symbol": "HYPE-USDT",
    "account_label": "Blofin Demo",
    "account_exchange": "blofin",
    "account_mode": "demo",
    "enabled": true,
    "stop_reason": null,
    "capital_allocation": 191.8626,
    "total_return": -3.67,
    "open_positions_count": 1,
    "open_pnl": 0
  },
  {
    "id": "ai-btc-6f8c",
    "name": "AI BTC",
    "symbol": "BTC-USDT",
    "account_label": "Hyperliquid",
    "account_exchange": "hyperliquid",
    "account_mode": "demo",
    "enabled": true,
    "stop_reason": null,
    "capital_allocation": 100.886054,
    "total_return": 0.89,
    "open_positions_count": 1,
    "open_pnl": 0
  }
]
```

### Supporting curl output (used to verify endpoint shapes before building)

**GET /strategies/hype-test-7db4/positions?scope=open**
```json
[{
  "id": "5862c610-54f7-455e-ad78-e73932505baa",
  "side": "long",
  "base_asset": "HYPE",
  "quote_asset": "USDT",
  "size": 3.2,
  "entry_price": 62.134,
  "mark_price": 62.134,
  "unrealized_pnl": null,
  "realized_pnl": null,
  "liquidation_price": null,
  "leverage": 10,
  "opened_at": "2026-06-23T19:42:25.344Z",
  "closed_at": null,
  "close_reason": null,
  "status": "open",
  "account_label": "Blofin Demo",
  "account_exchange": "blofin",
  "order_count": 1
}]
```

**GET /positions/5862c610-54f7-455e-ad78-e73932505baa/orders**
```json
[{
  "id": "4705b35b-bf05-4576-8e03-f3cb924970b9",
  "time": "2026-06-23T19:42:20.914Z",
  "type": "entry",
  "fill": 62.134,
  "delta": 3.21900501,
  "status": "filled",
  "key": {
    "avg_fill": 62.134,
    "realized": 0,
    "fee": 0
  }
}]
```

**GET /orders/4705b35b-bf05-4576-8e03-f3cb924970b9/detail**
```json
{
  "origin": {
    "signal_source": "tradingview",
    "raw_webhook": {
      "side": "buy", "size": "3.21900501", "price": null,
      "signal": "open_long", "leverage": 10, "sl_price": "56.5392",
      "tp_price": null, "timestamp": "2026-06-23T19:42:18Z",
      "base_asset": "HYPE", "order_type": "market", "margin_mode": null,
      "quote_asset": "USDT", "signal_source": "tradingview",
      "indicator_price": null,
      "signal_metadata": {
        "entry_ref": 62.131, "sl_source": "liquidation_safe",
        "used_size": 3.21900501, "original_size": 5,
        "sl_distance_pct": 9, "ref_price_source": "exchange_mark",
        "size_scaled_to_margin": true
      },
      "target_position": null
    }
  },
  "justification": {
    "signal_metadata": { "entry_ref": 62.131, ... },
    "indicator_price": null,
    "ai_reasoning": null,
    "ai_confidence": null
  },
  "execution": {
    "requested_price": null,
    "exchange_fee": 0,
    "exchange_order_id": "1000130850028",
    "placed_at": "2026-06-23T19:42:21.222Z",
    "filled_at": "2026-06-23T19:42:24.496Z",
    "actual_fill_price": 62.134,
    "events": []
  }
}
```

### What Phase 2A delivers

- `api.ts`: 4 new interfaces (`StrategyTreeItem`, `TreePosition`, `TreeOrder`, `OrderDetail`) + 4 typed methods (`fetchStrategyTree`, `fetchTreePositions`, `fetchPositionOrders`, `fetchOrderDetail`)
- `App.tsx`: `/tree` route + 🌳 Tree nav entry
- `src/pages/StrategyTree.tsx`: strategy L1 header rendering from live data
  - Left accent bar: blue (active) / gray (stopped)
  - Row 1: green dot (when open positions > 0) + `HeaderPill` symbol + bold name
  - Row 2: account chip (blue) + stop chip (gray, only when `!enabled`)
  - Row 3: `DataGrid` with Allocation / Total Return % / Open PnL (last only when `open_positions_count > 0`)
  - Top-right inert ⏸/▶ and ⓘ buttons
  - No write endpoints called; no browser storage used
  - All colors use design tokens (`var(--blue)`, `var(--green)`, etc.); no hardcoded hex from mockup

---

## Phase 2B — Expansion mechanics

### Deployed bundle

```
Asset hash: index-D94a2hDg.js  (was index-DzZZFKaq.js)
```

String verification in served bundle:
```
strategies/tree  → 1
Open Positions   → 1
Closed Positions → 1
Load more        → 1
full info        → 1
pointerType      → 2   (long-press handler)
```

### Real curl: GET /strategies/hype-test-7db4/positions?scope=all (truncated)

```json
[
  { "id": "5862c610-...", "side": "long", "base_asset": "HYPE", "status": "open",
    "size": 3.2, "entry_price": 62.134, "mark_price": 62.134,
    "unrealized_pnl": null, "realized_pnl": null, "liquidation_price": null,
    "leverage": 10, "opened_at": "2026-06-23T19:42:25.344Z", ... },
  { "id": "93918e49-...", "side": "long", "status": "closed",
    "realized_pnl": -3.3002, "close_reason": "flatten_on_disable",
    "closed_at": "2026-06-22T19:00:31.444Z", ... },
  ...
]
```

### Real curl: GET /positions/5862c610-.../orders

```json
[{
  "id": "4705b35b-...", "time": "2026-06-23T19:42:20.914Z",
  "type": "entry", "fill": 62.134, "delta": 3.21900501, "status": "filled",
  "key": { "avg_fill": 62.134, "realized": 0, "fee": 0 }
}]
```

### Real curl: GET /orders/4705b35b-.../detail (null AI/exec fields)

```json
{
  "origin": { "signal_source": "tradingview", "raw_webhook": {...} },
  "justification": { "indicator_price": null, "ai_reasoning": null, "ai_confidence": null },
  "execution": {
    "requested_price": null, "exchange_fee": 0,
    "exchange_order_id": "1000130850028",
    "placed_at": "2026-06-23T19:42:21.222Z",
    "filled_at": "2026-06-23T19:42:24.496Z",
    "actual_fill_price": 62.134, "events": []
  }
}
```

### What Phase 2B delivers

- **Strategy 3-state tap cycle**: collapsed → open (fetches `scope=open`) → all (fetches `scope=all`) → collapsed. Long-press (500ms, pointer events) → collapsed from any state.
- **Collapsed positions paging**: closed positions capped at 3; "Load more" adds 3 from already-fetched data. "Collapse" button goes back to collapsed state.
- **Position 3-state tap cycle**: header → details → details+orders (lazy fetch) → header.
- **Order expand**: tap header toggles key details (avg_fill/realized/fee/status). "full info" button lazy-fetches `/orders/:id/detail`, toggleable.
- **Null-safe rendering**: all `null` fields render as `—` throughout (justification.ai_reasoning, execution.requested_price, etc.).
- **Account diff chip**: shown on position only when account_label or account_exchange differs from strategy's.
- **Inert controls**: ✕ close icon (open position header) + "Close position" button (order track, open only) are present for layout fidelity but `disabled` with no write calls.

---

## Phase 2 fix-up — live PnL + stopped-bar color

### Deployed bundles

```
dashboard-api: rebuilt (layer-cached), container matp-dashboard-api-1 recreated
dashboard-ui:  asset hash: index-CsV-0-s_.js  (was index-D94a2hDg.js)
```

### Bundle string verification

```
docker compose exec -T dashboard-ui grep -c 'stopped-bar' /usr/share/nginx/html/assets/index-CsV-0-s_.js
→ 1

docker compose exec -T dashboard-ui grep -c 'toFixed(1)' /usr/share/nginx/html/assets/index-CsV-0-s_.js
→ 2   (allocation + one other use)
```

### Fix 1 — L2 live mark_price + unrealized_pnl

`GET /api/dashboard/strategies/hype-test-7db4/positions?scope=open`

```json
[{
  "id": "5862c610-54f7-455e-ad78-e73932505baa",
  "side": "long",
  "base_asset": "HYPE",
  "quote_asset": "USDT",
  "size": 3.2,
  "entry_price": 62.134,
  "mark_price": 63.1046,
  "unrealized_pnl": 3.10592,
  "realized_pnl": null,
  "liquidation_price": null,
  "leverage": 10,
  "opened_at": "2026-06-23T19:42:25.344Z",
  "closed_at": null,
  "close_reason": null,
  "status": "open",
  "account_label": "Blofin Demo",
  "account_exchange": "blofin",
  "order_count": 1
}]
```

`mark_price 63.1046 ≠ entry_price 62.134` — live executor feed confirmed.
`unrealized_pnl +3.1059` — was `null` / `0` from stale DB before.
Closed positions fall back to `mark_price = entry_price`, `unrealized_pnl = null`.

### Fix 2 — L1 tree live open_pnl (executor fanout once per request)

`GET /api/dashboard/strategies/tree` (relevant strategy excerpt):

```json
{
  "id": "hype-test-7db4",
  "name": "HYPE Test",
  "symbol": "HYPE-USDT",
  "open_positions_count": 1,
  "open_pnl": 3.096
}
```

`open_pnl 3.096` — was `0` from stale `SUM(sp.pnl_unrealized)` before.
Executor fanout: one `fetch /accounts/:id/positions` per unique active account, not per strategy.

### Fix 3 — Allocation format

`Metric` now renders `191.9` (`.toFixed(1)`) matching `pages/Strategies.tsx`, was `.toFixed(0)` → `192`.

### Fix 4 — Stopped accent bar

Added token `--stopped-bar: #cbd5e1` to `tokens.css`.
Stopped bar now uses `var(--stopped-bar)` (light cool gray, `#cbd5e1`) instead of `var(--gray)` (`#64748b` dark blue-slate).
Light neutral gray clearly reads as inactive, distinct from `--blue` (#2563eb).

### Other endpoint curls (unchanged, still working)

`GET /positions/5862c610-.../orders` → 1 entry order, `avg_fill: 62.134`

`GET /orders/4705b35b-.../detail` → origin: tradingview, execution: actual_fill_price: 62.134

---

_Phase 2C (ⓘ detail panel + final redeploy) pending human confirmation._
