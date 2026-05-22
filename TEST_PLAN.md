# MATP — Test Plan
**Version:** 2.0  **Date:** 2026-05-22

## How to Use This Document
This document provides a comprehensive checklist for verifying the functionality of the MATP system. These tests should be run after specific tasks are completed as outlined in `ACTION_PLAN.md`. They are categorized to guide execution based on the required interaction method.

## Test Categories
- **🖥️ Terminal Tests:** These tests require running specific commands in a terminal. They are designed for the project owner to execute with exact copy-paste commands, requiring no coding knowledge.
- **🌐 Browser / UI Tests:** These tests are performed entirely within a web browser, focusing on visual correctness, user interaction, and data integrity from a user interface perspective. No terminal interaction is needed.
- **🤖 AI-Verified Tests:** These tests are automatically executed by an AI agent during the implementation phases (e.g., compilation checks, linting, unit tests). They do not require manual intervention.

---

## 🖥️ Terminal Tests
> Run these with copy-paste commands in a terminal. No code knowledge needed.

### P2-Dashboard
| Test ID | What it verifies | Exact command(s) | Expected output |
|---------|------------------|------------------|-----------------|
| TT-T01.1 | Stats API `total_orders` count | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT COUNT(*) FROM orders;"` | A numeric count that matches `total_orders` in `/api/dashboard/stats` JSON. |
| TT-T01.2 | Stats API `filled` count | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT COUNT(*) FROM orders WHERE status = 'filled';" ` | A numeric count that matches `filled` in `/api/dashboard/stats` JSON. |
| TT-T01.3 | Stats API `failed` count | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT COUNT(*) FROM orders WHERE status = 'route_failed' OR status = 'rejected';" ` | A numeric count that matches `failed` in `/api/dashboard/stats` JSON. |
| TT-T03.1 | Dashboard stats match DB `total_orders` | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT COUNT(*) FROM orders;"` <br> (Compare with Dashboard UI stat card) | Count in terminal output matches "Total Orders" card in Dashboard. |
| TT-T03.2 | Dashboard stats match DB `filled` orders | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT COUNT(*) FROM orders WHERE status = 'filled';" ` <br> (Compare with Dashboard UI stat card) | Count in terminal output matches "Filled Orders" card (or similar) in Dashboard. |
| TT-T07.1 | Active platform persisted in DB | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT value FROM config WHERE key = 'active_platform';"` | Output should be `blofin` or `hyperliquid` (matching what was set in UI). |
| TT-T28.1 | Hyperliquid order in MATP DB | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT symbol, exchange_order_id, status FROM orders WHERE platform = 'hyperliquid' ORDER BY received_at DESC LIMIT 1;"` | Should show the symbol, a non-null `exchange_order_id`, and `filled` status if successful. |
| TT-T37.1 | Verify HTTPS Nginx config | `docker compose exec -T nginx cat /etc/nginx/conf.d/default.conf` | Output should contain `listen 443 ssl;` and `ssl_certificate` directives. |
| TT-T38.1 | Verify Nginx Basic Auth config | `docker compose exec -T nginx cat /etc/nginx/conf.d/default.conf` | Output should contain `auth_basic` and `auth_basic_user_file` directives for the dashboard location. |
| TT-T40.1 | Verify RSI strategy loaded in order-generator | `docker compose logs order-generator | grep "RSI strategy"` | Logs should indicate "RSI strategy" being loaded or scheduled. |
| TT-T41.1 | Verify CCXT OHLCV fetch | `docker compose logs order-generator | grep "Fetching OHLCV"` | Logs should show regular "Fetching OHLCV" messages for the strategy's symbol and interval. |
| TT-T42.1 | Verify strategy signal in DB | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT symbol, strategy_id, status FROM orders ORDER BY received_at DESC LIMIT 1;"` | Should show the symbol, `strategy_id` (e.g., `rsi-btc-5m`), and `filled` status. |
| TT-T43.1 | Verify strategy scheduler logs (disabled) | `docker compose logs order-generator | grep "Removing job for strategy"` | Should show a log message indicating the strategy's scheduler job was removed. |
| TT-T43.2 | Verify strategy scheduler logs (enabled) | `docker compose logs order-generator | grep "Adding job for strategy"` | Should show a log message indicating the strategy's scheduler job was added. |
| TT-T44.1 | Verify MA strategy loaded in order-generator | `docker compose logs order-generator | grep "MA strategy"` | Logs should indicate "MA strategy" being loaded or scheduled. |
| TT-T49.1 | Verify oversized order in dead letter | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT reason FROM dead_letter_orders ORDER BY failed_at DESC LIMIT 1;"` | Reason should indicate "Max order size exceeded". |

