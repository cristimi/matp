## [2026-06-13] - 2.8.0

### Context
A live HYPE-USDT short on Blofin was orphaned: still open on the exchange while MATP's DB marked
it closed (no user action). Root cause was the reconciler treating a transient Blofin API failure
as "position gone." Investigation surfaced three further coupled defects. All four are fixed below
and covered by new unit tests.

### Fixed
- **Transient exchange/executor errors could close live positions** (Part 1): the position read
  collapsed every failure into an empty list, so the reconciler could not distinguish "exchange
  confirmed no position" from "failed to ask." A transient Blofin outage closed a still-live short
  in the DB. The read is now three-state — confirmed list / confirmed empty / unknown — and
  `unknown` leaves the position untouched (stays `open`, rendered stale), never incrementing the
  miss counter and never closing.
- **Miss counter was a one-way ratchet** (Part 2): `reconcile_miss_count` only reset on an exact
  size match, so a position whose DB size never matched the exchange (see Part 3) accumulated
  transient failures indefinitely until it crossed the close threshold — defeating the "3
  consecutive misses" safeguard. A confirmed-present read at equal **or larger** size now resets
  the counter.
- **Blofin position size read in contracts instead of base coins** (Part 3): `get_open_positions`
  returned the raw `positions` field (contracts) while the rest of MATP uses base coins, so a
  5-coin position read as 50. This kept the reconciler's exact-match from ever firing (feeding the
  ratchet), showed inconsistent sizes in the dashboard, and made `modify-stops` place TP/SL
  triggers ~10x oversized (a double contracts conversion). The read path now converts contracts to
  base coins using each instrument's `contractValue`.
- **Closed-position PnL over-attributed** (Part 4): `get_closed_position_details` summed
  Hyperliquid `closedPnl` across a coin's entire fill history (Blofin took the latest record
  regardless of age), recording +30.01 USDT for a position actually worth +0.24 — a ~125x
  over-attribution. History lookups are now scoped to the position's lifetime via `since_ms`
  derived from `opened_at`.

### Added
- `order-executor/app/adapters/base.py`: `ExchangeUnavailableError`, raised when a position read
  cannot be confirmed (network/API error) so callers can treat it as UNKNOWN rather than empty.
- `order-listener/tests/test_reconciler.py`: 8 unit tests covering UNKNOWN-skip (no increment, no
  close), present-equal/larger reset, absent/smaller increment and threshold actions (full close /
  partial reduction), per-account isolation, and `opened_at`-scoped history. No live services or DB.

### Changed
- `order-executor/app/adapters/base.py`: `get_closed_position_details(symbol, since_ms=None)`.
- `order-executor/app/adapters/blofin.py`: `get_open_positions` raises `ExchangeUnavailableError`
  on API failure and returns base coins via new `_to_base()`; `get_closed_position_details` filters
  positions-history by `since_ms`.
- `order-executor/app/adapters/hyperliquid.py`: `get_open_positions` re-raises as
  `ExchangeUnavailableError` on failure; `get_closed_position_details` filters `userFills` by
  `since_ms` before summing `closedPnl`.
- `order-executor/app/main.py`: `GET /accounts/{id}/positions` returns HTTP 503 on failure instead
  of `[]`; `GET /accounts/{id}/positions/history` accepts a `since` (epoch ms) query param.
- `order-listener/app/executor_client.py`: `get_account_positions` returns `None` (UNKNOWN) vs a
  list (confirmed, possibly empty); `get_position_history` forwards `opened_at` as `since`.
- `order-listener/app/reconciler.py`: skips accounts whose read is UNKNOWN; resets the miss counter
  on any confirmed-present read (incl. `will not grow`); passes `opened_at` to history lookups in
  both `_handle_full_external_close` and `_recover_manual_close_pnl`.

### Commits
- Part 1 `fa712a0`, Part 2 `584a1a0`, Part 3 `8fe4d0e`, Part 4 `c306ef6`, tests `44fe289`.

