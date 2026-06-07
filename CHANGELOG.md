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
