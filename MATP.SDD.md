> **Note:** Testing plan, action items, and development roadmap have been
> moved to ACTION_PLAN.md and TEST_PLAN.md. This document covers architecture only.

# MATP — Software Design Document
**Version:** 4.0  **Date:** 2026-05-31  **Status:** Active Development

### Changelog from v3.0
- Added Symbol Coupling feature: `allow_quote_variants` and `allow_cross_charting` strategy flags with price-stripping safety rule (§4.1, §4.4.2, §5.1).
- Refactored webhook payload: removed legacy fields (`action`, `instrument`, `amount`, `platform`); replaced monolithic `symbol` with structured `base_asset` + `quote_asset`; added `target_position` for state synchronisation (§5.2).
- Added symbol validation and cross-charting subsection to Order Listener spec (§4.1).
- Added `symbol_validator.py` to Order Listener internal architecture (§4.1).
- Added Symbol Coupling UI toggles to Strategies page description (§4.4.2).
- Added `PUT /strategies/:id` endpoint to Dashboard API for flag updates (§4.4.1, §6.5).
- Added new definitions: Symbol Coupling, Quote Variants, Cross-Charting, Target Position, Execution Symbol (§1.3).
- Added `allow_quote_variants`, `allow_cross_charting` columns to `strategies` schema (§5.1).
- Added coupling flags to `GET /strategies` response (§6.5).
- Updated Redis cache key to include coupling flags (§7.3).
- Added migration reference: `002_symbol_coupling.sql` (§5.1, Appendix A).

---

## Table of Contents

