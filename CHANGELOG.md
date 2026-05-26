## [2026-05-25] - 2.0.10

### Added
- Hyperliquid Integration: Implemented `_fetch_asset_meta` in `order-listener/app/adapters/hyperliquid.py` to fetch and cache asset indices from Hyperliquid `/info` endpoint.

## [2026-05-25] - 2.0.9

### Added
- Dependencies: Added  (v0.13.7) to  to support ECDSA signing for upcoming Hyperliquid integration.
# Project Changelog

All notable changes to this project will be tracked in this file.

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