### P3-Hyperliquid
| Test ID | What it verifies | Exact command(s) | Expected output |
|---------|------------------|------------------|-----------------|
| TT-T22.1 | `eth-account` installed in listener | `docker compose exec -T order-listener pip show eth-account` | Output showing package details, including `Version: X.Y.Z`. |
| TT-T28.1 | Hyperliquid order in MATP DB | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT symbol, exchange_order_id, status FROM orders WHERE platform = 'hyperliquid' ORDER BY received_at DESC LIMIT 1;"` | Should show the symbol, a non-null `exchange_order_id`, and `filled` status if successful. |

### P4-Hardening
| Test ID | What it verifies | Exact command(s) | Expected output |
|---------|------------------|------------------|-----------------|
| TT-T36.1 | Blofin error message captured | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT error_msg FROM orders WHERE status = 'route_failed' AND platform = 'blofin' ORDER BY received_at DESC LIMIT 1;"` | Output should contain the specific error message from Blofin (e.g., "Insufficient Margin"). |
| TT-T37.1 | Nginx HTTPS config | `docker compose exec -T nginx cat /etc/nginx/conf.d/default.conf` | Output should contain `listen 443 ssl;` and `ssl_certificate` directives. |
| TT-T38.1 | Nginx Basic Auth config | `docker compose exec -T nginx cat /etc/nginx/conf.d/default.conf` | Output should contain `auth_basic` and `auth_basic_user_file` directives for the dashboard location. |

### P5-StrategyEngine
| Test ID | What it verifies | Exact command(s) | Expected output |
|---------|------------------|------------------|-----------------|
| TT-T41.1 | CCXT OHLCV fetch logs | `docker compose logs order-generator | grep "Fetching OHLCV"` | Logs should show regular "Fetching OHLCV" messages for the strategy's symbol and interval. |
| TT-T42.1 | Strategy signal in DB | `docker compose exec -T postgres psql -U matp -d matp -c "SELECT symbol, strategy_id, status FROM orders ORDER BY received_at DESC LIMIT 1;"` | Should show the symbol, `strategy_id` (e.g., `rsi-btc-5m`), and `filled` status. |
| TT-T43.1 | Strategy scheduler job removal log | `docker compose logs order-generator | grep "Removing job for strategy"` | Should show a log message indicating the strategy's scheduler job was removed. |
| TT-T43.2 | Strategy scheduler job addition log | `docker compose logs order-generator | grep "Adding job for strategy"` | Should show a log message indicating the strategy's scheduler job was added. |

---

## 🌐 Browser / UI Tests
> Run these from a web browser only. No terminal needed.

### P2-Dashboard
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T05.1 | Orders page filters by Symbol | 1. Open `http://localhost` in browser. <br> 2. Click "Orders" in the left sidebar. <br> 3. Use the "Symbol" filter dropdown and select a specific symbol (e.g., "BTC-USDT"). | The orders table/list updates to show only orders for the selected symbol. The count displayed (if any) should reflect the filtered results. |
| UT-T05.2 | Orders page filters by Platform | 1. Open `http://localhost` in browser. <br> 2. Click "Orders" in the left sidebar. <br> 3. Use the "Platform" filter dropdown and select a platform (e.g., "blofin"). | The orders table/list updates to show only orders for the selected platform. |
| UT-T05.3 | Orders page filters by Status | 1. Open `http://localhost` in browser. <br> 2. Click "Orders" in the left sidebar. <br> 3. Use the "Status" filter dropdown and select a status (e.g., "filled", "route_failed"). | The orders table/list updates to show only orders with the selected status. |
| UT-T06.1 | Live Feed WebSocket updates | 1. Open `http://localhost` in browser. <br> 2. Navigate to the "Dashboard" page. <br> 3. Trigger a test webhook (e.g., using `curl` as per `ACTION_PLAN.md` T28 details, or by enabling a strategy that generates a signal). | New order events appear in the "Live Feed" panel within a few seconds, with a green connection indicator. No page refresh is needed. |
| UT-T07.1 | Active platform switcher | 1. Open `http://localhost` in browser. <br> 2. Click "Settings" in the left sidebar. <br> 3. Use the "Active Trading Platform" selector to choose a platform (e.g., "hyperliquid"). <br> 4. Click "Save". | A success message or visual indicator confirms the change. Re-navigating to settings shows the newly selected platform is still active. |
| UT-T08.1 | Mobile layout (Dashboard) | 1. Open `http://localhost` in browser. <br> 2. Use browser developer tools to simulate a mobile device (e.g., iPhone SE, 375px width). <br> 3. Navigate to the "Dashboard" page. | Stat cards stack vertically, chart resizes appropriately, and the bottom navigation bar is visible and functional. |
| UT-T08.2 | Mobile layout (Orders) | 1. Open `http://localhost` in browser. <br> 2. Use browser developer tools to simulate a mobile device (e.g., iPhone SE, 375px width). <br> 3. Navigate to the "Orders" page. | The orders table transforms into a card-based layout, and filters are accessible and usable. |

