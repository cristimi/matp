markdown

# Software Design Document (SDD)
## Modular Automated Trading Platform (MATP)

**Version:** 1.1 — Implementation Edition
**Date:** 2026-05-13
**Status:** Active Development — Phase 1 Scaffolded
**Previous:** 1.0 — 2026-05-11 — Master Plan

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Implementation Status](#2-implementation-status) *(new in v1.1)*
3. [System Overview](#3-system-overview)
4. [Architecture](#4-architecture)
5. [Component Specifications](#5-component-specifications)
   - 5.1 [Order Generator Service](#51-order-generator-service)
   - 5.2 [Order Listener Service](#52-order-listener-service)
   - 5.3 [Dashboard Interface](#53-dashboard-interface)
   - 5.4 [Shared Infrastructure](#54-shared-infrastructure)
6. [Data Models](#6-data-models)
7. [API Contracts](#7-api-contracts)
8. [Docker Composition](#8-docker-composition)
9. [Security Considerations](#9-security-considerations)
10. [Development Phases & Roadmap](#10-development-phases--roadmap) *(updated in v1.1)*
11. [Technology Stack Summary](#11-technology-stack-summary)
12. [Testing Plan](#12-testing-plan) *(new in v1.1)*
13. [Next Steps](#13-next-steps) *(new in v1.1)*

---

## 1. Introduction

### 1.1 Purpose

This document provides the complete software design specification for the **Modular Automated Trading Platform (MATP)** — a locally hosted, Docker-based system for automated cryptocurrency trading. It is intended to serve as the master reference document for all development work.

Version 1.1 reflects the state after initial code scaffolding: all services have been generated and committed to GitHub. This update documents what has been built, defines the testing plan, and provides the prioritised next steps to reach production trading.

### 1.2 Scope

The platform covers:
- Automated order generation via configurable trading strategies
- Signal reception, logging, and routing to exchange platforms
- Real-time monitoring, analytics, and order management via a web interface
- Initial integration with **Blofin** (signal bot) and **Hyperliquid**

### 1.3 Definitions

| Term | Definition |
|------|-----------|
| Webhook | HTTP POST payload carrying a trade signal |
| Signal Bot | Blofin's copy-trading signal mechanism |
| Strategy | A module that produces buy/sell signals on a schedule or condition |
| Order Listener | Service that receives, validates, logs, and routes webhooks |
| Active Platform | The default exchange to which orders are routed |
| MATP | Modular Automated Trading Platform (this system) |

### 1.4 Design Goals

- **Modularity**: Each component is independently deployable and replaceable
- **Extensibility**: New strategies and exchange adapters added without touching core logic
- **Observability**: Full audit trail of every signal, routing decision, and order outcome
- **Resilience**: Failed exchange calls do not crash the system; retries and dead-letter queues
- **Mobile-friendly**: Dashboard usable on phone as well as desktop

### 1.5 What Changed in v1.1

| Section | Change |
|---------|--------|
| §2 | **New** — Implementation status table: all 66 files committed to GitHub |
| §10 | **Updated** — Roadmap now shows per-phase build status |
| §12 | **New** — Full testing plan: smoke tests, integration tests, DB checks, CI verification |
| §13 | **New** — Next steps: 33 prioritised tasks across all phases with effort estimates |

---

## 2. Implementation Status

All services have been scaffolded and committed to GitHub (66 files total). The table below shows the current build state of each component.

| Component | Files | Status | Notes |
|-----------|-------|--------|-------|
| **order-listener** | 14 Python files | ✅ Ready | FastAPI, webhook auth, PostgreSQL logging, Blofin adapter, router, orders & config API |
| **order-generator** | 8 Python files | 🟡 Scaffold | APScheduler, RSI & MA strategies, CCXT feed — activate in Phase 5 |
| **dashboard-api** | 9 TypeScript files | ✅ Ready | Node/Express, orders + stats + config + positions routes, Redis WebSocket feed |
| **dashboard-ui** | 15 React/TS files | ✅ Ready | 5 pages (Dashboard, Orders, Positions, Strategies, Settings), mobile nav, dark theme |
| **Infrastructure** | 8 config files | ✅ Complete | docker-compose, Nginx, PostgreSQL schema + triggers, GitHub Actions CI |
| **Hyperliquid Adapter** | 1 Python file | 🟡 Scaffold | Structure in place; ECDSA signing returns placeholder — Phase 3 work |
| **Documentation** | 5 Markdown files | ✅ Complete | README, SDD, setup guide, TradingView guide, docs index |

> **Key insight:** Phases 1 and 5 have all code written. The critical path to production is configuration, credential setup, and live testing — not greenfield development.

---

## 3. System Overview

### 3.1 High-Level Flow

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

### 3.2 Component Summary

| Component | Responsibility | Tech |
|-----------|---------------|------|
| **Order Generator** | Run trading strategies, emit webhook signals | Python, APScheduler |
| **Order Listener** | Receive, validate, log, route webhooks | Python, FastAPI |
| **Dashboard** | Monitor, control, analytics UI | React, Node/Express |
| **Database** | Persistent state for orders, strategies, config | PostgreSQL |
| **Message Bus** | Decouple internal events | Redis Pub/Sub |
| **Reverse Proxy** | TLS termination, single entry point | Nginx |

---

## 4. Architecture

### 4.1 Deployment Architecture

All services run in Docker containers orchestrated with **Docker Compose**. Communication between containers uses the internal Docker bridge network. Only the Nginx reverse proxy exposes external ports.

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

### 4.2 Communication Patterns

| From | To | Method | Notes |
|------|----|--------|-------|
| TradingView | Order Listener | HTTP Webhook (POST) | Authenticated by secret token |
| Order Generator | Order Listener | HTTP Webhook (POST) | Same format as TradingView |
| Order Listener | Blofin Signal Bot | HTTP POST (Blofin API) | HMAC signed |
| Order Listener | Hyperliquid | HTTP POST (HL API) | ECDSA signed |
| Order Listener | Redis | Pub/Sub | Broadcast order events |
| Dashboard API | PostgreSQL | SQL via ORM | Read/write |
| Dashboard API | Redis | Subscribe | Live order feed |
| Dashboard UI | Dashboard API | REST + WebSocket | Real-time updates |

### 4.3 Webhook Format Standard

All internal webhooks — from Order Generator and expected from TradingView — use one unified schema (Blofin-compatible superset):

```json
{
  "symbol":      "BTC-USDT",
  "side":        "buy",
  "orderType":   "market",
  "size":        "0.01",
  "price":       "optional, for limit orders",
  "leverage":    "10",
  "marginMode":  "cross",
  "tpPrice":     "optional",
  "slPrice":     "optional",
  "platform":    "blofin | hyperliquid | auto",
  "strategyId":  "uuid of originating strategy",
  "signal":      "open_long | close_long | open_short | close_short",
  "timestamp":   "ISO8601",
  "token":       "shared-secret for authentication"
}
```

The `platform` field overrides the system-wide default when set. `"auto"` routes to the currently configured active platform.

---

## 5. Component Specifications

### 5.1 Order Generator Service

**Purpose:** Run one or more trading strategies simultaneously. Each strategy produces webhook-format signals dispatched to the Order Listener.

#### 5.1.1 Internal Architecture

```
order-generator/
├── app/
│   ├── main.py              # FastAPI app + scheduler startup
│   ├── scheduler.py         # APScheduler instance, strategy loader
│   ├── strategies/
│   │   ├── base.py          # Abstract Strategy class
│   │   ├── rsi_strategy.py  # RSI crossover
│   │   ├── ma_crossover.py  # Moving average crossover
│   │   └── [user defined]
│   ├── strategies_api.py    # REST API for strategy management
│   └── config.py            # Loads strategy configs from YAML/env
├── strategies_config/       # YAML files defining strategy instances
│   └── example_rsi_btc.yaml
├── Dockerfile
└── requirements.txt
```

#### 5.1.2 Strategy Abstraction

Every strategy inherits from `BaseStrategy`:

```python
class BaseStrategy(ABC):
    strategy_id: str       # UUID, assigned at load time
    name: str
    symbol: str
    interval: str          # "1m", "5m", "1h", etc.
    platform: str          # default platform override or "auto"
    enabled: bool

    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Called on each new OHLCV candle. Returns a Signal or None."""
        pass
```

Each strategy is instantiated from a YAML config file, allowing multiple instances of the same strategy class with different parameters.

**Example YAML:**
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

#### 5.1.3 Data Feed

The generator uses **CCXT** (unified exchange library) to fetch OHLCV data from a configured data source (Binance, Bybit, or the target exchange itself). Data polling is driven by APScheduler tasks aligned to each strategy's interval.

#### 5.1.4 Signal Emission

When a strategy's `on_candle()` returns a `Signal`, the emitter constructs the standard webhook payload and POSTs it to the Order Listener's `/webhook` endpoint over the internal Docker network. Retries with exponential backoff (max 3 attempts).

#### 5.1.5 Management API

The generator exposes a REST API (accessible via the Dashboard via Nginx):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/strategies` | GET | List all loaded strategies and their status |
| `/strategies/{id}/enable` | POST | Enable a strategy |
| `/strategies/{id}/disable` | POST | Disable a strategy |
| `/strategies/{id}/config` | GET | View strategy config and params |
| `/health` | GET | Service health check |

---

### 5.2 Order Listener Service

**Purpose:** Receive webhooks from Order Generator or TradingView, validate, log every signal, and route to the appropriate exchange adapter.

#### 5.2.1 Internal Architecture

```
order-listener/
├── app/
│   ├── main.py             # FastAPI app + lifespan
│   ├── webhook_handler.py  # Receives + validates POST /webhook
│   ├── router.py           # Routing logic: platform selection + Redis cache
│   ├── adapters/
│   │   ├── base.py         # Abstract ExchangeAdapter
│   │   ├── blofin.py       # Blofin Signal Bot integration
│   │   └── hyperliquid.py  # Hyperliquid integration (scaffold)
│   ├── orders_api.py       # GET /orders, GET /orders/{id}, POST /orders/{id}/retry
│   ├── config_api.py       # GET/PUT /config/active_platform
│   ├── config.py           # pydantic-settings: loads from environment
│   ├── database.py         # asyncpg connection pool
│   ├── redis_client.py     # Redis pub/sub client
│   └── models.py           # Pydantic models for webhook payload
├── Dockerfile
└── requirements.txt
```

#### 5.2.2 Webhook Reception & Validation

1. Receive POST at `/webhook`
2. Validate `token` field against `WEBHOOK_SECRET` environment variable (constant-time comparison via `hmac.compare_digest`)
3. Validate schema with Pydantic
4. Reject malformed or unauthenticated payloads with `403` / `422`; log rejection
5. Assign internal `order_id` (UUID) and `received_at` timestamp
6. Write initial record to `orders` table with status `received`
7. Publish to Redis channel `orders:received`
8. Fire-and-forget async task for routing — returns `200 OK` immediately

#### 5.2.3 Routing Logic

```
if webhook.platform == "blofin"      → BlofinAdapter
if webhook.platform == "hyperliquid" → HyperliquidAdapter
if webhook.platform == "auto"        → use active_platform from config table
```

The active platform is read from the database `config` table on each request (cached for 5 seconds with Redis). This means changing the active platform in the Dashboard takes effect within seconds.

#### 5.2.4 Exchange Adapters

**Abstract interface:**
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
- Calls Blofin's Signal Bot REST API
- Authenticates with API key + HMAC-SHA256 signature
- Maps internal signal fields to Blofin's required format
- Handles Blofin-specific error codes (rate limits, insufficient margin, etc.)

**Hyperliquid Adapter:**
- Calls Hyperliquid's REST/WebSocket API
- Authenticates with ECDSA private key (standard HL auth)
- Maps signal to HL's order format (perp trading)
- **Status:** Scaffold only — ECDSA signing not yet implemented (Phase 3)

Both adapters return a standardised `OrderResult`:
```python
class OrderResult:
    success: bool
    exchange_order_id: str
    status: str        # "filled", "pending", "rejected"
    error_msg: str
    raw_response: dict
```

#### 5.2.5 Order Status Lifecycle

```
received → routing → submitted → filled
                              └→ rejected
                   └→ route_failed (exchange adapter error)
```

Status updates are written to PostgreSQL and published to Redis on each transition.

#### 5.2.6 Dead Letter Queue

Failed orders (`route_failed`, `rejected`) are written to a `dead_letter_orders` table with full context. The Dashboard shows these and allows manual retry.

#### 5.2.7 Management API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook` | POST | Receive trade signal |
| `/config` | GET | View system config |
| `/config/active_platform` | PUT | Set active platform |
| `/orders` | GET | List recent orders (paginated, filterable) |
| `/orders/{id}` | GET | Single order detail |
| `/orders/{id}/retry` | POST | Retry a failed/dead-letter order |
| `/health` | GET | Service health check |

---

### 5.3 Dashboard Interface

**Purpose:** Web-based UI for monitoring all orders, statistics, strategy management, and platform configuration. Responsive for desktop and mobile.

#### 5.3.1 Architecture

```
dashboard-api/ (Node.js + Express)
├── src/
│   ├── index.ts
│   ├── routes/
│   │   ├── orders.ts        # Order list, detail, retry
│   │   ├── strategies.ts    # Proxy to Order Generator API
│   │   ├── config.ts        # Active platform, exchange settings
│   │   ├── stats.ts         # Aggregated statistics
│   │   └── positions.ts     # Live open positions from exchanges
│   ├── ws/
│   │   └── orderFeed.ts     # WebSocket: subscribes Redis → pushes to clients
│   ├── db.ts                # PostgreSQL client (pg)
│   └── redis.ts             # Redis subscriber

dashboard-ui/ (React + Vite + Tailwind)
├── src/
│   ├── App.tsx              # Router + sidebar/bottom nav layout
│   ├── api.ts               # Typed fetch wrapper + TypeScript interfaces
│   ├── pages/
│   │   ├── Dashboard.tsx    # Overview: stats + live feed + chart
│   │   ├── Orders.tsx       # Full order history + filters + retry
│   │   ├── Positions.tsx    # Live open positions + manual close
│   │   ├── Strategies.tsx   # Strategy management + enable/disable
│   │   └── Settings.tsx     # Platform config, API keys, health links
│   ├── components/
│   │   ├── Badges.tsx       # StatusBadge, SideBadge, PlatformBadge
│   │   ├── StatPanel.tsx    # Stat card component
│   │   ├── PlatformSelector.tsx
│   │   └── LiveFeed.tsx     # WebSocket event stream
│   └── hooks/
│       └── useOrderStream.ts   # WebSocket hook with 3s auto-reconnect
```

#### 5.3.2 Dashboard Pages

**Dashboard (Overview)**
- Summary stat cards: Total Orders, Win Rate, Total P&L, Failed Orders
- Live order feed (WebSocket — new orders appear in real time)
- Active platform selector (instant apply via PUT /config/active_platform)
- Bar chart: orders by platform (recharts)
- Period filter: today / 7d / 30d / all

**Orders Page**
- Full paginated order history table (desktop) / card list (mobile)
- Columns: Time, Symbol, Side, Signal, Size, Platform, Status, P&L
- Filters: symbol, platform, status
- Row detail expansion: order ID, exchange ID, error message
- Retry button on `route_failed` / `rejected` orders

**Positions Page**
- Live open positions fetched from each connected exchange adapter
- Per-position: Symbol, Side, Size, Entry Price, Mark Price, Unrealized P&L, Liquidation Price
- Manual close button per position (sends close signal through Order Listener)
- Auto-refreshes every 10 seconds

**Strategies Page**
- List of all loaded strategies with: Name, Symbol, Interval, Status, Last Signal Time
- Enable/Disable toggle per strategy (calls generator API in real time)
- Instructions for adding new strategy YAML configs

**Settings Page**
- Active Platform selector (persisted to database)
- Exchange credentials display (masked; update via .env + restart)
- Webhook endpoint URL display
- Health check links for all three backend services

#### 5.3.3 Responsive Design Requirements

- Mobile breakpoint: ≤ 768px
- Bottom navigation bar on mobile (Dashboard, Orders, Positions, Strategies, Settings)
- Sidebar navigation on desktop
- Order table converts to card list on mobile
- Stat cards stack vertically on mobile
- Touch-friendly: minimum tap target 44px

#### 5.3.4 Real-time Updates

The Dashboard API maintains a WebSocket server. When the Redis `orders:*` channel receives a message (published by Order Listener on every status change), it is forwarded to all connected Dashboard clients. The React frontend receives this and updates state without full page reload. The `useOrderStream` hook reconnects automatically after 3 seconds if the connection drops.

---

### 5.4 Shared Infrastructure

#### 5.4.1 PostgreSQL Database Schema

**Table: `orders`**
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
    strategy_id       VARCHAR(100),
    status            VARCHAR(20) NOT NULL DEFAULT 'received',
    exchange_order_id VARCHAR(100),
    pnl               NUMERIC,
    raw_webhook       JSONB NOT NULL,
    raw_response      JSONB,
    error_msg         TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON orders (received_at DESC);
CREATE INDEX ON orders (status);
CREATE INDEX ON orders (strategy_id);
CREATE INDEX ON orders (platform);
```

**Table: `dead_letter_orders`**
```sql
CREATE TABLE dead_letter_orders (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_retry  TIMESTAMPTZ
);
```

**Table: `config`**
```sql
CREATE TABLE config (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Default rows inserted on first run:
-- ('active_platform', 'blofin')
-- ('max_order_size_btc', '1.0')
```

**Table: `strategies`**
```sql
CREATE TABLE strategies (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    class       VARCHAR(100) NOT NULL,
    symbol      VARCHAR(20) NOT NULL,
    interval    VARCHAR(10) NOT NULL,
    platform    VARCHAR(20) NOT NULL DEFAULT 'auto',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    config_yaml TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Table: `order_events`** (audit trail)
```sql
CREATE TABLE order_events (
    id          BIGSERIAL PRIMARY KEY,
    order_id    UUID NOT NULL REFERENCES orders(id),
    event_time  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    message     TEXT
);
```

Auto-update triggers on `orders` and `strategies` keep `updated_at` current automatically.

#### 5.4.2 Redis Usage

| Channel / Key | Purpose |
|---------------|---------|
| `orders:received` | Pub: new webhook received |
| `orders:routed` | Pub: order sent to exchange |
| `orders:filled` | Pub: exchange confirmed fill |
| `orders:failed` | Pub: routing or exchange error |
| `config:active_platform` | Key: cached active platform (TTL 5s) |

#### 5.4.3 Nginx Configuration

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

For LAN access from mobile: Nginx binds to `0.0.0.0`; accessible at the host machine's local IP (e.g. `http://192.168.1.x`).

---

## 6. Data Models

### 6.1 Webhook Payload (Pydantic)

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
```

### 6.2 Signal (Internal)

```python
@dataclass
class Signal:
    side:       str          # "buy" | "sell"
    signal:     str          # "open_long" | "close_long" | ...
    size:       Decimal
    tp_price:   Optional[Decimal]
    sl_price:   Optional[Decimal]
```

### 6.3 Statistics (Dashboard API Response)

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

---

## 7. API Contracts

### 7.1 POST /api/listener/webhook

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
  "token": "your-webhook-secret"
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

### 7.2 GET /api/dashboard/orders

Query params: `page`, `limit`, `symbol`, `platform`, `status`, `strategy_id`, `from`, `to`

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
      "strategy_id": "rsi-btc-5m"
    }
  ]
}
```

### 7.3 GET /api/dashboard/stats

Query param: `period` = `today | 7d | 30d | all`

**Response 200:** See `TradingStats` model above.

### 7.4 PUT /api/dashboard/config/active_platform

**Request:** `{ "platform": "hyperliquid" }`
**Response 200:** `{ "active_platform": "hyperliquid", "updated_at": "..." }`

### 7.5 WebSocket /ws/orders

Client connects to `ws://host/ws/orders`. Receives JSON messages on every order status change:

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

---

## 8. Docker Composition

### 8.1 docker-compose.yml

```yaml
version: "3.9"

networks:
  matp_net:

volumes:
  postgres_data:
  logs:
  strategy_configs:

services:

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [order-listener, order-generator, dashboard-api, dashboard-ui]
    networks: [matp_net]
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: matp
      POSTGRES_USER: matp
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    networks: [matp_net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U matp"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    networks: [matp_net]
    restart: unless-stopped

  order-listener:
    build: ./order-listener
    environment:
      DATABASE_URL: postgresql://matp:${POSTGRES_PASSWORD}@postgres:5432/matp
      REDIS_URL: redis://redis:6379
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
      MASTER_KEY: ${MASTER_KEY}
      BLOFIN_API_KEY: ${BLOFIN_API_KEY}
      BLOFIN_API_SECRET: ${BLOFIN_API_SECRET}
      HYPERLIQUID_PRIVATE_KEY: ${HYPERLIQUID_PRIVATE_KEY}
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    networks: [matp_net]
    restart: unless-stopped

  order-generator:
    build: ./order-generator
    environment:
      DATABASE_URL: postgresql://matp:${POSTGRES_PASSWORD}@postgres:5432/matp
      LISTENER_WEBHOOK_URL: http://order-listener:8001/webhook
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
      DATA_FEED_EXCHANGE: ${DATA_FEED_EXCHANGE:-binance}
    volumes:
      - strategy_configs:/app/strategies_config
    depends_on: [order-listener, postgres]
    networks: [matp_net]
    restart: unless-stopped

  dashboard-api:
    build: ./dashboard-api
    environment:
      DATABASE_URL: postgresql://matp:${POSTGRES_PASSWORD}@postgres:5432/matp
      REDIS_URL: redis://redis:6379
      GENERATOR_URL: http://order-generator:8002
      LISTENER_URL: http://order-listener:8001
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    networks: [matp_net]
    restart: unless-stopped

  dashboard-ui:
    build: ./dashboard-ui
    environment:
      VITE_API_BASE: /api/dashboard
      VITE_WS_URL: /ws/orders
    networks: [matp_net]
    restart: unless-stopped
```

### 8.2 Environment Variables (.env)

```
POSTGRES_PASSWORD=changeme
WEBHOOK_SECRET=changeme-long-random-string-min-32-chars
MASTER_KEY=changeme-another-long-random-string-32chars
BLOFIN_API_KEY=
BLOFIN_API_SECRET=
HYPERLIQUID_PRIVATE_KEY=
DATA_FEED_EXCHANGE=binance
```

Generate secrets: `openssl rand -hex 32`

---

## 9. Security Considerations

### 9.1 Webhook Authentication

Every webhook POST must include a `token` field matching `WEBHOOK_SECRET`. The comparison uses `hmac.compare_digest` to prevent timing attacks. Unauthenticated requests are rejected with `403` and logged.

For TradingView alerts, set the token in the alert message JSON. For internal (Generator → Listener) calls, the token is injected from the shared environment variable.

### 9.2 Credential Storage

Exchange API keys and private keys are stored in the `config` database table with **AES-256-GCM encryption** using a `MASTER_KEY` environment variable. They are never logged, never returned in plain text via any API, and masked in the Dashboard UI.

### 9.3 Network Isolation

PostgreSQL and Redis are not exposed to the host; they only exist on the internal `matp_net` Docker network. Only Nginx (port 80) is externally accessible.

### 9.4 Local Network Only

For local hosting, Nginx should be bound to the local machine only. If LAN access is needed (e.g., mobile on the same Wi-Fi), bind to the local IP — never expose to the internet without additional authentication (basic auth on Nginx at minimum, VPN recommended).

### 9.5 Input Validation

All webhook payloads are validated with Pydantic strict mode. Size and leverage values are range-checked to avoid fat-finger errors. A configurable `MAX_ORDER_SIZE` guard can be set per symbol.

---

## 10. Development Phases & Roadmap

### Updated Status (v1.1)

| Phase | Timeline | Deliverable | Status |
|-------|----------|-------------|--------|
| **1** | Weeks 1–2 | TradingView alerts → Order Listener → PostgreSQL → Blofin. All code scaffolded. Awaiting live credential test and smoke tests. | 🟡 Code Done |
| **2** | Weeks 3–4 | Dashboard MVP: orders UI, live feed, stats, platform selector. All UI pages built. Needs live data validation. | 🟡 UI Built |
| **3** | Weeks 5–6 | Hyperliquid adapter: ECDSA signing, place/get positions/close. Scaffold exists; implementation and testing needed. | ⬜ Pending |
| **4** | Weeks 7–8 | Production hardening: positions page wired, manual close, retry UI, pytest suite, HTTPS, dashboard auth. | ⬜ Pending |
| **5** | Weeks 9–11 | Strategy engine live: RSI and MA strategies running, CCXT feed active, strategy management UI connected. | 🟡 Code Done |

### Phase 1 — Core Infrastructure (Weeks 1–2)

- Docker Compose scaffold: postgres, redis, nginx containers up
- Database schema and migrations
- Order Listener: webhook endpoint, validation, PostgreSQL logging
- Basic Blofin Signal Bot adapter (place order only)
- Manual webhook test via curl/Postman
- TradingView alert format validation

**Deliverable:** TradingView alerts received, logged to DB, and forwarded to Blofin.

### Phase 2 — Dashboard MVP (Weeks 3–4)

- Dashboard API: orders endpoint, stats endpoint
- Dashboard UI: Orders page, Stats cards
- WebSocket live feed
- Active platform selector wired to DB config
- Settings page: Blofin API key management

**Deliverable:** Web UI showing all incoming TradingView orders, win/loss stats, live updates. Blofin trading fully observable and controllable from the dashboard.

### Phase 3 — Hyperliquid Integration (Weeks 5–6)

- Hyperliquid adapter (ECDSA auth, perp order format)
- Settings page: Hyperliquid private key management
- Dual-platform routing tested end-to-end with TradingView signals
- Platform switcher verified: same TradingView alert routes correctly to either exchange

**Deliverable:** TradingView → Blofin and TradingView → Hyperliquid both working. Active platform switchable from Dashboard in real time.

### Phase 4 — Full Controls & Polish (Weeks 7–8)

- Positions page (live open positions from both exchanges)
- Manual close position via UI
- Dead letter queue + retry UI for failed orders
- Mobile UI polish and testing
- Order size guard rails
- Logging improvements (structured logs, log viewer in UI)
- pytest suite: >80% coverage on order-listener
- HTTPS (self-signed cert for LAN)
- Dashboard basic auth

**Deliverable:** Production-ready system for live TradingView-driven trading on both platforms, fully monitored and controlled from desktop and mobile.

### Phase 5 — Order Generator / Strategy Engine (Weeks 9–11)

> **Prerequisites:** Phases 1–4 complete and stable. Both exchange adapters proven in live trading.

- Base strategy class and APScheduler scaffold *(complete)*
- CCXT data feed integration (OHLCV polling) *(complete)*
- First live strategy: RSI crossover *(complete)*
- MA Crossover strategy *(complete)*
- Generator → Listener internal webhook calls *(complete)*
- Strategy config YAML loader *(complete)*
- Strategy management page in Dashboard *(complete — needs live activation)*

**Deliverable:** Automated strategies running alongside TradingView alerts, using the same proven routing and exchange infrastructure.

### Future Enhancements (Backlog)

- Additional exchange adapters (Bybit, OKX, Binance Futures)
- Backtesting runner for strategies
- Position sizing / risk management module
- Telegram / Discord alerts on order fills or failures
- HTTPS with self-signed cert for LAN access
- Authentication layer for Dashboard (local login)
- Strategy performance analytics with equity curve chart
- Kubernetes migration for multi-host or cloud deployment

---

## 11. Technology Stack Summary

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Order Generator | Python 3.12 + FastAPI + APScheduler | Async, fast, CCXT integration |
| Order Listener | Python 3.12 + FastAPI | Consistent with generator; excellent async webhook handling |
| Dashboard API | Node.js 20 + Express + TypeScript | Strong WebSocket support, good pg/redis libraries |
| Dashboard UI | React 18 + Vite + Tailwind CSS | Fast builds, responsive design, component ecosystem |
| Database | PostgreSQL 16 | Reliable, JSONB for raw payloads, full query capability |
| Message Bus | Redis 7 (Pub/Sub) | Low latency, simple; not persisted (DB is the source of truth) |
| Reverse Proxy | Nginx | Production-grade, WebSocket upgrade, static file serving |
| Market Data | CCXT (Python) | Unified API for 100+ exchanges |
| Container Orchestration | Docker Compose v2 | Simple, local, no Kubernetes overhead |
| Exchange Auth | HMAC-SHA256 (Blofin), ECDSA (Hyperliquid) | Exchange-required authentication methods |

---

## 12. Testing Plan

This section defines everything to verify before progressing through each phase. Run tests in order: prerequisites first, then smoke tests, then integration tests.

### 12.1 Prerequisites — Stack Startup

| # | Action | Command | Expected |
|---|--------|---------|----------|
| 1 | Copy & edit env | `cp .env.example .env` then edit with real credentials | `.env` present with no empty required values |
| 2 | Build & start all | `docker compose up -d --build` | All 7 containers show "running" |
| 3 | Check container status | `docker compose ps` | State: running for all services |
| 4 | Check startup logs | `docker compose logs --tail=30` | No ERROR or CRITICAL lines; pool initialized messages visible |
| 5 | Verify DB schema | `docker compose exec postgres psql -U matp -d matp -c "\dt"` | Tables: orders, order_events, config, strategies, dead_letter_orders |

### 12.2 Smoke Tests — Health Endpoints

All should return HTTP 200. Run from the host machine or mobile on the same network.

| # | URL | Expected Response | Priority |
|---|-----|-------------------|----------|
| 1 | GET /api/listener/health | `{"status":"ok","service":"order-listener"}` | Must pass |
| 2 | GET /api/generator/health | `{"status":"ok","service":"order-generator"}` | Must pass |
| 3 | GET /api/dashboard/health | `{"status":"ok","service":"dashboard-api"}` | Must pass |
| 4 | GET / (browser) | React dashboard renders with nav and stat cards | Must pass |
| 5 | GET /api/dashboard/stats | JSON: `{period, total_orders, win_rate, ...}` | Must pass |
| 6 | GET /api/dashboard/orders | JSON: `{total, page, items:[]}` | Must pass |
| 7 | GET /api/dashboard/config | JSON with active_platform key | Must pass |
| 8 | GET /api/generator/strategies | JSON array (empty if no YAML configs enabled) | Must pass |

### 12.3 Webhook Integration Tests

The most critical tests. Verify the complete path: HTTP → validation → DB → exchange adapter → status update → WebSocket → UI.

#### Test A — Valid webhook received and logged

```bash
curl -X POST http://localhost/api/listener/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"BTC-USDT","side":"buy","signal":"open_long",
    "orderType":"market","size":"0.001","leverage":10,
    "platform":"auto","strategyId":"test-001",
    "timestamp":"2026-05-13T10:00:00Z","token":"YOUR_SECRET"
  }'
```

**Verify each step:**
- HTTP 200 returned with `{"order_id":"<uuid>","status":"received","message":"OK"}`
- Record in DB: `docker compose exec postgres psql -U matp -d matp -c "SELECT symbol, status FROM orders ORDER BY received_at DESC LIMIT 1;"`
- Status transitions visible in `order_events` table
- Order appears in Dashboard UI → Orders page within a few seconds
- Live Feed on Dashboard page shows the event (green dot, WebSocket connected)

#### Test B — Invalid token rejected

```bash
curl -X POST http://localhost/api/listener/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTC-USDT","side":"buy","signal":"open_long","size":"0.001",
       "timestamp":"2026-05-13T10:00:00Z","token":"WRONG_SECRET"}'
```

**Expected:** HTTP 403 — `{"detail":"Invalid token"}`. No record created in orders table.

#### Test C — Malformed payload rejected

```bash
curl -X POST http://localhost/api/listener/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTC-USDT","token":"YOUR_SECRET"}'
```

**Expected:** HTTP 422 Unprocessable Entity — missing required fields (side, signal, size, timestamp).

#### Test D — Active platform switching

```bash
curl -X PUT http://localhost/api/listener/config/active_platform \
  -H "Content-Type: application/json" -d '{"platform":"hyperliquid"}'
```

**Expected:** HTTP 200. Send another `"auto"` webhook — it should now route to Hyperliquid. Dashboard Settings should reflect the change within 5 seconds (Redis cache TTL).

#### Test E — Dead letter and retry

Temporarily set a bad `BLOFIN_API_KEY`, send a webhook with `platform: "blofin"`, observe `route_failed` status in Orders page. Then click Retry. Restore the real key and retry again to confirm it goes through.

### 12.4 Dashboard UI Manual Tests

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 1 | Dashboard loads | Open http://localhost in browser | Stat cards, Live Feed panel, and chart all visible |
| 2 | Live feed updates in real time | Send Test A webhook while Dashboard page is open | New event appears in Live Feed within 2 seconds, no refresh needed |
| 3 | Orders page renders | Click Orders in sidebar | Table shows order from Test A with correct symbol, status, platform |
| 4 | Row expand detail | Click any row in Orders table | Detail row shows order ID, exchange ID (if filled), error message (if failed) |
| 5 | Orders filter by status | Orders page → status dropdown → select route_failed | Only failed orders shown; count updates |
| 6 | Platform switch in Settings | Settings → click Hyperliquid → Save | Success indicator shown; GET /config returns hyperliquid as active |
| 7 | Stats period switching | Dashboard → click 7d, 30d, all buttons | Stat card numbers change to match selected period |
| 8 | Mobile layout (375px) | Open DevTools → set 375px width, or use phone on same Wi-Fi | Bottom nav bar visible; orders rendered as cards not table; tap targets comfortable |
| 9 | WebSocket auto-reconnect | `docker compose restart order-listener` | Live Feed shows grey dot briefly then reconnects; green dot returns without page reload |
| 10 | Strategies page | Click Strategies in nav | Page loads; message shown if no strategies enabled; toggle controls visible if any loaded |

### 12.5 Database Integrity Checks

Connect via: `docker compose exec postgres psql -U matp -d matp`

| # | Query | Expected |
|---|-------|----------|
| 1 | `SELECT COUNT(*) FROM orders;` | Matches total shown in Dashboard stat card |
| 2 | `SELECT from_status, to_status, message FROM order_events ORDER BY event_time DESC LIMIT 10;` | Audit trail entries: NULL→received, received→routing, routing→filled (or route_failed) |
| 3 | `SELECT key, value FROM config;` | active_platform row present; encrypted credential rows present if set via env |
| 4 | `SELECT * FROM dead_letter_orders;` | Empty if all orders succeeded; populated if any routing errors occurred |
| 5 | `SELECT status, COUNT(*) FROM orders GROUP BY status;` | Breakdown of order statuses matching dashboard stats |

### 12.6 Blofin Live Integration Test

> **Only run after setting real Blofin API credentials. Use the smallest possible order size.**

- Set `BLOFIN_API_KEY` and `BLOFIN_API_SECRET` in `.env` with real credentials
- Run: `docker compose up -d --build order-listener`
- Send a test webhook with `size: "0.001"` (minimum BTC) and `platform: "blofin"`
- Verify order appears in Blofin dashboard with filled status
- Verify `exchange_order_id` is populated in the MATP orders table
- **⚠️ Warning:** Use a testnet or paper trading account if Blofin offers one before placing real orders

### 12.7 CI Pipeline Verification

| # | Job | Trigger | Expected | Blocks |
|---|-----|---------|----------|--------|
| 1 | lint-python (order-listener) | Push / PR to main | Ruff: 0 errors | docker-build |
| 2 | lint-python (order-generator) | Push / PR to main | Ruff: 0 errors | docker-build |
| 3 | lint-node (dashboard-api) | Push / PR to main | tsc exits 0 | docker-build |
| 4 | lint-ui (dashboard-ui) | Push / PR to main | Vite build succeeds | docker-build |
| 5 | docker-build | After all lint jobs | All images build in parallel | merge |

---

## 13. Next Steps

33 prioritised tasks across all phases. Complete Phase 1 validation before any live trading. Effort is estimated as developer time excluding waiting.

### 13.1 Immediate — Phase 1 Completion

**Goal:** First live TradingView signal reaches Blofin successfully.

| # | Task | Detail | Effort | Status |
|---|------|--------|--------|--------|
| 1 | Configure `.env` with real credentials | Set POSTGRES_PASSWORD, WEBHOOK_SECRET, MASTER_KEY, BLOFIN_API_KEY, BLOFIN_API_SECRET. Generate with: `openssl rand -hex 32` | 15 min | TODO |
| 2 | Run docker compose up and startup tests | Follow §12.1 — all 7 containers must show "running"; no ERROR lines in logs | 30 min | TODO |
| 3 | Run smoke tests §12.2 | All 8 health endpoint checks must return 200 | 20 min | TODO |
| 4 | Webhook integration tests A, B, C | Use curl commands from §12.3 — verify auth, logging, rejection paths all work correctly | 30 min | TODO |
| 5 | Verify DB audit trail | Run §12.5 queries — confirm orders and order_events populate correctly on each status transition | 15 min | TODO |
| 6 | Fix: confirm pydantic-settings in order-listener | `config.py` imports `BaseSettings` from `pydantic_settings` — already added to requirements.txt; confirm build succeeds | 5 min | Done |
| 7 | Blofin live order test | Follow §12.6 — place smallest possible order, verify exchange_order_id returned and recorded | 1 hr | TODO |
| 8 | Expose webhook for TradingView | Install Tailscale or run `ngrok http 80`; paste the public URL into TradingView alert webhook field | 1 hr | TODO |
| 9 | Configure first TradingView alert | Use JSON from `docs/tradingview.md`; send a test alert from TV; confirm it appears in Dashboard Orders page | 30 min | TODO |

### 13.2 Phase 2 — Dashboard Data Validation

**Goal:** Dashboard shows accurate live data from real orders.

| # | Task | Detail | Effort | Status |
|---|------|--------|--------|--------|
| 10 | Run full UI tests §12.4 | All 10 UI manual tests; document any rendering or data issues | 1 hr | TODO |
| 11 | Validate stats SQL | GROUPING SETS query in stats.ts — confirm by_platform and by_strategy aggregate correctly against real order data | 1 hr | TODO |
| 12 | Map Blofin PnL to orders table | Blofin fill response contains realized PnL — extract it in blofin.py and set result OrderResult with pnl field; update orders table | 1 hr | TODO |
| 13 | Replace bar chart with volume timeline | Dashboard page: use recharts LineChart to show order count per day over last 7 days instead of platform breakdown bar | 2 hrs | TODO |
| 14 | Error toast on route_failed | When WebSocket receives order:route_failed event, show a red toast notification in the UI | 1 hr | TODO |
| 15 | Credential update UI in Settings | Settings page form to update API keys via API rather than requiring .env edit + container restart | 3 hrs | TODO |

### 13.3 Phase 3 — Hyperliquid Adapter

**Goal:** Same TradingView alert routes to either exchange via the platform selector.

| # | Task | Detail | Effort | Status |
|---|------|--------|--------|--------|
| 16 | Add eth-account to requirements.txt | `pip install eth-account` — needed for ECDSA private key signing in hyperliquid.py | 5 min | TODO |
| 17 | Fetch asset index from HL /info endpoint | GET /info with type=meta returns list of assets; build symbol→index map for order construction | 1 hr | TODO |
| 18 | Implement HL order signing | Construct L1 action, sign with ECDSA private key using eth-account, POST to /exchange. Ref: hyperliquid.gitbook.io | 4 hrs | TODO |
| 19 | Implement get_open_positions for HL | POST /info with type=clearinghouseState and wallet address; parse assetPositions list | 1 hr | TODO |
| 20 | Implement close_position for HL | Send market order with reduceOnly=true; or use HL closePosition action | 2 hrs | TODO |
| 21 | End-to-end test HL routing | Set active_platform=hyperliquid, send test webhook, verify order appears in HL with exchange_order_id in MATP DB | 1 hr | TODO |
| 22 | Platform switch test | Alternate webhooks between Blofin and HL via dashboard selector; confirm each routes correctly | 30 min | TODO |

### 13.4 Phase 4 — Production Hardening

**Goal:** System is safe, observable, and testable for regular live use.

| # | Task | Detail | Effort | Status |
|---|------|--------|--------|--------|
| 23 | Wire Positions page to adapters | Implement /positions endpoint in order-listener that calls adapter.get_open_positions() for active exchange | 2 hrs | TODO |
| 24 | Manual close button end-to-end | Positions page Close → dashboard-api → order-listener → adapter.close_position(); verify position closed on exchange | 2 hrs | TODO |
| 25 | Dead letter retry end-to-end | Force a route_failed order (bad API key), see it in Orders page, click Retry, verify it routes after key is restored | 1 hr | TODO |
| 26 | Max order size guard | Add MAX_ORDER_SIZE env var per symbol; reject oversized orders in webhook_handler.py with 400 and log to dead_letter_orders | 1 hr | TODO |
| 27 | Add pytest suite | Tests for: token validation, Pydantic model, RSI logic, router platform selection. Target 80%+ coverage on order-listener | 4 hrs | TODO |
| 28 | HTTPS for LAN access | Generate self-signed cert; add ssl_certificate block to nginx.conf — removes browser security warnings on mobile | 1 hr | TODO |
| 29 | Dashboard basic auth | Add Nginx auth_basic block to protect the dashboard from others on the LAN | 1 hr | TODO |

### 13.5 Phase 5 — Strategy Engine

**Goal:** Automated strategies run alongside TradingView alerts using the same proven infrastructure.

| # | Task | Detail | Effort | Status |
|---|------|--------|--------|--------|
| 30 | Enable RSI strategy | Copy example_rsi_btc.yaml, set enabled: true, mount into strategies_config volume, restart order-generator | 15 min | TODO |
| 31 | Verify CCXT data feed | Check logs for successful OHLCV fetches from Binance; confirm candle data reaches strategy.on_candle() | 30 min | TODO |
| 32 | Test internal webhook path | When RSI fires a signal, verify it appears in DB with strategyId populated and same routing as TradingView signals | 1 hr | TODO |
| 33 | Strategy enable/disable round-trip | Toggle strategy off in Dashboard, confirm scheduler job removed in logs; re-enable, confirm signals resume | 30 min | TODO |

### 13.6 Future Backlog

- Backtesting runner — replay historical OHLCV through strategies without placing real orders
- Telegram / Discord notifications on order fill or route failure
- Additional exchange adapters: Bybit, OKX, Binance Futures
- Position sizing / risk management module (% of portfolio per trade)
- Strategy performance analytics with equity curve chart
- Kubernetes migration for multi-host or cloud deployment

---

## Appendix A — Repository File Index

All files committed to GitHub. Key paths:

| File Path | Description |
|-----------|-------------|
| `order-listener/app/webhook_handler.py` | Webhook reception, HMAC auth, async dispatch, dead-letter write |
| `order-listener/app/router.py` | Platform routing with 5s Redis cache for active_platform |
| `order-listener/app/adapters/blofin.py` | Blofin Signal Bot adapter — HMAC-SHA256 signed requests |
| `order-listener/app/adapters/hyperliquid.py` | Hyperliquid adapter scaffold — ECDSA signing to complete in Phase 3 |
| `order-generator/app/scheduler.py` | APScheduler + CCXT OHLCV polling + signal emission to listener |
| `order-generator/app/strategies/rsi_strategy.py` | RSI crossover strategy — oversold/overbought signal generation |
| `order-generator/app/strategies/ma_crossover.py` | Moving average crossover strategy |
| `dashboard-api/src/ws/orderFeed.ts` | Redis subscription → WebSocket broadcast to all UI clients |
| `dashboard-ui/src/hooks/useOrderStream.ts` | React WebSocket hook with 3s auto-reconnect |
| `db/init.sql` | Full schema: orders, order_events, config, strategies, dead_letter_orders + triggers |
| `.github/workflows/ci.yml` | GitHub Actions: lint-python, lint-node, lint-ui, docker-build |
| `docs/tradingview.md` | TradingView alert JSON format, signal table, curl test commands |
| `docs/setup.md` | Full installation and development guide |

---

*End of Software Design Document — MATP v1.1 — 2026-05-13*
