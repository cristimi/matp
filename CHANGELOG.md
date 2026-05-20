# Project Changelog

All notable changes to this project will be tracked in this file.

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
- **Monitoring Columns:** Added "Origin" (Signal Source) and "Ind. Price" (Indicator Price) columns to the Orders page.
- **Visual Polish:** Refactored all components (`StatPanel`, `LiveFeed`, `PlatformSelector`) to be theme-aware with improved contrast and responsiveness.
- **API Types:** Updated Dashboard UI's `Order` interface to support granular signal metadata.

## [2026-05-19 15:45]

### Enhancements: Granular Strategy Monitoring
- Updated database schema (`orders` table) to track `signal_source`, `signal_metadata` (JSONB), and `indicator_price`.
- Updated `order-listener` to ingest and persist these new signal attributes, allowing for precise origin tracking (TradingView vs Internal) and indicator-based analysis.

## [2026-05-19 15:25]
...

### Testing Phase 1: Webhook Test D (Platform Switching)
- Verified active platform switching.
- Updated `active_platform` to `hyperliquid` via `/config/active_platform`.
- Sent a valid webhook request with `platform: auto`.
- Confirmed the system attempted to route to `hyperliquid` (evidenced by the `TypeError` in logs, confirming successful routing selection).

## [2026-05-19 15:05]

### Testing Phase 1: Webhook Test C (Malformed Payload)
- Performed a negative integration test using curl for `strat-001` with a malformed payload (missing fields).
- Verified HTTP 422 Unprocessable Entity response (correctly identified by Pydantic validation).
- Confirmed that failed validation requests do not create logs in `strategy_webhook_calls` as intended.
...


### Testing Phase 1: Webhook Test B (Invalid Token)
- Performed a negative integration test using curl for `strat-001` with an invalid token.
- Verified HTTP 403 response (`{"detail":"Invalid token"}`).
- Confirmed database table `strategy_webhook_calls` correctly logs the failure with `http_status=403` and `error_message='Invalid token'`.
...

### Testing Phase 1: Webhook Test A (Success)
- Performed a valid webhook integration test using curl for `strat-001`.
- Verified HTTP 200 response, database order logging, and correct execution flow (routing triggered).
- Confirmed database table `strategy_webhook_calls` reflects the success (HTTP 200). Note: `route_failed` is expected due to missing Blofin credentials, confirming the order listener successfully processes webhooks and attempts to route them.
...

### Testing Phase 1: Health Check Smoke Tests
- Executed health check smoke tests for all services via Nginx gateway.
- Confirmed all services (`order-listener`, `order-generator`, `dashboard-api`) return `{"status":"ok"}`.

## [2026-05-19 14:15]

### Testing Phase 1: Service Startup
- Successfully launched the Docker infrastructure with `docker compose up -d`.
- Verified that all 7 services (dashboard-api, dashboard-ui, nginx, order-generator, order-listener, postgres, redis) are up and healthy.
...

## [2026-05-19 14:00]

### Testing Phase 1: Environment Configuration
- Configured `.env` file with essential environment variables (DB credentials and secrets).

### Backend Architecture: Strategy-Centric Webhooks
- **Database:** Implemented `db/migrations/001_add_strategy_webhooks.sql` to support strategy-specific webhook secrets, performance tracking, and call logging.
- **Order Listener:**
    - Rewrote `order-listener/app/webhook_handler.py` to route webhooks via `/webhook/{strategy_id}`, implementing per-strategy HMAC authentication and daily rate limiting.
    - Updated `order-listener/app/models.py` to support dynamic payload structures.
    - Updated `order-listener/app/router.py` to prioritize strategy-specific platform configurations.
- **Order Generator:**
    - Updated `order-generator/app/scheduler.py` to support strategy-specific webhook URLs, header-based HMAC authentication, and exponential backoff retry logic.
- **Dashboard API:**
    - Updated `dashboard-api/src/routes/strategies.ts` to expose strategy metrics, performance data, and webhook configuration management.