### P2-StrategyUI
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T20.1 | Strategy Dashboard data | 1. Open `http://localhost` in browser. <br> 2. Click "Strategies" in the navigation. <br> 3. Verify the main strategy dashboard loads with a list of strategies and their summary performance metrics. | Data for each strategy (e.g., total trades, P&L) is displayed, and the page appears well-structured. |
| UT-T20.2 | Strategy Detail data | 1. Open `http://localhost` in browser. <br> 2. Click "Strategies" in the navigation. <br> 3. Click on a specific strategy from the list to navigate to its detail page. <br> 4. Verify that the strategy detail page loads with specific stats, open positions, and any configured parameters for that strategy. | All sections of the strategy detail page (stats, positions, config) are populated with data relevant to the selected strategy. |
| UT-T21.1 | Equity curve renders correctly | 1. Open `http://localhost` in browser. <br> 2. Navigate to a strategy detail page (UT-T20.2). <br> 3. Locate the equity curve chart. | The chart is visible, displays data points, has clear axis labels, and represents the strategy's cumulative P&L over time. |

### P3-Hyperliquid
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T29.1 | Platform switcher functionality | 1. Open `http://localhost` in browser. <br> 2. Click "Settings". <br> 3. Change "Active Trading Platform" to "blofin" and click "Save". <br> 4. Send a test webhook with `platform: "auto"`. <br> 5. Verify the order appears on Blofin exchange. <br> 6. Repeat steps 3-5, switching to "hyperliquid" and verifying the order appears on Hyperliquid exchange. | Orders consistently route to the platform selected in the UI. |

### P4-Hardening
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T33.1 | Manual Close Position E2E | 1. Ensure you have an open position on an exchange (e.g., created via a test webhook). <br> 2. Open `http://localhost` in browser. <br> 3. Navigate to the "Positions" page. <br> 4. Locate the open position and click the "Close" button associated with it. | A confirmation or success message is displayed. The position is removed from the "Positions" page (or its status changes). The position is closed on the actual exchange. |
| UT-T34.1 | Dead Letter Retry UI E2E | 1. Deliberately cause an order to fail (e.g., temporarily set an invalid API key for Blofin, then send a webhook with `platform: "blofin"`). <br> 2. Open `http://localhost` in browser. <br> 3. Navigate to the "Orders" page. <br> 4. Locate the `route_failed` order. <br> 5. Correct the cause of failure (e.g., restore valid API key). <br> 6. Click the "Retry" button on the failed order. | The order's status changes from `route_failed`. If successful, it eventually changes to `filled` and appears on the exchange. |
| UT-T39.1 | WebSocket reconnection feedback | 1. Open `http://localhost` in browser and navigate to the Dashboard. <br> 2. Observe the Live Feed panel's connection status indicator. <br> 3. Simulate a network interruption or restart the `order-listener` container (`docker compose restart order-listener`). | The Live Feed indicator temporarily shows a "connecting..." or "disconnected" state, then automatically returns to a connected state (green dot) without requiring a manual page reload. |

### P5-StrategyEngine
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T42.1 | Strategy signal to exchange E2E | 1. Ensure an automated strategy (e.g., RSI) is enabled and running (via T40, T41). <br> 2. Monitor the Dashboard "Live Feed" and "Orders" pages. <br> 3. Wait for the strategy to generate a signal. | The strategy signal appears in the Live Feed. A new order is visible on the "Orders" page with the correct `strategy_id` and eventually `filled` status. The order appears on the configured exchange. |
| UT-T43.1 | Strategy enable/disable toggle | 1. Open `http://localhost` in browser. <br> 2. Navigate to the "Strategies" page (after T15 is complete). <br> 3. Locate an active strategy and toggle it to "disabled". <br> 4. Observe `order-generator` logs (T43.1). <br> 5. Toggle the strategy back to "enabled". <br> 6. Observe `order-generator` logs (T43.2). | The toggle visually updates. Logs confirm the scheduler job was removed. Logs confirm the scheduler job was re-added. Signals stop/resume from the strategy. |