## [2026-06-07] - 2.7.0

### Added
- **HL realized PnL from userFills**: after a close order fills on Hyperliquid, the adapter queries `POST /info {type: "userFills"}` for the main wallet and sums `closedPnl` for the matching `oid`. Returns `None` when the oid is not found (unknown), `Decimal('0')` when the fill generated no closed PnL (open orders).
- **`OrderResult.realized_pnl`**: new optional field on the shared result model (both executor and listener); Blofin already fetched PnL from order details — it is now propagated through to `orders.pnl` and `strategy_positions.pnl_realized`.
- **Margin computed dynamically**: positions API now returns `margin = entry_price × size / leverage` (computed at response time) instead of the always-zero DB column, for both open and closed positions.

### Fixed
- **`close_long`/`close_short` left position `open` in DB**: webhook handler found the existing position via `pair_id` which is `NULL` for symbols not in `trading_pairs`; `WHERE pair_id = NULL` is always false in SQL. Switched lookup to `WHERE symbol = $2` so the close always finds and updates the position row.
- **`close_price` column mismatch**: close handler was writing to `close_price` (non-existent); DB schema uses `closing_price`. Fixed column name in both the `close_long`/`close_short` and `target_position=flat` handlers.
- **`pnl_realized` not written on close**: close handlers now write `pnl_realized` to `strategy_positions` from `result.realized_pnl`; executor's `_update_order_record` now writes `pnl` column on the `orders` table.
- **Blofin fill price always null**: `_get_order_details` called `GET /api/v1/trade/order` which returns HTTP 200 with `code: '405'` (wrong endpoint for completed orders). Now tries `/api/v1/trade/orders-history` then `/api/v1/trade/fills-history`. Fill price field is `averagePrice` (not `avgPrice`) — `_parse_fill_price()` tries all variants.
- **HL fill price always null**: `_place_order` extracted `filled.avgPx` from the exchange response but returned it as a raw value; `Decimal` was not imported in `hyperliquid.py` causing `NameError` at runtime. Added `from decimal import Decimal`.
- **HL realized PnL never fetched from webhook closes**: `_get_fill_pnl` was gated on `reduce_only=True` which is only set by the internal `close_position()` path. Webhook-originated `close_long`/`close_short` signals go through `submit_order`, which always called `_place_order(reduce_only=False)`. Fixed by deriving `reduce_only` from `order.signal`.
- **`flat` signal left position open**: `target_position=flat` handler closed the position on the exchange but never updated `strategy_positions`; same fix applied (write `closing_price` + `pnl_realized`).

### Changed
- `order-executor/app/models.py`: `OrderResult` gains `realized_pnl: Optional[Decimal]`.
- `order-executor/app/adapters/hyperliquid.py`: `from decimal import Decimal` added; `submit_order` passes `reduce_only=is_close` based on signal; `_place_order` reads `filled.avgPx` as `actual_fill_price`; new `_get_fill_pnl(oid)` method queries `userFills`.
- `order-executor/app/adapters/blofin.py`: `_get_order_details` tries `orders-history` → `fills-history` endpoints; new `_parse_fill_price()` helper tries all field name variants; `submit_order` and `close_position` return `realized_pnl`.
- `order-executor/app/executor.py`: `_update_order_record` writes `pnl` from `result.realized_pnl`.
- `order-listener/app/models.py`: `OrderResult` gains `realized_pnl: Optional[Decimal]`.
- `order-listener/app/webhook_handler.py`: close handlers write `closing_price` + `pnl_realized`; position lookup uses `symbol` instead of `pair_id`; `_update_order_status` reads `result.realized_pnl`.
- `dashboard-api/src/routes/positions.ts`: `margin` computed as `entry_price × size / leverage`; `closing_price` field priority corrected.

## [2026-06-07] - 2.6.0

