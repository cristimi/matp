# Project Changelog

All notable changes to this project will be tracked in this file.

## [2026-05-23] - 2.0.5

### Added
- Precise P&L Tracking: Added `pnl` and `actual_fill_price` tracking for orders in the database.
- Enhanced API responses: Updated `orders` endpoint to include `indicator_price` and `actual_fill_price`.

### Changed
- Webhook Handler: Standardized incoming signal fields and removed reliance on `symbol`, transitioning entirely to `pair_label` derived from `base_asset` and `quote_asset`.
- Blofin Adapter: Updated order placement logic to use `pair_label` for instId and mapping.
- Routing Engine: Updated `router.py` to use `pair_label` instead of `symbol` to avoid `AttributeError` on incoming webhooks.

### Fixed
- Price/PnL Display: Resolved issues where price and P&L were not appearing in the orders list due to column selection and mapping issues.
- Webhook Reception: Fixed routing issues caused by trailing slashes in Nginx configuration.
- Order Listener Stability: Fixed `NotNullViolationError` when inserting into `strategy_positions`.
- UI Crash: Resolved an Orders page crash by properly mapping database `symbol` to the UI's expected `pair` object.

### Tested
- Verified full end-to-end webhook processing: TradingView signal -> Listener -> Blofin Execution -> DB Update -> Dashboard API -> UI display.
- Verified successful processing of new signal format without `symbol`.

## [2026-05-23] - 2.0.4

### Added
- Multi-Platform Tracking: The Positions API now aggregates live data from all exchanges where positions are held, ensuring accurate P&L updates regardless of the active platform setting.
- Real-time P&L Display: Added support for displaying both Unrealized and Realized P&L in the Positions UI.
- Position Deduplication: Implemented logic to merge live exchange positions with database records, automatically flagging stale or duplicate records.

### Changed
- Positions UI: Refactored the P&L column to display 'Unrealized / Realized' for open positions.
- API: Improved robust symbol normalization for better cross-exchange data merging.
- Real-time Feed: Enabled Redis Pub/Sub events for order lifecycle updates, fixing the live feed visibility.
- Platform Resolution: Moved 'auto' platform resolution to the initial webhook handler to enforce database integrity.

### Fixed
- Positions: Fixed '0.00' P&L values by ensuring numeric fields are consistently returned as strings from adapters.
- Close Position: Fixed a bug where positions were incorrectly attempted to be closed on the active platform instead of the exchange where they were opened.
- Real-time Feed: Fixed missing WebSocket updates by enabling Redis event publishing on order status transitions.
- WebSocket Proxying: Corrected Nginx configuration for WebSocket path forwarding.

### Tested
- Verified multi-platform position tracking with live Blofin positions.
- Verified real-time order feed via WebSocket integration tests.
- Verified platform resolution for new webhook signals.




## [2026-05-20 14:25]

### Live Position Tracking
- **Blofin Position Mapping:** Implemented standard mapping for Blofin positions, enabling correct display of Symbol, Side, Size, Entry, and P&L in the Dashboard.
- **Hyperliquid Position Mapping:** Added initial implementation for fetching and mapping Hyperliquid positions.
- **UI Consistency:** Verified data flow from exchange adapters through `order-listener` and `dashboard-api` to the React frontend.

## [2026-05-20 14:15]

### Blofin Adapter & TradingView Integration
- **Fixed Blofin Authentication:** Implemented the exact Hex-to-Base64 HMAC-SHA256 signature method required by Blofin.
- **Improved Position Closing:** Implemented `close_position` using the dedicated `/api/v1/trade/close-position` endpoint for reliable market exits.
- **Enhanced Documentation:** Updated `docs/tradingview.md` with strategy-specific webhook URLs, `signalToken` authentication, and indicator metadata support.
- **End-to-End Verification:** Successfully tested the full signal lifecycle (TradingView Webhook -> MATP Router -> Blofin Execution) on a live demo account.

## [2026-05-20 12:55]
...
### Enhancements: Theme Support & Monitoring UI
- **Light Theme:** Implemented a full light theme and a theme switcher (sun/moon toggle) in the Dashboard. Preference is persisted to local storage.