### P5-RiskMgmt
| Test ID | What it verifies | Step-by-step instructions | What "pass" looks like |
|---------|------------------|--------------------------|-------------------------|
| UT-T49.1 | Oversized order rejection | 1. Construct a `curl` command for a webhook with a `size` value clearly exceeding the `MAX_ORDER_SIZE` limit configured in T31. <br> 2. Send the `curl` command. <br> 3. Open `http://localhost` in browser and navigate to the "Orders" page. | The `curl` command should return a 400 error. The order should appear on the "Orders" page with a `route_failed` or `rejected` status and a clear error message indicating an oversized order. |

---

## 🤖 AI-Verified Tests
> These are run by the AI agent as part of implementation. You don't need to run them manually.

- **TS-Linting & Type-checking (Python):** Covers `order-listener` and `order-generator` for Ruff linting and type checks.
- **TS-Linting & Type-checking (TypeScript):** Covers `dashboard-api` and `dashboard-ui` for TypeScript compilation (`tsc`) and ESLint checks.
- **TS-Docker Builds:** Ensures all Docker images (`order-listener`, `order-generator`, `dashboard-api`, `dashboard-ui`) build successfully.
- **TS-Pytest Suite (order-listener):** Verifies unit and integration tests written for `order-listener` pass. (Relevant to T35)

---

## Phase Test Checklists

### P2-Dashboard
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| TT-T01.1 | Stats API `total_orders` count | Terminal | |
| TT-T01.2 | Stats API `filled` count | Terminal | |
| TT-T01.3 | Stats API `failed` count | Terminal | |
| TT-T03.1 | Dashboard stats match DB `total_orders` | Terminal | |
| TT-T03.2 | Dashboard stats match DB `filled` orders | Terminal | |
| UT-T05.1 | Orders page filters by Symbol | Browser / UI | |
| UT-T05.2 | Orders page filters by Platform | Browser / UI | |
| UT-T05.3 | Orders page filters by Status | Browser / UI | |
| UT-T06.1 | Live Feed WebSocket updates | Browser / UI | |
| UT-T07.1 | Active platform switcher | Browser / UI | |
| UT-T08.1 | Mobile layout (Dashboard) | Browser / UI | |
| UT-T08.2 | Mobile layout (Orders) | Browser / UI | |

### P2-StrategyUI
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| UT-T20.1 | Strategy Dashboard data | Browser / UI | |
| UT-T20.2 | Strategy Detail data | Browser / UI | |
| UT-T21.1 | Equity curve renders correctly | Browser / UI | |

### P3-Hyperliquid
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| TT-T22.1 | `eth-account` installed in listener | Terminal | |
| TT-T28.1 | Hyperliquid order in MATP DB | Terminal | |
| UT-T29.1 | Platform switcher functionality | Browser / UI | |

### P4-Hardening
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| TT-T36.1 | Blofin error message captured | Terminal | |
| TT-T37.1 | Nginx HTTPS config | Terminal | |
| TT-T38.1 | Nginx Basic Auth config | Terminal | |
| UT-T33.1 | Manual Close Position E2E | Browser / UI | |
| UT-T34.1 | Dead Letter Retry UI E2E | Browser / UI | |
| UT-T39.1 | WebSocket reconnection feedback | Browser / UI | |
| AI-T35.1 | Pytest suite for order-listener | AI-Verified | |

### P5-StrategyEngine
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| TT-T40.1 | Verify RSI strategy loaded in order-generator | Terminal | |
| TT-T41.1 | Verify CCXT OHLCV fetch logs | Terminal | |
| TT-T42.1 | Verify strategy signal in DB | Terminal | |
| TT-T43.1 | Verify strategy scheduler logs (disabled) | Terminal | |
| TT-T43.2 | Verify strategy scheduler logs (enabled) | Terminal | |
| TT-T44.1 | Verify MA strategy loaded in order-generator | Terminal | |
| UT-T42.1 | Strategy signal to exchange E2E | Browser / UI | |
| UT-T43.1 | Strategy enable/disable toggle | Browser / UI | |

### P5-RiskMgmt
| Test ID | Description | Category | Status |
|---------|-------------|----------|--------|
| TT-T49.1 | Verify oversized order in dead letter | Terminal | |
| UT-T49.1 | Oversized order rejection | Browser / UI | |