### Added
- **Structured credentials UI**: replaced raw-JSON textarea with per-field inputs for each exchange. Sensitive fields (secrets, private keys) are masked (`type="password"`); non-sensitive fields (API keys, wallet addresses) are shown in plain text.
- **API Wallet field for Hyperliquid**: `api_wallet` is now an explicit field in the credential form; the server derives the wallet from `private_key` and cross-checks it matches the supplied `api_wallet` before storing.
- **Server-side credential validation**: `POST /credentials/validate` on the executor checks credentials before storage. Hyperliquid: derives wallet from private key, verifies match. Blofin: makes a live `get_balance()` call to verify auth; catches Blofin's HTTP-200 error codes.
- **HL agent wallet support**: `main_wallet` optional credential field; HL adapter uses it as `query_address` for all state queries (positions, balance) while the API wallet is still used for signing orders.
- **HL duplicate-account check**: when saving HL credentials, dashboard-api checks existing active HL accounts via `/meta` to ensure the same API wallet isn't registered twice.
- **Account creation with credentials inline**: "Add Account" modal now includes credential fields from the start. Name is the only user-entered field; account ID is derived automatically (`{exchange}-{kebab-name}-{rand4}`). Credentials are validated and saved atomically on creation; if credential save fails the account row is rolled back.
- **Non-sensitive fields pre-filled on edit**: "Update Credentials" opens with non-sensitive fields (api_key for Blofin; api_wallet + main_wallet for HL) pre-populated from the cached meta, so only secrets need to be re-entered.

### Changed
- `order-executor/app/models.py`: `Position` gains `mark_price: Optional[Decimal]` and `unrealized_pnl: Optional[Decimal]`.
- `order-executor/app/adapters/hyperliquid.py`: `get_open_positions` returns `mark_price` (derived from `positionValue/size`) and `unrealized_pnl` (was `pnl`); `get_balance` queries both clearinghouse endpoints in parallel; `get_account_meta` returns `main_wallet` when set; all state queries use `self.query_address`.
- `order-executor/app/adapters/blofin.py`: `get_open_positions` returns `mark_price` from `markPrice`/`last`/`averagePrice`; `get_balance` checks `code` field for Blofin HTTP-200 auth errors; `get_account_meta` returns full `api_key`.
- `order-executor/app/main.py`: added `POST /credentials/validate` endpoint.
- `dashboard-api/src/routes/accounts.ts`: `POST /:id/credentials` now validates before encrypting; HL duplicate check; `api_wallet` stripped from JSON before storage; `TODO(blofin-dedup)` marker for future Blofin deduplication.
- `dashboard-ui/src/pages/Accounts.tsx`: full credential fields UX overhaul; `CRED_FIELDS` schema; `slugify`/`emptyCredFields` helpers; Add Account modal with inline credentials; `addForm.name` replaces `addForm.id + addForm.label`.
- `dashboard-ui/src/pages/Positions.tsx`: open positions use `unrealized_pnl`; label "P&L (Realized)" → "Unrealized P&L" for open positions; poll rate drops to 3 s when open positions exist.

### Fixed
- **HL positions/balance under API wallet**: orders placed by the API wallet appear under the main wallet's clearinghouse state — resolved by `query_address` using `main_wallet` for all state queries.
- **Live P&L not updating**: `pnl` field renamed to `unrealized_pnl` across adapter + UI; `mark_price` now populated from exchange data so Positions page shows real-time P&L.
- **Blofin validation false-positive**: `get_balance` previously used `raise_for_status()` which doesn't catch Blofin's HTTP-200 auth errors; now checks `code` in response JSON.

## [2026-06-07] - 2.5.0

