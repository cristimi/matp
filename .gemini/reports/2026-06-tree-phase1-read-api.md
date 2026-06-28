# Strategy Tree — Phase 1 Read API

Date: 2026-06-28

## Phase 1A — Queries proven against live DB

### L1 — `GET /strategies/tree`

```
id                   | name                     | symbol    | account_label | account_exchange | account_mode | enabled | stop_reason | capital_allocation                | total_return | open_positions_count | open_pnl
---------------------+--------------------------+-----------+---------------+------------------+--------------+---------+-------------+-----------------------------------+--------------+----------------------+----------
hype-breakout-da2e   | HYPE Breakout            | HYPE-USDT | Blofin Demo   | blofin           | demo         | t       |             | 200                               | 0            | 0                    | 0
tv_test_harness      | TV Test Harness (shadow) | BTC-USDT  | Blofin Demo   | blofin           | demo         | t       |             | 291.41234552...                   | -2.86        | 0                    | 0
tv-btc-test-hl-94e1  | TV BTC Test HL           | BTC-USDT  | Hyperliquid   | hyperliquid      | demo         | t       |             | 66.20809...                       | -55.86       | 0                    | 0
```
`stop_reason` = NULL (column does not exist yet, always null). `total_return` = realized-only %. `open_pnl` separate.

### L2 — `GET /strategies/:id/positions` — account derivation from opening order

All historical positions have `orders.account_id = NULL` (not populated in early orders). The COALESCE fallback chain `o_open.account_id → oel.account_id → s.account_id` resolves correctly via the exec log:

```
id (position)                        | order_account | oel_account                  | strategy_account
-------------------------------------+---------------+------------------------------+------------------------------
0793a730 (tv-btc-test-hl-94e1 long)  | (null)        | hyperliquid-hyperliquid-hqdy | hyperliquid-hyperliquid-hqdy
```

`base_asset`/`quote_asset`: from `trading_pairs`/`assets` join; SPLIT_PART fallback for NULL pair_id (confirmed for open HYPE-USDT position → HYPE / USDT).

### L3 — Position `7c737988-7f74-41cd-a1ab-21aee46d499d` (ai-btc-6f8c BTC long with 2 partial closes)

```
id (order)           | time                  | type          | fill       | delta      | status | key.avg_fill | key.realized | key.fee
---------------------+-----------------------+---------------+------------+------------+--------+--------------+--------------+--------
ddca79f1             | 2026-06-26 23:08:05   | entry         | 59700.0    | 0.00167521 | filled | 59700.0      | null         | 0
c28a9c90             | 2026-06-27 03:25:26   | partial-close | 59838.0    | 0.00084    | filled | 59838.0      | 0.11592      | null
c5e10ffc             | 2026-06-27 03:46:32   | partial-close | 59816.04   | 0          | filled | 59816.04     | 0.19495      | null
```

Entry order has OEL join (fee=0 from oel.exchange_fee). Close orders have no OEL row → fee=null (graceful).

### L4 — Graceful null case: partial-close order `c28a9c90`

OEL join fails (no matching exchange_order_id in order_execution_log for this close order):
```
signal_source | signal_metadata_peek                              | requested_price | exchange_fee | placed_at | filled_at | ai_reasoning | ai_confidence
--------------+---------------------------------------------------+-----------------+--------------+-----------+-----------+--------------+--------------
ai_engine     | {"dry_run": false, "reasoning": "The original ... |                 |              |           |           |              |
```
All execution/AI fields null — graceful, order not dropped. ✓

Entry order `ddca79f1` resolves fully:
- OEL join: placed_at / filled_at / exchange_fee populated
- signal_log join: ai_reasoning (full text), ai_confidence = 0.780 ✓

---

## Phase 1B — Endpoints wired and curl-verified

### Routes added

- `GET /strategies/tree` — placed before `/:id` in strategies.ts ✓
- `GET /strategies/:id/positions?scope=open|all` — replaced old inner-join-only version ✓
- `GET /positions/:id/orders` — new route in positions.ts ✓
- `GET /orders/:id/detail` — new two-segment route in orders.ts ✓

