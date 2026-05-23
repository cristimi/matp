> **Note:** Testing plan, action items, and development roadmap have been
> moved to ACTION_PLAN.md and TEST_PLAN.md. This document covers architecture only.

# MATP — Software Design Document
**Version:** 2.0  **Date:** 2026-05-22  **Status:** Active Development

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
    - [Internal Architecture](#internal-architecture)
    - [Webhook Reception & Validation Flow](#webhook-reception--validation-flow)
    - [Routing Logic](#routing-logic)
    - [Exchange Adapters](#exchange-adapters)
    - [Order Status Lifecycle](#order-status-lifecycle)
    - [Dead Letter Queue](#dead-letter-queue)
    - [Management API Endpoints](#management-api-endpoints)
  - [4.2 Order Generator (Python/FastAPI + APScheduler)](#42-order-generator-pythonfastapi--apscheduler)
    - [Internal Architecture](#internal-architecture-1)
    - [Strategy Abstraction](#strategy-abstraction)
    - [Data Feed](#data-feed)
    - [Signal Emission](#signal-emission)
    - [Management API Endpoints](#management-api-endpoints-1)
  - [4.3 Dashboard Interface](#43-dashboard-interface)
    - [4.3.1 Dashboard API (Node/Express/TypeScript)](#431-dashboard-api-nodeexpresstypescript)
      - [Internal Architecture](#internal-architecture-2)
      - [API Endpoints](#api-endpoints)
      - [WebSocket Feed](#websocket-feed)
    - [4.3.2 Dashboard UI (React/Vite/Tailwind)](#432-dashboard-ui-reactvitetailwind)
      - [Internal Architecture](#internal-architecture-3)
      - [Pages and their Purpose](#pages-and-their-purpose)
      - [Responsive Design Rules](#responsive-design-rules)
      - [Real-time Update Flow](#real-time-update-flow)
- [5. Data Models](#5-data-models)
  - [5.1 Database Schema](#51-database-schema)
  - [5.2 Webhook Payload (Pydantic model)](#52-webhook-payload-pydantic-model)
  - [5.3 WebSocket Event Format](#53-websocket-event-format)
  - [5.4 Statistics Response Format](#54-statistics-response-format)
- [6. API Contracts](#6-api-contracts)
  - [6.1 POST /api/listener/webhook](#61-post-apilistenerwebhook)
  - [6.2 GET /api/dashboard/orders](#62-get-apidashboardorders)
  - [6.3 GET /api/dashboard/stats](#63-get-apidashboardstats)
  - [6.4 GET /api/dashboard/strategies](#64-get-apidashboardstrategies)
  - [6.5 GET /api/dashboard/strategies/:id/stats](#65-get-apidashboardstrategiesidstats)
  - [6.6 GET /api/dashboard/strategies/:id/equity-curve](#66-get-apidashboardstrategiesidequity-curve)
  - [6.7 PUT /api/dashboard/config/active_platform](#67-put-apidashboardconfigactive_platform)
  - [6.8 WebSocket /ws/orders](#68-websocket-wsorders)
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
- Signal reception, validation, logging, and routing to exchange platforms.
- Real-time monitoring, analytics, and order management via a web interface.
- Integration with **Blofin** (signal bot) and **Hyperliquid** for perpetual futures trading.

### 1.3 Definitions

| Term | Definition |
|------|-----------|
| Webhook | HTTP POST payload carrying a trade signal. |
| Signal Bot | Blofin's copy-trading signal mechanism. |
| Strategy | A module that produces buy/sell signals on a schedule or condition. |
| Order Listener | Service that receives, validates, logs, and routes webhooks. |
| Active Platform | The default exchange to which orders are routed. |
| MATP | Modular Automated Trading Platform (this system). |

### 1.4 Design Goals

-   **Modularity**: Each component is independently deployable and replaceable.
-   **Extensibility**: New strategies and exchange adapters integrate without touching core logic.
-   **Observability**: The system maintains a full audit trail of every signal, routing decision, and order outcome.
-   **Resilience**: Failed exchange calls do not crash the system; it incorporates retries and dead-letter queues.
-   **Mobile-friendly**: The Dashboard is usable on both phone and desktop.

## 2. System Overview

### 2.1 High-Level Flow

```
┌────────────────────────┐       ┌──────────────────────────┐
│   TradingView Alerts   │──────▶│                          │
└────────────────────────┘       │                          │
                                 │    Order Listener        │
┌────────────────────────┐       │    (webhook receiver,    │──▶ Blofin Signal Bot
│   Order Generator      │──────▶│     router & logger)     │──▶ Hyperliquid
│   (strategy engine)    │       │                          │──▶ [future platforms]
└────────────────────────┘       └──────────┬───────────────┘
                                            │ writes
                                            ▼
                                  ┌──────────────────┐
                                  │   PostgreSQL DB  │
                                  └──────────┬───────┘
                                            │ reads
                                            ▼
                                  ┌──────────────────┐
                                  │  Dashboard (Web) │
                                  │  React + REST    │
                                  └──────────────────┘
```

### 2.2 Component Summary

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| **Order Generator** | Runs trading strategies, emits webhook signals. | Python, APScheduler |
| **Order Listener** | Receives, validates, logs, and routes webhooks. | Python, FastAPI |
| **Dashboard** | Provides a web UI for monitoring, control, and analytics. | React, Node/Express |
| **Database** | Stores persistent state for orders, strategies, and configuration. | PostgreSQL |
| **Message Bus** | Decouples internal events using publish/subscribe. | Redis Pub/Sub |
| **Reverse Proxy** | Handles TLS termination and acts as a single entry point. | Nginx |

### 2.3 Deployment Architecture

All services operate within Docker containers, orchestrated by Docker Compose. Communication between containers occurs over an internal Docker bridge network. Nginx serves as the sole external entry point, exposing only HTTP/HTTPS ports.

```
Host Machine (local)
│
├── Port 80/443 → nginx (reverse proxy)
│                    ├── /api/listener  → order-listener:8001
│                    ├── /api/generator → order-generator:8002
│                    ├── /api/dashboard → dashboard-api:8003
│                    └── /             → dashboard-ui:3000
│
├── Internal network: matp_net
│   ├── order-generator   (Python/FastAPI, port 8002)
│   ├── order-listener    (Python/FastAPI, port 8001)
│   ├── dashboard-api     (Node/Express, port 8003)
│   ├── dashboard-ui      (React/Vite, port 3000)
│   ├── postgres          (port 5432 — internal only)
│   └── redis             (port 6379 — internal only)
│
└── Volumes
    ├── postgres_data
    ├── logs/
    └── strategy_configs/
```

## 3. Strategy-Centric Design

### 3.1 Philosophy — everything links to a strategy

MATP's design is inherently strategy-centric. Every significant action and data point within the system is directly traceable to a specific trading strategy. This approach enables granular performance tracking, risk management, and operational control at the strategy level, rather than a monolithic system. Strategies are distinct entities, each with its own configuration, performance metrics, and managed positions.

### 3.2 Strategy Lifecycle (signal → order → position → stats)

The lifecycle of a trade signal within MATP is deeply integrated with the concept of a strategy:
1.  **Signal Generation**: A strategy (either from Order Generator or an external source like TradingView) emits a signal. This signal includes a `strategy_id`.
2.  **Order Creation**: The Order Listener receives this signal and logs it as an `order` in the database, retaining the associated `strategy_id`.
3.  **Position Management**: If the order successfully executes on an exchange and opens a new trade, a corresponding `strategy_positions` record is created, explicitly linking the open position to the `strategy_id` and the `opening_order_id`.
4.  **Performance Tracking**: As positions open and close, and orders are filled, `strategy_stats` and `strategy_performance` tables aggregate metrics, providing strategy-specific P&L, win rates, and other key performance indicators. This ensures that all trading activity contributes to the individual performance profile of its originating strategy.

### 3.3 Strategy Data Model Overview

The `strategies` table is the central hub for strategy configurations. All orders, positions, and performance metrics directly reference a `strategy_id`. This relational structure facilitates comprehensive analysis and management of each trading strategy's independent performance and risk profile.

## 4. Component Specifications

### 4.1 Order Listener (Python/FastAPI)

The Order Listener service receives, validates, logs every signal, and routes it to the appropriate exchange adapter.

#### Internal Architecture

```
order-listener/
├── app/
│   ├── main.py             # FastAPI application and lifespan events
│   ├── webhook_handler.py  # Handles reception and validation of POST /webhook
│   ├── router.py           # Implements routing logic for platform selection
│   ├── adapters/
│   │   ├── base.py         # Defines the abstract ExchangeAdapter interface
│   │   ├── blofin.py       # Blofin Signal Bot integration
│   │   └── hyperliquid.py  # Hyperliquid integration (scaffold)
│   ├── orders_api.py       # Provides API for order-related queries and actions
│   ├── config_api.py       # Handles API for active platform configuration
│   ├── config.py           # Manages pydantic-settings for environment variables
│   ├── database.py         # Manages asyncpg PostgreSQL connection pool
│   ├── redis_client.py     # Manages Redis pub/sub client
│   └── models.py           # Defines Pydantic models for webhook payload and internal types
├── Dockerfile
└── requirements.txt
```

#### Webhook Reception & Validation Flow

1.  Receives HTTP POST requests at `/webhook/{strategy_id}`.
2.  Validates the `token` field against the `WEBHOOK_SECRET` environment variable using `hmac.compare_digest` for timing attack prevention.
3.  Validates the payload's schema using Pydantic.
4.  Rejects malformed or unauthenticated payloads with `403` / `422` status codes and logs the rejection.
5.  Assigns an internal `order_id` (UUID) and `received_at` timestamp.
6.  Writes an initial record to the `orders` table with `status = 'received'`.
7.  Publishes the event to the Redis channel `orders:received`.
8.  Triggers an asynchronous routing task and immediately returns a `200 OK` response.

#### Routing Logic

The routing logic dynamically selects an exchange adapter based on the webhook's `platform` field:
-   If `webhook.platform == "blofin"`, it routes to `BlofinAdapter`.
-   If `webhook.platform == "hyperliquid"`, it routes to `HyperliquidAdapter`.
-   If `webhook.platform == "auto"`, it uses the `active_platform` configured in the database, which is cached in Redis for 5 seconds to ensure rapid updates.

#### Exchange Adapters

Exchange adapters implement a common `ExchangeAdapter` interface to interact with specific exchange APIs.

**Abstract Interface:**
```python
class ExchangeAdapter(ABC):
    @abstractmethod
    async def place_order(self, signal: WebhookPayload) -> OrderResult:
        pass

    @abstractmethod
    async def get_open_positions(self) -> List[Position]:
        pass

    @abstractmethod
    async def close_position(self, symbol: str, side: str) -> OrderResult:
        pass
```

**Blofin Signal Bot Adapter:**
-   Interacts with Blofin's Signal Bot REST API.
-   Authenticates requests using API key and HMAC-SHA256 signature.
-   Maps internal signal fields to Blofin's required format.
-   Handles Blofin-specific error codes for scenarios such as rate limits or insufficient margin.

**Hyperliquid Adapter:**
-   Interacts with Hyperliquid's REST/WebSocket API.
-   Authenticates requests using ECDSA private key (standard Hyperliquid authentication).
-   Maps internal signals to Hyperliquid's perpetual futures order format.

Both adapters return a standardized `OrderResult`:
```python
class OrderResult:
    success: bool
    exchange_order_id: str
    status: str        # "filled", "pending", "rejected"
    error_msg: str
    raw_response: dict
```

#### Order Status Lifecycle

Orders progress through defined statuses:
`received` → `routing` → `submitted` → `filled`
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└→ `rejected`
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└→ `route_failed` (indicating an exchange adapter error)

Status updates are written to PostgreSQL and published to Redis on each transition, creating an audit trail.

#### Dead Letter Queue

Failed orders (with `route_failed` or `rejected` statuses) are recorded in a `dead_letter_orders` table, retaining full context for later review and manual retry.

#### Management API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/{strategy_id}` | POST | Receives trade signals for a specific strategy. |
| `/config` | GET | Retrieves system configuration. |
| `/config/active_platform` | PUT | Sets the active trading platform. |
| `/orders` | GET | Lists recent orders, supporting pagination and filtering. |
| `/orders/{id}` | GET | Retrieves details for a single order. |
| `/orders/{id}/retry` | POST | Retries a failed or dead-letter order. |
| `/health` | GET | Provides a service health check. |

### 4.2 Order Generator (Python/FastAPI + APScheduler)

The Order Generator service runs one or more trading strategies concurrently, each producing webhook-format signals dispatched to the Order Listener.

#### Internal Architecture

```
order-generator/
├── app/
│   ├── main.py              # FastAPI application and scheduler startup
│   ├── scheduler.py         # Manages APScheduler instance and strategy loading
│   ├── strategies/
│   │   ├── base.py          # Defines the abstract BaseStrategy class
│   │   ├── rsi_strategy.py  # Implements an RSI crossover strategy
│   │   ├── ma_crossover.py  # Implements a moving average crossover strategy
│   │   └── [user defined]
│   ├── strategies_api.py    # Provides REST API for strategy management
│   └── config.py            # Loads strategy configurations from YAML files or environment variables
├── strategies_config/       # Directory for YAML files defining strategy instances
│   └── example_rsi_btc.yaml
├── Dockerfile
└── requirements.txt
```

#### Strategy Abstraction

Every trading strategy adheres to the `BaseStrategy` abstract class, ensuring a consistent interface for signal generation.

```python
class BaseStrategy(ABC):
    strategy_id: str       # UUID, assigned at load time
    name: str
    symbol: str
    interval: str          # "1m", "5m", "1h", etc.
    platform: str          # Default platform override or "auto"
    enabled: bool

    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Called on each new OHLCV candle. Returns a Signal or None."""
        pass
```

Strategies are instantiated from YAML configuration files, allowing multiple instances of the same strategy class with distinct parameters.

**Example YAML Configuration:**
```yaml
strategy_id: rsi-btc-5m
class: RsiStrategy
symbol: BTC-USDT
interval: 5m
platform: auto
enabled: false   # Set to true to activate
params:
  period: 14
  oversold: 30
  overbought: 70
  size: "0.01"
  leverage: "10"
```

#### Data Feed

The generator uses the **CCXT** library to fetch OHLCV (Open, High, Low, Close, Volume) data from a configurable data source (e.g., Binance). Data polling is managed by APScheduler tasks, synchronized with each strategy's specified interval.

#### Signal Emission

When a strategy's `on_candle()` method returns a `Signal` object, the system constructs a standard webhook payload. This payload is then sent via HTTP POST to the Order Listener's `/webhook` endpoint over the internal Docker network. The system includes retry mechanisms with exponential backoff (up to 3 attempts) for signal delivery.

#### Management API Endpoints

The Order Generator exposes a REST API for strategy management, accessible via the Dashboard through Nginx.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/strategies` | GET | Lists all loaded strategies and their current status. |
| `/strategies/{id}/enable` | POST | Enables a specific strategy. |
| `/strategies/{id}/disable` | POST | Disables a specific strategy. |
| `/strategies/{id}/config` | GET | Displays the configuration and parameters for a specific strategy. |
| `/health` | GET | Provides a service health check. |

### 4.3 Dashboard Interface

The Dashboard provides a web-based user interface for monitoring all orders, viewing statistics, managing strategies, and configuring platform settings. It is designed to be responsive for both desktop and mobile devices.

#### 4.3.1 Dashboard API (Node/Express/TypeScript)

The Dashboard API acts as a backend for the UI, providing data and WebSocket services.

**Note on Reconciliation**: The system includes a 'Reconcile' feature to synchronize database state with live exchange data. Future iterations should expand this to support:
- **Option 1**: Full deletion of stale position records and their associated order history.
- **Option 2**: Manual specification of closing price and date for historical accuracy.

##### Internal Architecture

```
dashboard-api/ (Node.js + Express)
├── src/
│   ├── index.ts             # Main entry point for the API server
│   ├── routes/
│   │   ├── orders.ts        # Handles order list, detail, and retry operations
│   │   ├── strategies.ts    # Proxies requests to Order Generator API for strategy management
│   │   ├── config.ts        # Manages active platform and exchange settings
│   │   ├── stats.ts         # Provides aggregated trading statistics
│   │   └── positions.ts     # Fetches live open positions from exchanges
│   ├── ws/
│   │   └── orderFeed.ts     # Manages WebSocket connections: subscribes to Redis and pushes events to clients
│   ├── db.ts                # PostgreSQL client setup (pg library)
│   └── redis.ts             # Redis subscriber client
```

##### API Endpoints

The Dashboard API provides RESTful endpoints to the UI for various data and control functions.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/orders` | GET | Retrieves a paginated and filterable list of orders. |
| `/orders/:id` | GET | Retrieves details for a specific order. |
| `/orders/:id/retry` | POST | Retries a failed order. |
| `/strategies` | GET | Lists all configured strategies and their metadata. |
| `/strategies/:id/enable` | POST | Enables a specific strategy. |
| `/strategies/:id/disable` | POST | Disables a specific strategy. |
| `/config` | GET | Retrieves system configuration (e.g., active platform). |
| `/config/active_platform` | PUT | Updates the active trading platform. |
| `/stats` | GET | Provides aggregated trading statistics. |
| `/positions` | GET | Retrieves live open positions from connected exchanges. |

##### WebSocket Feed

The Dashboard API hosts a WebSocket server that broadcasts real-time order events. It subscribes to Redis `orders:*` channels (where Order Listener publishes events) and forwards these events to connected UI clients, ensuring immediate updates in the Dashboard.

#### 4.3.2 Dashboard UI (React/Vite/Tailwind)

The Dashboard UI is a React-based frontend providing the user interface.

##### Internal Architecture

```
dashboard-ui/ (React + Vite + Tailwind)
├── src/
│   ├── App.tsx              # Defines router and main layout with sidebar/bottom navigation
│   ├── api.ts               # Provides typed fetch wrapper and TypeScript interfaces for API interactions
│   ├── pages/
│   │   ├── Dashboard.tsx    # Overview: displays summary stats, live feed, and charts
│   │   ├── Orders.tsx       # Shows full order history with filters and retry functionality
│   │   ├── Positions.tsx    # Displays live open positions and allows manual closing
│   │   ├── Strategies.tsx   # Manages strategy configurations and enables/disables strategies
│   │   └── Settings.tsx     # Handles platform configuration, API keys, and health links
│   ├── components/
│   │   ├── Badges.tsx       # Contains reusable status, side, and platform badge components
│   │   ├── StatPanel.tsx    # Reusable component for displaying statistical cards
│   │   ├── PlatformSelector.tsx # Component for selecting the active trading platform
│   │   └── LiveFeed.tsx     # Displays real-time order events via WebSocket
│   └── hooks/
│       └── useOrderStream.ts   # Custom React hook for WebSocket connections with auto-reconnect
```

##### Pages and their Purpose

-   **Dashboard (Overview)**: Displays summary stat cards (Total Orders, Win Rate, Total P&L, Failed Orders), a live order feed (via WebSocket), an active platform selector, and charts for order visualization.
-   **Orders Page**: Presents a full paginated order history with filters for symbol, platform, and status. It allows expanding rows for detailed order information and provides a retry mechanism for failed orders.
-   **Positions Page**: Shows live open positions fetched from each connected exchange adapter. Each position displays symbol, side, size, entry price, mark price, unrealized P&L, and liquidation price. It includes a manual close button.
-   **Strategies Page**: Lists all loaded strategies with their name, symbol, interval, status, and last signal time. It includes an enable/disable toggle for each strategy.
-   **Settings Page**: Manages the active trading platform, displays (masked) exchange credentials, provides webhook endpoint URLs, and links to service health checks.

##### Responsive Design Rules

-   The UI supports mobile devices with a breakpoint of ≤ 768px.
-   Mobile layouts feature a bottom navigation bar, while desktop layouts use a sidebar.
-   Order tables transform into card lists on mobile.
-   Stat cards stack vertically on mobile.
-   All interactive elements are touch-friendly with minimum tap targets of 44px.

##### Real-time Update Flow

The Dashboard UI uses a custom `useOrderStream` React hook to connect to the Dashboard API's WebSocket server. This hook automatically reconnects after 3 seconds if the connection drops. Real-time events from the Redis Pub/Sub system (forwarded by the Dashboard API) to connected UI clients.

## 5. Data Models

### 5.1 Database Schema (full SQL for all tables)

The PostgreSQL database stores all persistent system data, including orders, strategies, configuration, and audit trails. The schema includes triggers to automatically update `updated_at` timestamps on relevant tables.

```sql
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
    platform          VARCHAR(20) NOT NULL,
    strategy_id       VARCHAR(100) NOT NULL, -- Added NOT NULL in 002 migration
    status            VARCHAR(20) NOT NULL DEFAULT 'received',
    exchange_order_id VARCHAR(100),
    pnl               NUMERIC,
    raw_webhook       JSONB NOT NULL,
    raw_response      JSONB,
    error_msg         TEXT,
    signal_source     VARCHAR(50),           -- Added in 001 migration context
    signal_metadata   JSONB,                 -- Added in 001 migration context
    indicator_price   NUMERIC,               -- Added in 001 migration context
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON orders (received_at DESC);
CREATE INDEX ON orders (status);
CREATE INDEX ON orders (strategy_id);
CREATE INDEX ON orders (platform);

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
    id                     VARCHAR(100) PRIMARY KEY,
    name                   VARCHAR(100) NOT NULL,
    class                  VARCHAR(100) NOT NULL,
    symbol                 VARCHAR(20) NOT NULL,
    interval               VARCHAR(10) NOT NULL,
    platform               VARCHAR(20) NOT NULL DEFAULT 'auto',
    enabled                BOOLEAN NOT NULL DEFAULT TRUE,
    config_yaml            TEXT NOT NULL,
    webhook_secret         VARCHAR(255) NOT NULL, -- Added in 001 migration
    webhook_enabled        BOOLEAN DEFAULT TRUE,  -- Added in 001 migration
    description            TEXT,                  -- Added in 001 migration
    platform_override      VARCHAR(20),           -- Added in 001 migration
    max_daily_signals      INTEGER DEFAULT 500,   -- Added in 001 migration
    max_position_size      NUMERIC DEFAULT 1.0,   -- Added in 002 migration
    max_leverage           INTEGER DEFAULT 10,    -- Added in 002 migration
    max_daily_drawdown_percent NUMERIC DEFAULT 20, -- Added in 002 migration
    capital_allocation_percent NUMERIC DEFAULT 100, -- Added in 002 migration
    signals_today          INTEGER DEFAULT 0,     -- Added in 002 migration
    pnl_today              NUMERIC DEFAULT 0,     -- Added in 002 migration
    pnl_total              NUMERIC DEFAULT 0,     -- Added in 002 migration
    win_count              INTEGER DEFAULT 0,     -- Added in 002 migration
    loss_count             INTEGER DEFAULT 0,     -- Added in 002 migration
    last_signal_at         TIMESTAMPTZ,           -- Added in 002 migration
    tags                   TEXT[] DEFAULT '{}',   -- Added in 002 migration
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    exchange            VARCHAR(20) NOT NULL,
    symbol              VARCHAR(20) NOT NULL,
    side                VARCHAR(10) NOT NULL,
    entry_price         NUMERIC NOT NULL,
    current_price       NUMERIC,
    size                NUMERIC NOT NULL,
    leverage            INTEGER,
    margin_mode         VARCHAR(20),
    pnl_unrealized      NUMERIC,
    pnl_realized        NUMERIC DEFAULT 0,
    status              VARCHAR(20) DEFAULT 'open',
    opening_order_id    UUID REFERENCES orders(id) ON DELETE RESTRICT,
    closing_order_id    UUID REFERENCES orders(id) ON DELETE RESTRICT,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE strategy_stats (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    period_date         DATE NOT NULL,
    trades_count        INTEGER DEFAULT 0,
    trades_won          INTEGER DEFAULT 0,
    trades_lost         INTEGER DEFAULT 0,
    win_rate            NUMERIC,
    pnl_total           NUMERIC DEFAULT 0,
    pnl_avg             NUMERIC,
    max_drawdown        NUMERIC DEFAULT 0,
    capital_deployed    NUMERIC DEFAULT 0,
    leverage_avg        NUMERIC,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period_date)
);

CREATE TABLE strategy_webhook_calls ( -- Added in 001 migration
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     VARCHAR(100) NOT NULL REFERENCES strategies(id),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    http_status     INTEGER,
    error_message   TEXT,
    source_ip       INET
);

-- Triggers for updated_at column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attaching triggers to relevant tables
DO $$
BEGIN
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
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_strategy_webhook_calls_modtime') THEN
        CREATE TRIGGER update_strategy_webhook_calls_modtime BEFORE UPDATE ON strategy_webhook_calls FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
```

### 5.2 Webhook Payload (Pydantic model)

The standard webhook payload format used internally by Order Generator and expected from external sources like TradingView.

```python
class WebhookPayload(BaseModel):
    symbol:      str
    side:        Literal["buy", "sell"]
    orderType:   Literal["market", "limit"] = "market"
    size:        Decimal
    price:       Optional[Decimal] = None
    leverage:    Optional[int] = None
    marginMode:  Optional[Literal["cross", "isolated"]] = "cross"
    tpPrice:     Optional[Decimal] = None
    slPrice:     Optional[Decimal] = None
    platform:    str = "auto"
    strategyId:  Optional[str] = None
    signal:      Literal["open_long", "close_long", "open_short", "close_short"]
    timestamp:   datetime
    token:       str
    signal_source: Optional[str] = "tradingview" # Added in 001 migration context
    signal_metadata: Optional[dict] = {}         # Added in 001 migration context
    indicator_price: Optional[Decimal] = None    # Added in 001 migration context
```

### 5.3 WebSocket Event Format

The WebSocket feed (`ws://host/ws/orders`) broadcasts real-time JSON messages on every order status change.

```json
{
  "event": "order:filled",
  "order_id": "uuid",
  "status": "filled",
  "symbol": "BTC-USDT",
  "platform": "blofin",
  "timestamp": "2026-05-13T10:00:01Z"
}
```

### 5.4 Statistics Response Format

The Dashboard API returns aggregated statistics in a structured format.

```python
class TradingStats(BaseModel):
    period:          str      # "today" | "7d" | "30d" | "all"
    total_orders:    int
    filled:          int
    failed:          int
    win_count:       int      # orders with pnl > 0
    loss_count:      int      # orders with pnl < 0
    win_rate:        float    # percentage
    total_pnl:       Decimal
    avg_pnl:         Decimal
    by_platform:     dict     # { "blofin": {...}, "hyperliquid": {...} }
    by_strategy:     dict     # { strategy_id: {...} }
```

## 6. API Contracts

This section outlines the key API endpoints and their expected request/response structures.

### 6.1 POST /api/listener/webhook

Receives trade signals from Order Generator or external sources.

**Request:**
```json
{
  "symbol": "BTC-USDT",
  "side": "buy",
  "signal": "open_long",
  "orderType": "market",
  "size": "0.01",
  "leverage": 10,
  "marginMode": "cross",
  "platform": "auto",
  "strategyId": "rsi-btc-5m",
  "timestamp": "2026-05-13T10:00:00Z",
  "token": "your-webhook-secret",
  "signal_source": "tradingview",
  "indicator_price": 65000
}
```

**Response 200:**
```json
{ "order_id": "uuid", "status": "received", "message": "OK" }
```

**Response 403:**
```json
{ "detail": "Invalid token" }
```

**Response 422:** Unprocessable Entity — missing or invalid fields.

### 6.2 GET /api/dashboard/orders

Retrieves a paginated list of orders.

**Query Parameters:** `page`, `limit`, `symbol`, `platform`, `status`, `strategy_id`, `from`, `to`

**Response 200:**
```json
{
  "total": 342,
  "page": 1,
  "limit": 50,
  "items": [
    {
      "id": "uuid",
      "received_at": "2026-05-13T10:00:00Z",
      "symbol": "BTC-USDT",
      "side": "buy",
      "signal": "open_long",
      "size": "0.01",
      "platform": "blofin",
      "status": "filled",
      "exchange_order_id": "blofin-123",
      "pnl": "12.50",
      "strategy_id": "rsi-btc-5m",
      "signal_source": "tradingview",
      "indicator_price": 65000
    }
  ]
}
```

### 6.3 GET /api/dashboard/stats

Retrieves aggregated trading statistics.

**Query Parameter:** `period` = `today | 7d | 30d | all`

**Response 200:** See [5.4 Statistics Response Format](#54-statistics-response-format).

### 6.4 GET /api/dashboard/strategies

Lists all configured strategies and their metadata.

**Response 200:**
```json
[
  {
    "id": "rsi-btc-5m",
    "name": "RSI BTC 5m",
    "class": "RsiStrategy",
    "symbol": "BTC-USDT",
    "interval": "5m",
    "platform": "auto",
    "enabled": true,
    "description": "RSI crossover strategy for BTC-USDT on 5m candles.",
    "tags": ["rsi", "momentum"],
    "max_position_size": "1.0",
    "max_leverage": 10,
    "signals_today": 5,
    "pnl_total": "123.45"
  }
]
```

### 6.5 GET /api/dashboard/strategies/:id/stats

Retrieves detailed performance statistics for a single strategy.

**Query Parameter:** `period` = `today | 7d | 30d | all`

**Response 200:**
```json
{
  "strategy_id": "rsi-btc-5m",
  "trades_count": 100,
  "trades_won": 60,
  "win_rate": 60.0,
  "pnl_total": "500.00",
  "pnl_avg": "5.00",
  "max_drawdown": "10.0"
}
```

### 6.6 GET /api/dashboard/strategies/:id/equity-curve

Retrieves time-series data for plotting a strategy's equity curve.

**Response 200:**
```json
[
  {"date": "2026-05-01", "cumulative_pnl": "10.00"},
  {"date": "2026-05-02", "cumulative_pnl": "15.50"},
  // ... more data points
]
```

### 6.7 PUT /api/dashboard/config/active_platform

Updates the system's active trading platform.

**Request:** `{ "platform": "hyperliquid" }`
**Response 200:** `{ "active_platform": "hyperliquid", "updated_at": "..." }`

### 6.8 WebSocket /ws/orders

Client connects to `ws://host/ws/orders`. Receives JSON messages on every order status change. See [5.3 WebSocket Event Format](#53-websocket-event-format).

## 7. Infrastructure

### 7.1 Docker Compose Services

MATP leverages Docker Compose to orchestrate its microservices.

| Service | Technology | Port (internal) | Purpose |
|---------|------------|-----------------|---------|
| `nginx` | Nginx      | 80              | Reverse proxy, static file serving, TLS termination. |
| `postgres` | PostgreSQL 16 | 5432            | Persistent storage for all application data. |
| `redis` | Redis 7    | 6379            | Message bus (Pub/Sub) and caching for configuration. |
| `order-listener` | Python/FastAPI | 8001            | Webhook reception, validation, order routing to exchanges. |
| `order-generator` | Python/FastAPI + APScheduler | 8002            | Executes trading strategies and emits signals. |
| `dashboard-api` | Node.js/Express/TS | 8003            | Backend for the Dashboard UI, REST APIs, WebSocket feed. |
| `dashboard-ui` | React/Vite/Tailwind | 3000            | Frontend web application for monitoring and control. |

### 7.2 Nginx Routing Rules

Nginx routes external HTTP requests to the appropriate internal Docker services. It also handles WebSocket proxying.

```nginx
server {
    listen 80;
    server_name localhost;

    location /api/listener/ {
        proxy_pass http://order-listener:8001/;
    }
    location /api/generator/ {
        proxy_pass http://order-generator:8002/;
    }
    location /api/dashboard/ {
        proxy_pass http://dashboard-api:8003/;
    }
    location /ws/ {
        proxy_pass http://dashboard-api:8003/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    location / {
        proxy_pass http://dashboard-ui:3000/;
    }
}
```

### 7.3 Redis Channels and Keys

Redis is used as a lightweight message bus for real-time event broadcasting and for caching frequently accessed configuration.

| Channel / Key | Purpose |
|---------------|---------|
| `orders:received` | Publishes events when a new webhook is received. |
| `orders:routed` | Publishes events when an order is sent to an exchange. |
| `orders:filled` | Publishes events when an exchange confirms an order fill. |
| `orders:failed` | Publishes events for routing or exchange errors. |
| `config:active_platform` | Caches the currently active trading platform (TTL 5s). |

### 7.4 Environment Variables

The system relies on environment variables for sensitive credentials and configuration. These are typically loaded from a `.env` file during Docker Compose startup.

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes        | Password for the PostgreSQL `matp` user. |
| `WEBHOOK_SECRET` | Yes        | Shared secret for webhook authentication (min 32 chars). |
| `MASTER_KEY` | Yes        | Key for AES-256-GCM encryption of exchange credentials in DB (min 32 chars). |
| `BLOFIN_API_KEY` | Conditional | API key for Blofin exchange. Required if trading on Blofin. |
| `BLOFIN_API_SECRET` | Conditional | API secret for Blofin exchange. Required if trading on Blofin. |
| `HYPERLIQUID_PRIVATE_KEY` | Conditional | ECDSA private key for Hyperliquid exchange. Required if trading on Hyperliquid. |
| `DATA_FEED_EXCHANGE` | No (default: binance) | Exchange to source OHLCV data for the Order Generator. |

### 7.5 Network Isolation Model

PostgreSQL and Redis services are isolated within the internal `matp_net` Docker network and are not directly exposed to the host machine or external networks. Only the Nginx reverse proxy (on port 80/443) is externally accessible, acting as the system's controlled entry point.

## 8. Security

### 8.1 Webhook Authentication (HMAC)

All incoming webhook POST requests must include a `token` field that matches the `WEBHOOK_SECRET`. The system validates this token using `hmac.compare_digest` to prevent timing attacks. Requests with invalid tokens are rejected with a `403` status and logged.

### 8.2 Exchange Credential Storage (AES-256-GCM)

Sensitive exchange API keys and private keys are stored encrypted within the PostgreSQL database. The encryption uses **AES-256-GCM** with a `MASTER_KEY` environment variable. Credentials are never logged, never returned in plaintext via any API, and are masked when displayed in the Dashboard UI.

### 8.3 Network Isolation (Docker internal network)

Critical services like PostgreSQL and Redis are not exposed directly to the host machine or the public internet. They communicate solely over the internal Docker `matp_net` bridge network, minimizing their attack surface.

### 8.4 Input Validation (Pydantic)

All incoming webhook payloads are rigorously validated against Pydantic models in strict mode. This ensures data integrity and prevents malformed data from entering the system. Specific checks include range validation for `size` and `leverage` values to mitigate "fat-finger" errors.

### 8.5 Local Network Deployment Model

The MATP platform is designed for local hosting. Nginx binds to `0.0.0.0` for local area network (LAN) access (e.g., from mobile devices). It is critical not to expose this local deployment to the internet without implementing additional robust authentication mechanisms (e.g., Nginx basic authentication, VPN, or IP whitelisting) to secure it from unauthorized external access.

## 9. Technology Stack

The MATP platform is built upon a modern and asynchronous technology stack to ensure performance, scalability, and maintainability.

| Layer | Technology | Version | Rationale |
|---------------|------------|---------|----------------------------------------------------------|
| Order Generator | Python     | 3.12    | Asynchronous capabilities, rich ecosystem for trading (CCXT), and FastAPI for APIs. |
| Order Listener | Python     | 3.12    | Consistent with generator; excellent async webhook handling with FastAPI. |
| Dashboard API | Node.js    | 20      | Strong WebSocket support, mature libraries for PostgreSQL (`pg`) and Redis. |
| Dashboard UI | React      | 18      | Component-based architecture, efficient UI updates, large community. |
| UI Build Tool | Vite       | N/A     | Fast development server and optimized builds for React. |
| UI Styling | Tailwind CSS | N/A     | Utility-first CSS framework for rapid and consistent styling. |
| Database | PostgreSQL | 16      | Robust, ACID-compliant, supports JSONB for flexible data storage. |
| Message Bus | Redis      | 7       | High-performance in-memory data store for Pub/Sub messaging and caching. |
| Reverse Proxy | Nginx      | N/A     | Production-grade, handles request routing, load balancing, and static content. |
| Market Data | CCXT       | Python Library | Unified API for interacting with over 100 cryptocurrency exchanges. |
| Orchestration | Docker Compose | v2      | Simplifies local multi-container application deployment and management. |
| Exchange Auth | HMAC-SHA256 (Blofin) | N/A     | Exchange-specific authentication protocol. |
| Exchange Auth | ECDSA (Hyperliquid) | N/A     | Exchange-specific cryptographic signature standard. |

## Appendix A — Repository File Index

This index provides a quick reference to key files and their primary function within the MATP repository.

| File Path | Description |
|-----------|-------------|
| `order-listener/app/webhook_handler.py` | Webhook reception, HMAC authentication, async dispatch, dead-letter recording. |
| `order-listener/app/router.py` | Platform routing logic, implements 5-second Redis cache for active platform. |
| `order-listener/app/adapters/blofin.py` | Blofin Signal Bot adapter, handles HMAC-SHA256 signed requests. |
| `order-listener/app/adapters/hyperliquid.py` | Hyperliquid adapter (scaffold), includes ECDSA signing implementation. |
| `order-generator/app/scheduler.py` | APScheduler instance, manages CCXT OHLCV polling and signal emission to listener. |
| `order-generator/app/strategies/rsi_strategy.py` | Implementation of the RSI crossover strategy for signal generation. |
| `order-generator/app/strategies/ma_crossover.py` | Implementation of the moving average crossover strategy. |
| `dashboard-api/src/ws/orderFeed.ts` | Manages Redis subscription and WebSocket broadcast to all UI clients. |
| `dashboard-ui/src/hooks/useOrderStream.ts` | React WebSocket hook with built-in 3-second auto-reconnect functionality. |
| `db/init.sql` | Initial PostgreSQL schema: `orders`, `order_events`, `config`, `strategies`, `dead_letter_orders`, `strategy_positions`, `strategy_stats`, `strategy_webhook_calls` tables, plus `updated_at` triggers. |
| `.github/workflows/ci.yml` | GitHub Actions workflow for linting (Python, Node, UI) and Docker image builds. |
| `docs/tradingview.md` | Provides detailed instructions for TradingView alert setup, JSON format, and signal table. |
| `docs/setup.md` | Comprehensive guide for system installation and development environment setup. |
| `MATP.SDD.md` | This Software Design Document, serving as the master architecture reference. |
| `ACTION_PLAN.md` | Prioritized task list and development roadmap. |
| `TEST_PLAN.md` | Comprehensive test cases and verification checklist. |
| `README.md` | High-level overview and quick start guide for the project. |
| `CHANGELOG.md` | Reverse-chronological history of all technical changes and fixes. |