- [1. Introduction](#1-introduction)
  - [1.1 Purpose](#11-purpose)
  - [1.2 Scope](#12-scope)
  - [1.3 Definitions](#13-definitions)
  - [1.4 Design Goals](#14-design-goals)
- [2. System Overview](#2-system-overview)
  - [2.1 High-Level Flow](#21-high-level-flow)
  - [2.2 Component Summary](#22-component-summary)
  - [2.3 Deployment Architecture](#23-deployment-architecture)
- [3. Strategy-Centric Design](#3-strategy-centric-design)
  - [3.1 Philosophy](#31-philosophy)
  - [3.2 Strategy Lifecycle](#32-strategy-lifecycle)
  - [3.3 Strategy Data Model Overview](#33-strategy-data-model-overview)
- [4. Component Specifications](#4-component-specifications)
  - [4.1 Order Listener (Python/FastAPI)](#41-order-listener-pythonfastapi)
  - [4.2 Order Generator (Python/FastAPI + APScheduler)](#42-order-generator-pythonfastapi--apscheduler)
  - [4.3 Order Executor (Python/FastAPI)](#43-order-executor-pythonfastapi)
  - [4.4 Dashboard Interface](#44-dashboard-interface)
    - [4.4.1 Dashboard API (Node/Express/TypeScript)](#441-dashboard-api-nodeexpresstypescript)
    - [4.4.2 Dashboard UI (React/Vite/Tailwind)](#442-dashboard-ui-reactvitetailwind)
- [5. Data Models](#5-data-models)
  - [5.1 Database Schema](#51-database-schema)
  - [5.2 Webhook Payload (Pydantic model)](#52-webhook-payload-pydantic-model)
  - [5.3 OrderRequest / OrderResult (Internal)](#53-orderrequest--orderresult-internal)
  - [5.4 WebSocket Event Format](#54-websocket-event-format)
  - [5.5 Statistics Response Format](#55-statistics-response-format)
- [6. API Contracts](#6-api-contracts)
  - [6.1 POST /api/listener/webhook](#61-post-apilistenerwebhook)
  - [6.2 POST /api/executor/execute (internal)](#62-post-apiexecutorexecute-internal)
  - [6.3 GET /api/dashboard/orders](#63-get-apidashboardorders)
  - [6.4 GET /api/dashboard/stats](#64-get-apidashboardstats)
  - [6.5 GET /api/dashboard/strategies](#65-get-apidashboardstrategies)
  - [6.6 GET /api/dashboard/strategies/:id/stats](#66-get-apidashboardstrategiesidstats)
  - [6.7 GET /api/dashboard/strategies/:id/equity-curve](#67-get-apidashboardstrategiesidequity-curve)
  - [6.8 GET /api/dashboard/accounts](#68-get-apidashboardaccounts)
  - [6.9 WebSocket /ws/orders](#69-websocket-wsorders)
- [7. Infrastructure](#7-infrastructure)
  - [7.1 Docker Compose Services](#71-docker-compose-services)
  - [7.2 Nginx Routing Rules](#72-nginx-routing-rules)
  - [7.3 Redis Channels and Keys](#73-redis-channels-and-keys)
  - [7.4 Environment Variables](#74-environment-variables)
  - [7.5 Network Isolation Model](#75-network-isolation-model)
- [8. Security](#8-security)
  - [8.1 Webhook Authentication (HMAC)](#81-webhook-authentication-hmac)
  - [8.2 Exchange Credential Storage (AES-256-GCM)](#82-exchange-credential-storage-aes-256-gcm)
  - [8.3 Network Isolation (Docker internal network)](#83-network-isolation-docker-internal-network)
  - [8.4 Input Validation (Pydantic)](#84-input-validation-pydantic)
  - [8.5 Local Network Deployment Model](#85-local-network-deployment-model)
- [9. Technology Stack](#9-technology-stack)
- [Appendix A — Repository File Index](#appendix-a--repository-file-index)

---

## 1. Introduction

### 1.1 Purpose

This document provides the complete software design specification for the **Modular Automated Trading Platform (MATP)**. MATP is a locally hosted, Docker-based system for automated cryptocurrency trading. It serves as the master reference document for the system's architecture and design.

### 1.2 Scope

The platform provides:
- Automated order generation via configurable trading strategies.
- Signal reception, validation, symbol resolution, and routing to the Order Executor.
- Centralised exchange communication via a dedicated Order Executor service.
- Dynamic, multi-account exchange management supporting multiple accounts per exchange and per mode.
- Flexible TradingView charting via Symbol Coupling — allowing signals from index or variant chart symbols to execute safely against the configured execution symbol.
- Real-time monitoring, analytics, and order management via a web interface.
- Integration with **Blofin** and **Hyperliquid** for perpetual futures trading.

### 1.3 Definitions

| Term | Definition |
|------|-----------|
| Webhook | HTTP POST payload carrying a trade signal. |
| Signal Bot | Blofin's copy-trading signal mechanism. |
| Strategy | A module that produces buy/sell signals on a schedule or condition. |
| Order Listener | Service that receives, validates, logs, and dispatches signals to the Order Executor. |
| Order Executor | Service that owns all exchange communication. The single source of truth for adapter logic. |
| Order Generator | Service that runs trading strategies and emits signals to the Order Listener. |
| Account | A named, credentialled connection to a specific exchange in a specific mode (live/demo). |
| AccountRegistry | In-memory cache of live adapter instances, keyed by `account_id`. |
| Adapter | A stateless class implementing the `ExchangeAdapter` interface for one exchange. |
| Symbol Coupling | The mechanism by which the listener resolves an incoming signal's chart symbol to the strategy's configured execution symbol using configurable tolerance flags. |
| Quote Variants | The set of quote currencies treated as interchangeable: USD, USDC, USDT, PERP. Used by `allow_quote_variants`. |
| Cross-Charting | A coupling mode where only the base asset is matched; the quote currency is ignored entirely. Used by `allow_cross_charting`. |
| Target Position | An optional signal field (`long`, `short`, `flat`) for state synchronisation. A `flat` value closes any open position for the strategy regardless of side. |
| Execution Symbol | The canonical symbol stored in `strategies.symbol`, used verbatim when submitting orders to the exchange. |
| MATP | Modular Automated Trading Platform (this system). |

### 1.4 Design Goals

- **Modularity**: Each component is independently deployable and replaceable.
- **Extensibility**: New strategies and exchange adapters integrate without touching core logic. Adding a new exchange requires one new adapter class and one registry entry.
- **Single Responsibility for Exchange I/O**: Only the Order Executor communicates with exchanges. No other service holds exchange credentials or calls exchange APIs directly.
- **Dynamic Account Management**: Multiple accounts per exchange, per mode, can run simultaneously. Account configuration lives in the database, not in environment variables.
- **Observability**: The system maintains a full audit trail of every signal, routing decision, and order outcome.
- **Resilience**: Failed exchange calls do not crash the system; the executor handles retries and dead-letter recording.
- **Charting Flexibility with Execution Safety**: TradingView chart symbols and execution symbols can differ, but price parameters are never sent to the exchange when loose coupling is used.
- **Mobile-friendly**: The Dashboard is usable on both phone and desktop.

---

## 2. System Overview

### 2.1 High-Level Flow

```
┌────────────────────────┐
│   TradingView Alerts   │──────┐
└────────────────────────┘      │
                                ▼
┌────────────────────────┐   ┌──────────────────────────┐
│   Order Generator      │──▶│   Order Listener         │
│   (strategy engine)    │   │   (webhook receiver,     │
└────────────────────────┘   │    symbol resolver,      │
                             │    validator & logger)   │
                             └────────────┬─────────────┘
                                          │ POST /execute
                                          ▼
                             ┌──────────────────────────┐
                             │   Order Executor         │
                             │   (exchange gateway,     │
                             │    account registry,     │──▶ Blofin (acc_01, acc_02 …)
                             │    adapter instances)    │──▶ Hyperliquid (acc_06, acc_07 …)
                             └────────────┬─────────────┘──▶ [future exchanges]
                                          │ writes
                                          ▼
                             ┌──────────────────────────┐
                             │      PostgreSQL DB       │
                             └────────────┬─────────────┘
                                          │ reads / WebSocket
                                          ▼
                             ┌──────────────────────────┐
                             │   Dashboard (Web)        │
                             │   React + REST + WS      │
                             └──────────────────────────┘
```

### 2.2 Component Summary

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| **Order Listener** | Receives, validates, resolves symbols, logs, and forwards signals to Order Executor. | Python, FastAPI |
| **Order Generator** | Runs trading strategies, emits signals to Order Listener. | Python, FastAPI, APScheduler |
| **Order Executor** | Single gateway for all exchange communication. Manages account instances. | Python, FastAPI |
| **Dashboard** | Web UI for monitoring, control, and analytics. | React, Node/Express |
| **Database** | Stores persistent state for orders, strategies, accounts, and configuration. | PostgreSQL |
| **Message Bus** | Decouples internal events using publish/subscribe. | Redis Pub/Sub |
| **Reverse Proxy** | Single external entry point, handles TLS termination and routing. | Nginx |

### 2.3 Deployment Architecture

```
Host Machine (local)
│
├── Port 80/443 → nginx (reverse proxy)
│                    ├── /api/listener  → order-listener:8001
│                    ├── /api/generator → order-generator:8002
│                    ├── /api/executor  → order-executor:8004  (internal only)
│                    ├── /api/dashboard → dashboard-api:8003
│                    └── /             → dashboard-ui:3000
│
├── Internal network: matp_net
│   ├── order-listener    (Python/FastAPI, port 8001)
│   ├── order-generator   (Python/FastAPI, port 8002)
│   ├── dashboard-api     (Node/Express, port 8003)
│   ├── order-executor    (Python/FastAPI, port 8004)
│   ├── dashboard-ui      (React/Vite, port 3000)
│   ├── postgres          (port 5432 — internal only)
│   └── redis             (port 6379 — internal only)
│
└── Volumes
    ├── postgres_data
    ├── logs/
    └── strategy_configs/
```

> **Note:** `order-executor` is an internal-only service. Nginx does not expose it externally. Only `order-listener` and `order-generator` call it, over the `matp_net` Docker network.

---

## 3. Strategy-Centric Design

### 3.1 Philosophy — everything links to a strategy

MATP's design is inherently strategy-centric. Every significant action and data point within the system is directly traceable to a specific trading strategy. This approach enables granular performance tracking, risk management, and operational control at the strategy level, rather than as a monolithic system.

Each strategy references a specific **account**, which determines which exchange and which credentials are used for execution. Multiple strategies can share an account; one strategy maps to exactly one account.

Each strategy also defines its **execution symbol** — the canonical symbol sent verbatim to the exchange. Incoming signals may carry a different chart symbol; the Symbol Coupling flags determine whether and how that mismatch is resolved.

### 3.2 Strategy Lifecycle (signal → order → position → stats)

1. **Signal Generation**: A strategy (from Order Generator or TradingView) emits a signal including a `strategy_id`.
2. **Symbol Resolution**: The Order Listener fetches the strategy config and resolves the incoming `base_asset` + `quote_asset` to the strategy's execution symbol using the coupling flags. Rejects with `422` if resolution fails.
3. **Order Creation**: The listener logs the order with `status = received` and dispatches it to the Order Executor using the resolved execution symbol.
4. **Exchange Execution**: The Order Executor resolves the `account_id`, loads the correct adapter instance, and submits the trade.
5. **Position Management**: On successful execution, a `strategy_positions` record is created linking the position to `strategy_id` and `opening_order_id`.
6. **Performance Tracking**: As positions open and close, `strategy_stats` aggregates metrics per strategy.

### 3.3 Strategy Data Model Overview

The `strategies` table is the central hub. All orders, positions, and performance metrics reference a `strategy_id`. Each strategy carries an `account_id` FK (execution target), a `symbol` column (execution symbol), and `allow_quote_variants` / `allow_cross_charting` flags (signal acceptance tolerance).

---

## 4. Component Specifications

### 4.1 Order Listener (Python/FastAPI)

The Order Listener receives, validates, resolves symbols, and logs every incoming signal. All execution is delegated to the Order Executor.

#### Internal Architecture

```
order-listener/
├── app/
│   ├── main.py             # FastAPI application and lifespan events
│   ├── webhook_handler.py  # Reception, HMAC validation, symbol resolution, DB write, executor dispatch
│   ├── symbol_validator.py # Symbol Coupling logic — resolve incoming assets to execution symbol
│   ├── executor_client.py  # HTTP client for POST order-executor:8004/execute
│   ├── orders_api.py       # API for order queries and retry actions
│   ├── config_api.py       # API for system configuration
│   ├── config.py           # Pydantic-settings for environment variables
│   ├── database.py         # asyncpg PostgreSQL connection pool
│   ├── redis_client.py     # Redis pub/sub client
│   └── models.py           # Pydantic models for webhook payload and internal types
├── Dockerfile
└── requirements.txt
```

#### Webhook Reception & Validation Flow

1. Receives HTTP POST at `/webhook/{strategy_id}`.
2. Validates `token` against `WEBHOOK_SECRET` using `hmac.compare_digest`.
3. Validates payload schema with Pydantic.
4. Rejects malformed or unauthenticated payloads with `403` / `422`.
5. **Fetches strategy configuration from the database** (or Redis cache, TTL 5s) to obtain `symbol`, `allow_quote_variants`, `allow_cross_charting`, and `account_id`.
6. **Resolves the incoming `base_asset` + `quote_asset` to the strategy's execution symbol** via `symbol_validator.resolve_symbol()`. Rejects with `422` if resolution fails.
7. Assigns internal `order_id` (UUID) and `received_at` timestamp.
8. Writes initial record to `orders` with `status = 'received'`.
9. Publishes to Redis `orders:received`.
10. Calls `executor_client.execute(order_request)` **synchronously**. The `OrderRequest` always carries the resolved execution symbol — never the raw incoming assets.
11. Updates the order record with the executor's result.
12. Returns `200 OK` to the caller.

> **Why synchronous?** Lag-fail / route-fail classification requires knowing the execution outcome. TradingView does not use the response body but requires a timely `200`; the internal executor call adds negligible latency.

#### Symbol Validation & Cross-Charting

The listener evaluates `base_asset` and `quote_asset` against the strategy's `symbol` using two strategy-level flags. Resolution logic lives exclusively in `symbol_validator.py`.

**Strict matching (both flags false — default):**
`{base_asset}-{quote_asset}` must match `strategies.symbol` exactly. Any mismatch returns `422`.

**`allow_quote_variants = true`:**
Treats USD, USDC, USDT, and PERP as interchangeable quote currencies. A signal with `quote_asset=USDC` is accepted for a strategy with `symbol=BTC-USDT`. The execution symbol is always taken from the strategy config.

**`allow_cross_charting = true`:**
Only the base asset is matched; the quote is ignored entirely. A signal with `base_asset=BTC, quote_asset=EUR` is accepted for `symbol=BTC-USDT`.

**Critical safety rule — price stripping:**
If either loose coupling flag resolves a mismatch, the listener **must strip `price`, `tp_price`, and `sl_price`** from the payload before building the `OrderRequest`, setting all three to `None`. This prevents index chart prices or cross-currency prices from reaching the exchange as limit or TP/SL parameters. The resulting order is always a market order when price stripping applies.

**`target_position = "flat"` handling:**
Triggers closure of any open position for the strategy on the configured account. Does not place a new order. `"long"` and `"short"` values are informational metadata only.

#### Routing Logic

The listener resolves `account_id` from the strategy config (Redis-cached, TTL 5s) and includes it in the `OrderRequest`. The executor owns all exchange routing.

#### Order Status Lifecycle

```
received → dispatched → submitted → filled
                                 └→ rejected
              └→ route_failed     (executor could not reach exchange)
              └→ lag_failed       (signal arrived after acceptable window)
              └→ symbol_rejected  (symbol mismatch, no coupling flag covers it)
```

#### Dead Letter Queue

Orders with `route_failed` or `rejected` status are recorded in `dead_letter_orders` with full context.

#### Management API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/{strategy_id}` | POST | Receives trade signals. |
| `/config` | GET | Retrieves system configuration. |
| `/orders` | GET | Lists recent orders with pagination and filtering. |
| `/orders/{id}` | GET | Retrieves a single order. |
| `/orders/{id}/retry` | POST | Retries a failed order via the executor. |
| `/health` | GET | Service health check. |

---

### 4.2 Order Generator (Python/FastAPI + APScheduler)

The Order Generator runs trading strategies and emits signals. It does not communicate with exchanges directly — it delegates via the listener → executor chain.

#### Internal Architecture

```
order-generator/
├── app/
│   ├── main.py              # FastAPI application and scheduler startup
│   ├── scheduler.py         # APScheduler instance and strategy loading
│   ├── strategies/
│   │   ├── base.py          # Abstract BaseStrategy class
│   │   ├── rsi_strategy.py  # RSI crossover strategy
│   │   ├── ma_crossover.py  # Moving average crossover strategy
│   │   └── [user defined]
│   ├── strategies_api.py    # REST API for strategy management
│   └── config.py            # Loads strategy configurations from YAML or DB
├── strategies_config/
│   └── example_rsi_btc.yaml
├── Dockerfile
└── requirements.txt
```

#### Strategy Abstraction

```python
class BaseStrategy(ABC):
    strategy_id: str
    name: str
    symbol: str
    interval: str         # "1m", "5m", "1h", etc.
    account_id: str       # references exchange_accounts.id
    enabled: bool

    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Called on each new OHLCV candle. Returns a Signal or None."""
        pass
```

#### Signal Emission

The generator always sends `base_asset` and `quote_asset` derived from its configured `symbol`. It never sends a mismatched chart symbol, so Symbol Coupling flags do not apply to internally generated signals.

#### Management API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/strategies` | GET | Lists all loaded strategies. |
| `/strategies/{id}/enable` | POST | Enables a strategy. |
| `/strategies/{id}/disable` | POST | Disables a strategy. |
| `/strategies/{id}/config` | GET | Returns strategy configuration. |
| `/health` | GET | Service health check. |

---

### 4.3 Order Executor (Python/FastAPI)

The Order Executor is the **single gateway for all exchange communication**. No other service holds exchange credentials or calls exchange APIs.

#### Internal Architecture

```
order-executor/
├── app/
│   ├── main.py               # FastAPI application and registry startup
│   ├── executor.py           # Main execute() handler
│   ├── registry.py           # AccountRegistry: in-memory adapter instance cache
│   ├── adapters/
│   │   ├── base.py           # Abstract ExchangeAdapter interface
│   │   ├── blofin.py         # Blofin adapter (stateless, credential-aware)
│   │   └── hyperliquid.py    # Hyperliquid adapter (stateless, credential-aware)
│   ├── credentials.py        # AES-256-GCM encrypt/decrypt helper
│   ├── database.py           # asyncpg pool for account record reads
│   ├── redis_client.py       # Redis pub/sub client for result events
│   └── models.py             # OrderRequest, OrderResult Pydantic models
├── Dockerfile
└── requirements.txt
```

#### ExchangeAdapter Interface

```python
class ExchangeAdapter(ABC):
    def __init__(self, credentials: dict, mode: str):
        pass

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        pass

    @abstractmethod
    async def close_position(self, symbol: str, side: str) -> OrderResult:
        pass

    @abstractmethod
    async def get_open_positions(self) -> list[dict]:
        pass
```

#### AccountRegistry

Maintains one adapter instance per `account_id`, loaded lazily. Credentials decrypted once per process lifetime.

```python
class AccountRegistry:
    async def get(self, account_id: str) -> ExchangeAdapter: ...
    def invalidate(self, account_id: str): ...
```

Adding a new exchange: one adapter class + one `case` in `_load()` + one DB row. Nothing else changes.

#### Execute Flow

```
POST /execute  { OrderRequest }
      │
      ├─ registry.get(account_id) → adapter instance
      ├─ adapter.submit_order(order)   ← symbol is always the execution symbol
      ├─ write result to orders table
      ├─ publish to Redis
      └─ return OrderResult
```

#### Retry Logic

3 attempts, exponential backoff (1s, 2s, 4s). On exhaustion: `route_failed` + dead letter.

#### Management API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/execute` | POST | Submits order. **Internal only.** |
| `/close-position` | POST | Closes an open position by account/symbol/side. **Internal only.** |
| `/credentials/encrypt` | POST | Encrypts a credentials JSON string using `MASTER_KEY`. Returns base64 ciphertext. |
| `/accounts/{id}/invalidate` | POST | Evicts adapter instance from registry. |
| `/accounts/{id}/balance` | GET | Returns live balance from the exchange adapter. |
| `/accounts/{id}/meta` | GET | Returns safe public metadata (e.g. wallet address). |
| `/accounts/{id}/positions` | GET | Returns open positions from the exchange adapter. |
| `/accounts/{id}/positions/close` | POST | Closes a specific position by symbol and side. |
| `/health` | GET | Service health check. |

---

### 4.4 Dashboard Interface

#### 4.4.1 Dashboard API (Node/Express/TypeScript)

##### Internal Architecture

```
dashboard-api/ (Node.js + Express)
├── src/
│   ├── index.ts
│   ├── routes/
│   │   ├── orders.ts
│   │   ├── strategies.ts    # Includes coupling flag update endpoints
│   │   ├── accounts.ts
│   │   ├── config.ts
│   │   ├── stats.ts
│   │   └── positions.ts
│   ├── ws/
│   │   └── orderFeed.ts
│   ├── db.ts
│   └── redis.ts
```

##### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/orders` | GET | Paginated, filterable order list. |
| `/orders/:id` | GET | Single order detail. |
| `/orders/:id/retry` | POST | Retries a failed order. |
| `/strategies` | GET | All strategies with metadata including coupling flags. |
| `/strategies/:id` | PUT | Updates strategy fields including coupling flags. |
| `/strategies/:id/enable` | POST | Enables a strategy. |
| `/strategies/:id/disable` | POST | Disables a strategy. |
| `/accounts` | GET | Lists exchange accounts (credentials masked). |
| `/accounts` | POST | Creates a new exchange account. |
| `/accounts/:id` | PUT | Updates account label or active status. |
| `/accounts/:id` | DELETE | Deactivates an account. |
| `/accounts/:id/invalidate` | POST | Evicts cached adapter instance in executor. |
| `/config` | GET | System configuration. |
| `/stats` | GET | Aggregated trading statistics. |
| `/positions` | GET | Live open positions from all active accounts. |

##### WebSocket Feed

Subscribes to Redis `orders:*` channels and forwards events to connected UI clients.

#### 4.4.2 Dashboard UI (React/Vite/Tailwind)

##### Internal Architecture

```
dashboard-ui/ (React + Vite + Tailwind)
├── src/
│   ├── App.tsx
│   ├── api.ts
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── Orders.tsx
│   │   ├── Positions.tsx
│   │   ├── Strategies.tsx   # Includes Symbol Coupling toggles
│   │   ├── Accounts.tsx
│   │   └── Settings.tsx
│   ├── components/
│   │   ├── Badges.tsx
│   │   ├── StatPanel.tsx
│   │   ├── AccountSelector.tsx
│   │   └── LiveFeed.tsx
│   └── hooks/
│       └── useOrderStream.ts
```

##### Pages and their Purpose

- **Dashboard**: Summary stats, live order feed (WebSocket), charts.
- **Orders**: Paginated history, filters, retry for failed orders.
- **Positions**: Live open positions per account, manual close.
- **Strategies**: Strategy list, enable/disable toggle, account assignment, and per-strategy **Symbol Coupling toggles**: `Allow Quote Variants` (treat USD/USDC/USDT/PERP as interchangeable) and `Allow Cross-Charting` (match base asset only, ignore quote). Both default to off. When `Allow Cross-Charting` is enabled, a warning badge on the strategy card indicates that price parameters will be stripped from loose-coupled signals.
- **Accounts**: Add/edit/delete exchange accounts, masked credential display, mode badge.
- **Settings**: System configuration, webhook URLs, health check links.

##### Responsive Design Rules

- Breakpoint ≤ 768px: bottom navigation bar, card-based order list, stacked stat cards.
- All interactive elements: minimum 44px touch target.

---

## 5. Data Models

### 5.1 Database Schema

```sql
-- Exchange accounts
CREATE TABLE exchange_accounts (
    id          VARCHAR(100) PRIMARY KEY,
    exchange    VARCHAR(30)  NOT NULL,
    mode        VARCHAR(10)  NOT NULL CHECK (mode IN ('live', 'demo')),
    label       VARCHAR(100) NOT NULL,
    credentials BYTEA        NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE orders (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol            VARCHAR(20) NOT NULL,
    side              VARCHAR(10) NOT NULL,
    signal            VARCHAR(20) NOT NULL,
    order_type        VARCHAR(20) NOT NULL,
    size              NUMERIC NOT NULL,
    price             NUMERIC,
    leverage          INTEGER,
    margin_mode       VARCHAR(10),
    tp_price          NUMERIC,
    sl_price          NUMERIC,
    account_id        VARCHAR(100) REFERENCES exchange_accounts(id),
    strategy_id       VARCHAR(100) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'received',
    exchange_order_id VARCHAR(100),
    pnl               NUMERIC,
    raw_webhook       JSONB NOT NULL,
    raw_response      JSONB,
    error_msg         TEXT,
    signal_source     VARCHAR(50),
    signal_metadata   JSONB,
    indicator_price   NUMERIC,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON orders (received_at DESC);
CREATE INDEX ON orders (status);
CREATE INDEX ON orders (strategy_id);
CREATE INDEX ON orders (account_id);

CREATE TABLE dead_letter_orders (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_retry  TIMESTAMPTZ
);

CREATE TABLE config (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE strategies (
    id                         VARCHAR(100) PRIMARY KEY,
    name                       VARCHAR(100) NOT NULL,
    class                      VARCHAR(100) NOT NULL,
    symbol                     VARCHAR(20)  NOT NULL,  -- execution symbol, e.g. BTC-USDT
    interval                   VARCHAR(10)  NOT NULL,
    account_id                 VARCHAR(100) NOT NULL REFERENCES exchange_accounts(id),
    enabled                    BOOLEAN      NOT NULL DEFAULT TRUE,
    config_yaml                TEXT         NOT NULL,
    config                     JSONB        NOT NULL DEFAULT '{}',  -- adapter settings, e.g. {"slippage_pct": 1.0}
    webhook_secret             VARCHAR(255) NOT NULL,
    webhook_enabled            BOOLEAN      DEFAULT TRUE,
    description                TEXT,
    max_daily_signals          INTEGER      DEFAULT 500,
    max_position_size          NUMERIC      DEFAULT 1.0,
    max_leverage               INTEGER      DEFAULT 10,
    max_daily_drawdown_percent NUMERIC      DEFAULT 20,
    capital_allocation_percent NUMERIC      DEFAULT 100,
    signals_today              INTEGER      DEFAULT 0,
    pnl_today                  NUMERIC      DEFAULT 0,
    pnl_total                  NUMERIC      DEFAULT 0,
    win_count                  INTEGER      DEFAULT 0,
    loss_count                 INTEGER      DEFAULT 0,
    last_signal_at             TIMESTAMPTZ,
    tags                       TEXT[]       DEFAULT '{}',
    -- Symbol Coupling flags (added via migration 002_symbol_coupling.sql)
    allow_quote_variants       BOOLEAN      NOT NULL DEFAULT FALSE,
    allow_cross_charting       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE order_events (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    event_time  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    message     TEXT
);

CREATE TABLE strategy_positions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id      VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    account_id       VARCHAR(100) NOT NULL REFERENCES exchange_accounts(id),
    symbol           VARCHAR(20)  NOT NULL,
    side             VARCHAR(10)  NOT NULL,
    entry_price      NUMERIC      NOT NULL,
    current_price    NUMERIC,
    size             NUMERIC      NOT NULL,
    leverage         INTEGER,
    margin_mode      VARCHAR(20),
    pnl_unrealized   NUMERIC,
    pnl_realized     NUMERIC      DEFAULT 0,
    status           VARCHAR(20)  DEFAULT 'open',
    opening_order_id UUID REFERENCES orders(id) ON DELETE RESTRICT,
    closing_order_id UUID REFERENCES orders(id) ON DELETE RESTRICT,
    opened_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE strategy_stats (
    id               BIGSERIAL PRIMARY KEY,
    strategy_id      VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    period_date      DATE         NOT NULL,
    trades_count     INTEGER      DEFAULT 0,
    trades_won       INTEGER      DEFAULT 0,
    trades_lost      INTEGER      DEFAULT 0,
    win_rate         NUMERIC,
    pnl_total        NUMERIC      DEFAULT 0,
    pnl_avg          NUMERIC,
    max_drawdown     NUMERIC      DEFAULT 0,
    capital_deployed NUMERIC      DEFAULT 0,
    leverage_avg     NUMERIC,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period_date)
);

CREATE TABLE strategy_webhook_calls (
    id            BIGSERIAL PRIMARY KEY,
    strategy_id   VARCHAR(100) NOT NULL REFERENCES strategies(id),
    received_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    http_status   INTEGER,
    error_message TEXT,
    source_ip     INET
);

-- Signal / execution audit trail (migration 005_signal_log.sql)
CREATE TABLE signal_log (
    id          BIGSERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_id VARCHAR(100) REFERENCES strategies(id) ON DELETE SET NULL,
    source_ip   INET,
    raw_body    JSONB,
    http_status INTEGER,
    outcome     VARCHAR(30),  -- 'filled' | 'guard_rejected' | 'validation_failed' | 'symbol_rejected' | 'auth_failed' | 'route_failed'
    error_detail TEXT,
    duration_ms INTEGER
);
CREATE INDEX ON signal_log (received_at DESC);
CREATE INDEX ON signal_log (strategy_id, received_at DESC);

CREATE TABLE order_execution_log (
    id                BIGSERIAL PRIMARY KEY,
    signal_log_id     BIGINT REFERENCES signal_log(id) ON DELETE SET NULL,
    order_id          UUID REFERENCES orders(id) ON DELETE SET NULL,
    attempted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    placed_at         TIMESTAMPTZ,
    exchange          VARCHAR(50),
    exchange_order_id VARCHAR(255),
    client_order_id   UUID,
    symbol            VARCHAR(30),
    side              VARCHAR(10),
    order_type        VARCHAR(20),
    requested_size    NUMERIC,
    status            VARCHAR(30),  -- 'filled' | 'pending' | 'rejected' | 'route_failed'
    error_message     TEXT
);
CREATE INDEX ON order_execution_log (signal_log_id);
CREATE INDEX ON order_execution_log (order_id);

-- updated_at triggers
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_exchange_accounts_modtime') THEN
        CREATE TRIGGER update_exchange_accounts_modtime BEFORE UPDATE ON exchange_accounts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_orders_modtime') THEN
        CREATE TRIGGER update_orders_modtime BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategies_modtime') THEN
        CREATE TRIGGER update_strategies_modtime BEFORE UPDATE ON strategies FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_config_modtime') THEN
        CREATE TRIGGER update_config_modtime BEFORE UPDATE ON config FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_positions_modtime') THEN
        CREATE TRIGGER update_strategy_positions_modtime BEFORE UPDATE ON strategy_positions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_stats_modtime') THEN
        CREATE TRIGGER update_strategy_stats_modtime BEFORE UPDATE ON strategy_stats FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
```

> **Migration note:** `allow_quote_variants` and `allow_cross_charting` are added to existing deployments via `db/migrations/002_symbol_coupling.sql`. The schema above reflects the complete target state.

### 5.2 Webhook Payload (Pydantic model)

Legacy fields removed: `action`, `instrument`, `amount`, `platform`. The monolithic `symbol` field is replaced by structured `base_asset` + `quote_asset`.

```python
class WebhookPayload(BaseModel):
    # Structured asset identification.
    # The listener constructs f"{base_asset}-{quote_asset}" and validates
    # it against strategies.symbol using the coupling flags.
    base_asset:      str
    quote_asset:     str

    side:            Literal["buy", "sell"]
    order_type:      Literal["market", "limit"] = "market"
    size:            Decimal
    price:           Optional[Decimal] = None     # stripped if loose coupling used
    leverage:        Optional[int] = None
    margin_mode:     Optional[Literal["cross", "isolated"]] = "cross"
    tp_price:        Optional[Decimal] = None     # stripped if loose coupling used
    sl_price:        Optional[Decimal] = None     # stripped if loose coupling used
    signal:          Literal["open_long", "close_long", "open_short", "close_short"]
    target_position: Optional[Literal["long", "short", "flat"]] = None
    timestamp:       datetime
    token:           str
    signal_source:   Optional[str] = "tradingview"
    signal_metadata: Optional[dict] = {}
    indicator_price: Optional[Decimal] = None
```

**Field notes:**
- `base_asset` + `quote_asset` replace `symbol`. TradingView alerts must be updated accordingly.
- `price`, `tp_price`, `sl_price` are set to `None` by the listener whenever loose coupling resolves a mismatch. This is not optional — it is enforced by `symbol_validator.py`.
- `target_position = "flat"` closes any open position for the strategy; does not place a new order.
- `token` is validated server-side and never forwarded to the executor.

### 5.3 OrderRequest / OrderResult (Internal)

Used exclusively for the `order-listener` → `order-executor` HTTP call. The executor never sees `base_asset` or `quote_asset`.

```python
class OrderRequest(BaseModel):
    order_id:    str       # UUID assigned by listener
    account_id:  str       # resolved from strategy
    symbol:      str       # always strategies.symbol — the execution symbol
    side:        Literal["buy", "sell"]
    signal:      str
    order_type:  str
    size:        Decimal
    price:       Optional[Decimal]    # None if loose coupling was applied
    leverage:    Optional[int]
    margin_mode: Optional[str]
    tp_price:    Optional[Decimal]    # None if loose coupling was applied
    sl_price:    Optional[Decimal]    # None if loose coupling was applied
    config:         Optional[dict]       # forwarded from strategies.config (e.g. slippage_pct)
    signal_log_id:  Optional[int]        # links executor attempt to originating signal_log row

class OrderResult(BaseModel):
    success:           bool
    exchange_order_id: Optional[str]
    status:            Literal["filled", "pending", "rejected", "route_failed"]
    error_msg:         Optional[str]
    raw_response:      Optional[dict]
```

### 5.4 WebSocket Event Format

```json
{
  "event": "order:filled",
  "order_id": "uuid",
  "status": "filled",
  "symbol": "BTC-USDT",
  "account_id": "acc_blofin_demo_01",
  "account_label": "Blofin Demo 1",
  "timestamp": "2026-05-28T10:00:01Z"
}
```

### 5.5 Statistics Response Format

```python
class TradingStats(BaseModel):
    period:       str
    total_orders: int
    filled:       int
    failed:       int
    win_count:    int
    loss_count:   int
    win_rate:     float
    total_pnl:    Decimal
    avg_pnl:      Decimal
    by_account:   dict
    by_strategy:  dict
```

---

## 6. API Contracts

### 6.1 POST /api/listener/webhook

**Request:** See §5.2. **Response 200:** `{ "order_id": "uuid", "status": "filled", "message": "OK" }`
**Response 403:** Invalid token. **Response 422:** Schema error or symbol mismatch.

### 6.2 POST /api/executor/execute (internal)

**Request:** §5.3 OrderRequest. **Response 200:** §5.3 OrderResult. **Response 503:** Retries exhausted.

### 6.3 GET /api/dashboard/orders

**Query Parameters:** `page`, `limit`, `symbol`, `account_id`, `status`, `strategy_id`, `from`, `to`

**Response 200:**
```json
{
  "total": 342, "page": 1, "limit": 50,
  "items": [{
    "id": "uuid", "received_at": "2026-05-28T10:00:00Z",
    "symbol": "BTC-USDT", "side": "buy", "signal": "open_long",
    "size": "0.01", "account_id": "acc_blofin_demo_01",
    "account_label": "Blofin Demo 1", "status": "filled",
    "exchange_order_id": "blofin-123", "pnl": "12.50",
    "strategy_id": "rsi-btc-5m"
  }]
}
```

### 6.4 GET /api/dashboard/stats

**Query Parameter:** `period` = `today | 7d | 30d | all`. **Response 200:** See §5.5.

### 6.5 GET /api/dashboard/strategies

**Response 200:**
```json
[{
  "id": "rsi-btc-5m", "name": "RSI BTC 5m", "symbol": "BTC-USDT",
  "interval": "5m", "account_id": "acc_blofin_demo_01",
  "account_label": "Blofin Demo 1", "account_exchange": "blofin",
  "account_mode": "demo", "enabled": true,
  "allow_quote_variants": false,
  "allow_cross_charting": false,
  "signals_today": 5, "pnl_total": "123.45"
}]
```

### 6.6 GET /api/dashboard/strategies/:id/stats

**Response 200:**
```json
{
  "strategy_id": "rsi-btc-5m", "trades_count": 100,
  "trades_won": 60, "win_rate": 60.0,
  "pnl_total": "500.00", "pnl_avg": "5.00", "max_drawdown": "10.0"
}
```

### 6.7 GET /api/dashboard/strategies/:id/equity-curve

**Response 200:**
```json
[
  { "date": "2026-05-01", "cumulative_pnl": "10.00" },
  { "date": "2026-05-02", "cumulative_pnl": "15.50" }
]
```

### 6.8 GET /api/dashboard/accounts

**Response 200:**
```json
[{
  "id": "acc_blofin_demo_01", "exchange": "blofin", "mode": "demo",
  "label": "Blofin Demo 1", "is_active": true,
  "created_at": "2026-05-01T00:00:00Z"
}]
```

### 6.9 WebSocket /ws/orders

`ws://host/ws/orders` — JSON messages on every order status change. See §5.4.

### 6.10 GET /api/dashboard/signals

Paginated signal log with one joined execution-log row per signal. Filters: `strategy_id`, `outcome`, `from` (ISO timestamp), `to` (ISO timestamp). Pagination: `page`, `limit` (max 200).

**Response 200:**
```json
{
  "total": 142,
  "page": 1,
  "limit": 50,
  "items": [{
    "id": 1,
    "received_at": "2026-06-06T12:00:00Z",
    "source_ip": "1.2.3.4",
    "strategy_id": "hl_eth_long",
    "http_status": 200,
    "outcome": "filled",
    "error_detail": null,
    "raw_body": { "base_asset": "ETH", "side": "buy", "size": "0.02" },
    "duration_ms": 312,
    "oel_id": 7,
    "exchange": "hyperliquid",
    "exchange_order_id": "54506983576",
    "client_order_id": "uuid",
    "oel_symbol": "ETH-USDT",
    "oel_side": "buy",
    "oel_order_type": "market",
    "requested_size": "0.02",
    "oel_status": "filled",
    "oel_error_message": null
  }]
}
```

Outcome values: `filled`, `guard_rejected`, `validation_failed`, `symbol_rejected`, `auth_failed`, `route_failed`.

### 6.11 GET /api/dashboard/signals/strategies

Returns distinct `strategy_id` values present in `signal_log`, used to populate the filter dropdown.

**Response 200:** `["hl_eth_long", "blofin_btc_01"]`

---

## 7. Infrastructure

### 7.1 Docker Compose Services

| Service | Technology | Port (internal) | Purpose |
|---------|------------|-----------------|---------|
| `nginx` | Nginx | 80 | Reverse proxy, TLS termination. |
| `postgres` | PostgreSQL 16 | 5432 | Persistent storage. |
| `redis` | Redis 7 | 6379 | Message bus and config cache. |
| `order-listener` | Python/FastAPI | 8001 | Webhook reception, symbol validation, dispatch. |
| `order-generator` | Python/FastAPI + APScheduler | 8002 | Strategy execution, signal emission. |
| `dashboard-api` | Node.js/Express/TS | 8003 | Dashboard backend, REST, WebSocket. |
| `order-executor` | Python/FastAPI | 8004 | Exchange gateway, account registry, adapters. |
| `dashboard-ui` | React/Vite/Tailwind | 3000 | Frontend web application. |

### 7.2 Nginx Routing Rules

```nginx
server {
    listen 80;
    server_name localhost;
    location /api/listener/  { proxy_pass http://order-listener:8001/; }
    location /api/generator/ { proxy_pass http://order-generator:8002/; }
    location /api/dashboard/ { proxy_pass http://dashboard-api:8003/; }
    location /ws/ {
        proxy_pass http://dashboard-api:8003/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    location / { proxy_pass http://dashboard-ui:3000/; }
}
```

### 7.3 Redis Channels and Keys

| Channel / Key | Purpose |
|---------------|---------|
| `orders:received` | New webhook received by listener. |
| `orders:dispatched` | Order forwarded to executor. |
| `orders:filled` | Exchange confirmed fill. |
| `orders:failed` | Routing or exchange error. |
| `config:strategy_cache:{strategy_id}` | Cached strategy config including `account_id` and coupling flags (TTL 5s). |

### 7.4 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password. |
| `WEBHOOK_SECRET` | Yes | Shared secret for webhook HMAC (min 32 chars). |
| `MASTER_KEY` | Yes | AES-256-GCM master key for credential encryption (min 32 chars). |
| `DATA_FEED_EXCHANGE` | No | OHLCV data source for Order Generator (default: binance). |

### 7.5 Network Isolation Model

PostgreSQL, Redis, and `order-executor` are isolated within `matp_net`. The executor has no Nginx route. Exchange credentials never leave the executor container.

---

## 8. Security

### 8.1 Webhook Authentication (HMAC)

All webhook POST requests validated via `hmac.compare_digest` against `WEBHOOK_SECRET`. Invalid tokens: `403`.

### 8.2 Exchange Credential Storage (AES-256-GCM)

Credentials stored encrypted in `exchange_accounts` using AES-256-GCM with `MASTER_KEY`. Only `order-executor` decrypts them. Never logged, never returned in API responses, masked in UI.

### 8.3 Network Isolation (Docker internal network)

Postgres, Redis, and executor unreachable from outside `matp_net`.

### 8.4 Input Validation (Pydantic)

All webhook payloads and `OrderRequest` objects validated with Pydantic in strict mode. Symbol coupling validation occurs after Pydantic parsing, in `symbol_validator.py`.

### 8.5 Local Network Deployment Model

MATP designed for local hosting. Do not expose port 80 to the internet without VPN, basic auth, or IP allowlist.

---

## 9. Technology Stack

| Layer | Technology | Version | Rationale |
|-------|------------|---------|-----------|
| Order Listener | Python | 3.12 | Async webhook handling with FastAPI. |
| Order Generator | Python | 3.12 | CCXT integration, APScheduler, FastAPI. |
| Order Executor | Python | 3.12 | Owns all exchange I/O. |
| Dashboard API | Node.js | 20 | WebSocket support, mature pg/Redis libraries. |
| Dashboard UI | React | 18 | Component architecture, efficient real-time updates. |
| UI Build Tool | Vite | — | Fast dev server, optimised builds. |
| UI Styling | Tailwind CSS | — | Utility-first, rapid consistent styling. |
| Database | PostgreSQL | 16 | ACID-compliant, JSONB support. |
| Message Bus | Redis | 7 | High-performance Pub/Sub and config caching. |
| Reverse Proxy | Nginx | — | Routing, TLS, single external entry point. |
| Market Data | CCXT | Python lib | Unified API across 100+ exchanges for OHLCV data. |
| Orchestration | Docker Compose | v2 | Local multi-container deployment. |
| Exchange Auth | HMAC-SHA256 (Blofin) | — | Blofin-specific auth protocol. |
| Exchange Auth | ECDSA (Hyperliquid) | — | Hyperliquid cryptographic signature standard. |
| Credential Encryption | AES-256-GCM | — | Symmetric encryption for stored credentials. |

---

## Appendix A — Repository File Index

| File Path | Description |
|-----------|-------------|
| `order-listener/app/webhook_handler.py` | Webhook reception, HMAC auth, symbol resolution, DB write, executor dispatch. |
| `order-listener/app/symbol_validator.py` | Symbol Coupling logic — resolves incoming assets to execution symbol, applies price stripping. |
| `order-listener/app/executor_client.py` | HTTP client for `POST order-executor:8004/execute`. |
| `order-executor/app/executor.py` | Main execute handler: resolves account, calls adapter, writes result. |
| `order-executor/app/registry.py` | `AccountRegistry`: lazy-loaded, in-memory adapter instance cache. |
| `order-executor/app/adapters/base.py` | Abstract `ExchangeAdapter` interface. |
| `order-executor/app/adapters/blofin.py` | Blofin adapter (HMAC-SHA256 signed requests). |
| `order-executor/app/adapters/hyperliquid.py` | Hyperliquid adapter (ECDSA signing). |
| `order-executor/app/credentials.py` | AES-256-GCM encrypt/decrypt helper. |
| `order-generator/app/scheduler.py` | APScheduler instance, CCXT OHLCV polling, signal emission. |
| `order-generator/app/strategies/base.py` | Abstract `BaseStrategy` with `account_id` field. |
| `dashboard-api/src/routes/accounts.ts` | CRUD endpoints for `exchange_accounts` (masked credentials). |
| `dashboard-api/src/routes/strategies.ts` | Strategy endpoints including coupling flag updates. |
| `dashboard-api/src/ws/orderFeed.ts` | Redis subscription → WebSocket broadcast to UI clients. |
| `dashboard-ui/src/pages/Accounts.tsx` | Account management UI. |
| `dashboard-ui/src/pages/Strategies.tsx` | Strategy management UI including Symbol Coupling toggles. |
| `dashboard-ui/src/hooks/useOrderStream.ts` | WebSocket hook with 3-second auto-reconnect. |
| `db/init.sql` | Full schema including Symbol Coupling columns and all triggers. |
| `db/migrations/001_exchange_accounts.sql` | Adds `exchange_accounts` and `account_id` columns. |
| `db/migrations/002_symbol_coupling.sql` | Adds `allow_quote_variants`, `allow_cross_charting` to strategies. |
| `docs/tradingview.md` | TradingView alert setup. Updated for `base_asset`/`quote_asset` payload format. |
| `docs/setup.md` | Installation and development environment guide. |
| `MATP.SDD.md` | This document. |
| `ACTION_PLAN.md` | Prioritised task list and development roadmap. |
| `TEST_PLAN.md` | Test cases and verification checklist. |
| `README.md` | High-level overview and quick start. |
| `CHANGELOG.md` | Reverse-chronological technical changelog. |