### L1 curl — `/api/dashboard/strategies/tree`

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
  }
]
```

### L2 curl — `/api/dashboard/strategies/tv-btc-test-hl-94e1/positions?scope=all&limit=2`

Lazy payload confirmed: keys = [id, side, base_asset, quote_asset, size, entry_price, mark_price, unrealized_pnl, realized_pnl, liquidation_price, leverage, opened_at, closed_at, close_reason, status, account_label, account_exchange, order_count]
`raw_webhook` present: False — `orders` present: False — `signal_metadata` present: False ✓

```json
[
  {
    "id": "0793a730-cb54-4386-8d09-ab7b6ae0203d",
    "side": "long",
    "base_asset": "BTC",
    "quote_asset": "USDT",
    "size": 0.02,
    "entry_price": 59819.9,
    "mark_price": 59819.9,
    "unrealized_pnl": null,
    "realized_pnl": -19.53298,
    "liquidation_price": null,
    "leverage": 40,
    "opened_at": "2026-06-27T06:25:02.378Z",
    "closed_at": "2026-06-28T17:38:31.627Z",
    "close_reason": "Closed on exchange",
    "status": "closed",
    "account_label": "Hyperliquid",
    "account_exchange": "hyperliquid",
    "order_count": 2
  }
]
```

### L3 curl — `/api/dashboard/positions/7c737988-7f74-41cd-a1ab-21aee46d499d/orders`

```json
[
  {
    "id": "ddca79f1-6658-4c0d-a740-b2fadfa37f3e",
    "time": "2026-06-26T23:08:05.297Z",
    "type": "entry",
    "fill": 59700,
    "delta": 0.00167521,
    "status": "filled",
    "key": { "avg_fill": 59700, "realized": null, "fee": 0 }
  },
  {
    "id": "c28a9c90-48f3-4db4-af0d-924759329edb",
    "time": "2026-06-27T03:25:26.656Z",
    "type": "partial-close",
    "fill": 59838,
    "delta": 0.00084,
    "status": "filled",
    "key": { "avg_fill": 59838, "realized": 0.11592, "fee": null }
  },
  {
    "id": "c5e10ffc-5a7b-44da-99fb-9637db05b7d5",
    "time": "2026-06-27T03:46:32.386Z",
    "type": "partial-close",
    "fill": 59816.041667,
    "delta": 0,
    "status": "filled",
    "key": { "avg_fill": 59816.041667, "realized": 0.19495, "fee": null }
  }
]
```

### L4 curl — `/api/dashboard/orders/ddca79f1.../detail` (entry — OEL + AI join resolves)

```json
{
  "origin": { "signal_source": "ai_engine", "raw_webhook": { ... } },
  "justification": {
    "signal_metadata": { "reasoning": "Price is significantly deviated...", "confidence": 0.78, ... },
    "indicator_price": null,
    "ai_reasoning": "Price is significantly deviated, trading -14.56% below VWAP...",
    "ai_confidence": 0.78
  },
  "execution": {
    "requested_price": null,
    "exchange_fee": 0,
    "exchange_order_id": "55643543923",
    "placed_at": "2026-06-26T23:08:05.613Z",
    "filled_at": "2026-06-26T23:08:08.748Z",
    "actual_fill_price": 59700,
    "events": []
  }
}
```

### L4 curl — `/api/dashboard/orders/c28a9c90.../detail` (partial-close — graceful nulls)

```json
{
  "origin": { "signal_source": "ai_engine", "raw_webhook": { ... } },
  "justification": {
    "signal_metadata": { "reasoning": "The original mean-reversion thesis has partially played out...", ... },
    "indicator_price": null,
    "ai_reasoning": null,
    "ai_confidence": null
  },
  "execution": {
    "requested_price": null,
    "exchange_fee": null,
    "exchange_order_id": null,
    "placed_at": null,
    "filled_at": null,
    "actual_fill_price": 59838,
    "events": []
  }
}
```

OEL and AI join null for partial-close as expected. ✓

---

---

## Phase 1C — Cleanups

### C1 — `close_price` typo in positions.ts

Removed bogus `dbPos.close_price` fallback (column never existed in DB):
```
- close_price: Number(dbPos.closing_price || dbPos.close_price || 0),
+ close_price: Number(dbPos.closing_price || 0),
```

### C2 — Drop `strategy_positions.current_price`

**Zero readers grep (all live services, excl tester/tester-ui/migrations):**
```
grep -rn "current_price" dashboard-api/src/ dashboard-ui/src/ order-listener/ order-executor/ order-generator/
(none)
```

Files updated before migration:
- `dashboard-api/src/routes/positions.ts`: `dbPos.current_price` → `dbPos.entry_price` for mark_price fallback
- `dashboard-api/src/routes/strategies.ts` `/:id/positions`: already fixed in Phase 1B (uses `entry_price` as mark_price)
- `order-listener/app/webhook_handler.py`: removed `current_price` from INSERT column list (was a writer, not reader)

**Migration 031 applied:**
```
BEGIN
ALTER TABLE
COMMIT
NOTICE:  Migration 031 verified OK
DO
```

**`\d public.strategy_positions` after migration — `current_price` absent:**
```
         Column          |           Type           | Nullable | Default
-------------------------+--------------------------+----------+---------------------------
 id                      | uuid                     | not null | gen_random_uuid()
 strategy_id             | character varying(100)   | not null |
 exchange                | character varying(20)    | not null |
 symbol                  | character varying(50)    | not null |
 side                    | character varying(10)    | not null |
 entry_price             | numeric                  | not null |
 size                    | numeric                  | not null |
 leverage                | integer                  |          |
 margin_mode             | character varying(20)    |          |
 pnl_unrealized          | numeric                  |          |
 pnl_realized            | numeric                  |          |
 status                  | character varying(20)    |          | 'open'
 opening_order_id        | uuid                     |          |
 closing_order_id        | uuid                     |          |
 opened_at               | timestamp with time zone | not null | now()
 closed_at               | timestamp with time zone |          |
 ...
 closing_price           | numeric                  |          |
 liquidation_price       | numeric                  |          |
 ...
(current_price NOT present) ✓
```

Both dashboard-api and order-listener redeployed healthy after the migration.

### C3 — Strip stale `win_count`/`loss_count` from Strategy interface

Removed from `dashboard-ui/src/api.ts` Strategy interface (Stats interface untouched):
```typescript
- win_count: number;
- loss_count: number;
```

dashboard-ui rebuilt and redeployed. Live asset: `index-Co-kfOzQ.js`.
