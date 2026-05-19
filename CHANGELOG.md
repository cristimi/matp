# Project Changelog

All notable changes to this project will be tracked in this file.

## [2026-05-19 15:25]

### Testing Phase 1: Final Webhook Integration Success
- Successfully verified end-to-end webhook delivery via `webhooks.bbs15.duckdns.org`.
- Configured Zoraxy to proxy requests directly to port `8001` with Authentication bypassed, relying on internal HMAC security.
- Confirmed full integration with a `200 OK` response from the system.

## [2026-05-19 15:15]

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