### Fixed
- **Deleted accounts reappear on refresh**: `GET /accounts` was returning all rows including `is_active = false` ones, so soft-deleted accounts came back after a page reload. Added `WHERE is_active = true` to the list query — matches the same pattern as the `status != 'deleted'` filter on orders.
- **Hyperliquid balance always 0**: `get_balance` only queried `clearinghouseState` (perp). Testnet faucet funds and accounts in Unified Account Mode hold balances in `spotClearinghouseState` instead. Now queries both endpoints in parallel and sums the results so either account mode reports correctly.

### Changed
- `dashboard-api/src/routes/accounts.ts`: `GET /` query now filters `WHERE is_active = true`.
- `dashboard-ui/src/pages/Accounts.tsx`: removed `opacity: 0.6` and conditional border colour for inactive accounts (they are no longer returned by the API).
- `order-executor/app/adapters/hyperliquid.py`: `get_balance` queries both `clearinghouseState` and `spotClearinghouseState` via `asyncio.gather`; USDC spot balance parsed from `balances[coin=="USDC"].total − hold`.

## [2026-06-06] - 2.4.0

### Fixed
- **Blofin available balance and used margin**: `get_balance` was reading `available` from the top-level `data[0]` object; the field is actually nested inside `data[0].details[0].available`. Used margin is now correctly `totalEquity − available` instead of the full equity.
- **Cancelled orders did not visually respond**: `'cancelled'` was missing from `ChipStatus`; it fell through to the `pending` default (blue badge, Cancel button still shown). Added yellow token (`--yellow`), amber chip for `cancelled`, "✕ Delete" footer button, optimistic local-state update in `handleCancel`.
- **All bad-outcome statuses now share amber colour**: `route-fail`, `lag-fail`, `rejected`, and `cancelled` use `--failed-color` for both the left bar and the status pill. Previously `rejected` was red and `cancelled` was yellow.
- **lag-fail button label**: renamed "Delete Log" → "Delete" for consistency.
- **Account delete button missing**: "Deactivate" button (hidden for inactive accounts) replaced by a "Delete" button shown for all accounts. On success, removes the card from local state immediately.

### Changed
- `order-executor/app/adapters/blofin.py`: `get_balance` now parses `data[0].details[0]` for `available` / `availableEquity`; `totalEquity` is still read from `data[0]`.
- `dashboard-ui/src/styles/tokens.css`: added `--yellow`, `--yellow-a`, `--yellow-b` tokens (reserved for future use; cancelled chip uses `--failed-color`).

## [2026-06-06] - 2.3.0

### Fixed
- **Position side convention**: `strategy_positions.side` now stores `long`/`short` (position convention) instead of `buy`/`sell` (order convention), matching Blofin's native format. This fixed the stale-position bug where the `account_id:symbol:side` lookup key in the positions API never matched the executor's real-time data, causing every open position to show as stale.
- **Entry price populated from fill**: `OrderResult` in the executor was missing `actual_fill_price` as a Pydantic field, so the adapter's value was silently dropped. Added the field, wrote it to `orders.actual_fill_price` in `_update_order_record`, and added a UI fallback to use `realPos.entry_price` when the DB value is 0.
- **Delete order shows as pending**: `GET /orders` now excludes `status = 'deleted'` by default, so soft-deleted orders no longer reappear after deletion. `handleDelete` in the UI optimistically removes the order from local state on success instead of re-fetching (which caused a flash of the unmapped `deleted` status rendering as `pending`).

### Changed
- `order-listener/app/webhook_handler.py`: `_create_strategy_position()` converts `buy`→`long` / `sell`→`short` before writing `strategy_positions.side`.
- `order-listener/app/adapters/blofin.py`: `close_position` now accepts `long`/`short` natively; backward-compat map retained for `buy`/`sell`.
- `dashboard-api/src/routes/positions.ts`: removed buy/sell normalisation (no longer needed); entry_price falls back to `realPos.entry_price` when DB value is 0.

## [2026-06-06] - 2.2.0

### Added
- DB migration `005_signal_log.sql`: new tables `signal_log` and `order_execution_log` for full request/execution audit trail.
- `signal_log`: every inbound webhook request is recorded before any validation or processing, capturing `strategy_id`, `source_ip`, `raw_body` (JSONB), `http_status`, `outcome`, `error_detail`, and `duration_ms`.
- `order_execution_log`: one row per order execution attempt in the executor, capturing `exchange`, `exchange_order_id`, `client_order_id` (UUID), `symbol`, `side`, `order_type`, `requested_size`, `status`, and `error_message`.
- `dashboard-api`: new routes `GET /api/dashboard/signals` (paginated, filterable by `strategy_id`, `outcome`, `from`, `to`) and `GET /api/dashboard/signals/strategies` (distinct strategy IDs for filter dropdown).
- Dashboard UI: `/signals` page with outcome badges, expandable rows showing `raw_body` JSON + execution log grid, filter bar, Load More pagination, and 15 s auto-refresh.
- `OrderRequest` (internal): added `signal_log_id: Optional[int]` field; webhook handler sets this so executor can link execution log rows back to the originating signal.

### Changed
- `order-listener/app/webhook_handler.py`: `receive_webhook` now accepts a raw `Request` and parses the body manually before Pydantic validation so every outcome (including schema errors) is logged to `signal_log`.
- `order-executor/app/executor.py`: each attempt generates a fresh `client_order_id` (UUID) and writes/updates an `order_execution_log` row.

## [2026-06-06] - 2.1.0

### Added
- Multi-account support: `order-executor` now fully wired; all exchange calls route via `AccountRegistry` keyed by `strategy.account_id`.
- Executor endpoints: `POST /close-position`, `POST /credentials/encrypt`, `GET /accounts/{id}/balance`, `GET /accounts/{id}/meta`, `GET /accounts/{id}/positions`, `POST /accounts/{id}/positions/close`.
- Dashboard API accounts: `GET /accounts/:id/balance`, `GET /accounts/:id/meta`, `POST /accounts/:id/credentials` (encrypt-then-store via executor).
- Dashboard API orders: `DELETE /orders/:id` (soft-delete), `POST /orders/:id/cancel`; added `account_id` filter and `account_label` join to order list response.
- DB migration `004_strategy_config_jsonb.sql`: adds `config JSONB NOT NULL DEFAULT '{}'` to `strategies` table for per-strategy adapter settings.
- Hyperliquid adapter: market-order slippage now configurable via `strategies.config.slippage_pct` (default 1%). Replaces hardcoded 2%.
- `order-listener/app/symbol_validator.py`: isolated symbol resolution + coupling logic extracted from webhook handler.
- `executor_client.py`: `call_executor_close_position()` helper for flat-signal position closing.
- `CLAUDE.md`: project-level guidance file for AI agent sessions.

### Changed
- `OrderRequest` (internal): added `config: Optional[dict]` field; webhook handler now forwards strategy JSONB config to executor.
- Orders API: query aliases all columns as `o.*`; left-joins `exchange_accounts` to expose `account_label` in list response.
- `order-listener/app/orders_api.py`: retry logic now calls `call_executor` directly (removed dependency on deleted `router.py`).
- `.env.example`: reorganised with section headers; added `POSTGRES_USER`, `POSTGRES_DB`, `EXECUTOR_URL`, and port override examples.
- `docker-compose.yml`: removed deprecated `version` field.

### Removed
- `order-listener/app/router.py`: deleted; routing is now handled directly in `webhook_handler.py` via `executor_client`.

### Tested
- Hyperliquid market buy E2E (0.02 ETH, testnet): order filled, `exchange_order_id=54506983576`, status=`filled` in DB.

## [2026-05-25] - 2.0.10

### Added
- Hyperliquid Integration: Implemented `_fetch_asset_meta` in `order-listener/app/adapters/hyperliquid.py` to fetch and cache asset indices from Hyperliquid `/info` endpoint.

## [2026-05-25] - 2.0.9

### Added
- Dependencies: Added  (v0.13.7) to  to support ECDSA signing for upcoming Hyperliquid integration.
# Project Changelog

All notable changes to this project will be tracked in this file.

## [2026-05-28] - 2.0.11

### Added
- Integration Testing: Created `run_integration_test.py` and `test_blofin_e2e.py` for automated webhook verification.

### Tested
- Verified E2E signal flow from simulated TradingView webhooks to Blofin Demo API.
- Verified successful authentication, payload validation, and order routing.
- Verified capture of exchange error responses (e.g., "Insufficient margin") in the database.

## [2026-05-25] - 2.0.10

### Added
- Hyperliquid Integration: Implemented `_fetch_asset_meta` in `order-listener/app/adapters/hyperliquid.py` to fetch and cache asset indices from Hyperliquid `/info` endpoint.

## [2026-05-25] - 2.0.9

### Added
- Dependencies: Added `eth-account` to `order-listener/requirements.txt` to support ECDSA signing for upcoming Hyperliquid integration.

## [2026-05-25] - 2.0.8

### Added
- Strategy CRUD: Implemented POST, GET by ID, PUT, DELETE endpoints in `dashboard-api/src/routes/strategies.ts`.
- Strategy Form UI: Completed `StrategyForm.tsx` functionality with full CRUD integration.
- Hyperliquid Integration: Added `eth-account` dependency to `order-listener/requirements.txt` to prepare for ECDSA signing.

### Fixed
- Close Position: Resolved 404 on close endpoint by rebuilding UI; patched `order-listener/app/adapters/blofin.py` to fix `KeyError` on rejected orders.
- Order Retry: Implemented local state synchronization in `dashboard-ui/src/pages/Orders.tsx` to reflect retry results immediately.
- SQL Bug: Fixed missing `$1` parameter in `dashboard-api/src/routes/orders.ts` (GET order by ID).

### Tested
- Verified Strategy CRUD lifecycle (Create/Update/Delete).
- Verified 'Close Position' request routing and backend error handling.
- Verified Order Retry state sync and API response handling.

### Added
- Strategy Management: Added 'Create Strategy' page (`dashboard-ui/src/pages/StrategyForm.tsx`) with cascading type-class dropdown and YAML configuration support.
- Strategies UI: Added 'Create New Strategy' button and 'Edit' actions to `Strategies.tsx` for improved management workflows.
- Routing: Configured React Router paths for new strategy management routes.

### Changed
- UI Responsiveness: Refactored Strategies page layout for better mobile experience (responsive button placement, hidden edit column).

### Tested
- Verified navigation to/from Strategy Management UI.
- Verified mobile responsiveness of the Strategies page.
- Verified successful build of UI container and Nginx proxy communication.

## [2026-05-24] - 2.0.6

### Added
- Symbol Factory: Implemented `SymbolFactory` to normalize across `BTC/USDT`, `BTC-USDT`, and `BTCUSDT` formats.
- Realized P&L: Integrated realized P&L fetching from Blofin when closing positions.
- API Endpoint: Added `/api/v1/trade/order` for order details fetching.

### Fixed
- UI Positions: Fixed breakage by adding missing `pair` object.
- Price Rendering: UI now prioritizes `actual_fill_price` over `indicator_price`.
- P&L Rendering: UI now renders '—' instead of '0.00' for null P&L values.
- NameError: Fixed NameError in `order-listener/app/positions_api.py` during close position requests.
- Retry Logic: Fixed missing order status synchronization after retry.
- SQL Bug: Fixed mismatch between `strategy_id` and `status` columns in `orders` logging.
- Missing Import: Fixed NameError in Blofin adapter due to missing `asyncio` import.

### Tested
- Verified manual position closing and realized P&L reporting.
- Verified orders page retry logic and status sync.
- Verified UI pair object mappings and P&L null-handling.
...
