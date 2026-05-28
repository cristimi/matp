> **Note:** Testing plan, action items, and development roadmap have been
> moved to ACTION_PLAN.md and TEST_PLAN.md. This document covers architecture only.

# MATP — Software Design Document
**Version:** 3.0  **Date:** 2026-05-28  **Status:** Active Development

### Changelog from v2.0
- Added `order-executor` service (§4.3) as the single dedicated component for all exchange communication.
- Removed exchange adapters from `order-listener` and `order-generator`; both now delegate to `order-executor` via internal HTTP.
- Replaced static per-exchange environment variables with a dynamic `exchange_accounts` table supporting multiple named accounts per exchange and per mode (live/demo).
- Added `AccountRegistry` pattern for in-memory adapter instance caching.
- Updated `strategies` table to reference `account_id` instead of `platform`.
- Updated all affected component specs, data models, API contracts, infrastructure, and security sections.

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
- Signal reception, validation, logging, and routing to the Order Executor.
- Centralised exchange communication via a dedicated Order Executor service.
- Dynamic, multi-account exchange management supporting multiple accounts per exchange and per mode (live/demo).
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
| MATP | Modular Automated Trading Platform (this system). |

### 1.4 Design Goals

- **Modularity**: Each component is independently deployable and replaceable.
- **Extensibility**: New strategies and exchange adapters integrate without touching core logic. Adding a new exchange requires one new adapter class and one registry entry.
- **Single Responsibility for Exchange I/O**: Only the Order Executor communicates with exchanges. No other service holds exchange credentials or calls exchange APIs directly.
- **Dynamic Account Management**: Multiple accounts per exchange, per mode, can run simultaneously. Account configuration lives in the database, not in environment variables.
- **Observability**: The system maintains a full audit trail of every signal, routing decision, and order outcome.
- **Resilience**: Failed exchange calls do not crash the system; the executor handles retries and dead-letter recording.
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
└────────────────────────┘   │    validator & logger)   │
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
| **Order Listener** | Receives, validates, logs, and forwards signals to Order Executor. | Python, FastAPI |
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
│                    ├── /api/executor  → order-executor:8004  (internal only — no external route)
│                    ├── /api/dashboard → dashboard-api:8003
│                    └── /             → dashboard-ui:3000
│
├── Internal network: matp_net
│   ├── order-listener    (Python/FastAPI, port 8001)
│   ├── order-generator   (Python/FastAPI, port 8002)
│   ├── dashboard-api     (Node/Express, port 8003)
│   ├── order-executor    (Python/FastAPI, port 8004)  ← new
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

Each strategy now also references a specific **account**, which determines which exchange and which credentials are used for execution. Multiple strategies can share an account; one strategy maps to exactly one account.

### 3.2 Strategy Lifecycle (signal → order → position → stats)

1. **Signal Generation**: A strategy (from Order Generator or TradingView) emits a signal including a `strategy_id`.
2. **Order Creation**: The Order Listener receives the signal, logs it as an `order` with `status = received`, and dispatches it synchronously to the Order Executor.
3. **Exchange Execution**: The Order Executor resolves the `account_id` from the strategy, loads (or retrieves from cache) the correct adapter instance, and submits the trade to the exchange.
4. **Position Management**: On successful execution, a `strategy_positions` record is created linking the position to `strategy_id` and `opening_order_id`.
5. **Performance Tracking**: As positions open and close, `strategy_stats` and `strategy_performance` tables aggregate metrics per strategy.

### 3.3 Strategy Data Model Overview

The `strategies` table is the central hub. All orders, positions, and performance metrics reference a `strategy_id`. Each strategy also carries an `account_id` foreign key into `exchange_accounts`, which determines the execution target.

---

## 4. Component Specifications

### 4.1 Order Listener (Python/FastAPI)

The Order Listener receives, validates, and logs every incoming signal. It no longer contains exchange adapter logic — all execution is delegated to the Order Executor.

#### Internal Architecture

```
order-listener/
├── app/
│   ├── main.py             # FastAPI application and lifespan events
│   ├── webhook_handler.py  # Reception, HMAC validation, DB write, executor dispatch
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
5. Assigns internal `order_id` (UUID) and `received_at` timestamp.
6. Writes initial record to `orders` with `status = 'received'`.
7. Publishes to Redis `orders:received`.
8. Calls `executor_client.execute(order_request)` **synchronously** (awaits the result).
9. Updates the order record with the executor's result (`filled` / `route_failed` / `lag_failed`).
10. Returns `200 OK` to the caller only after the executor response is received.

> **Why synchronous?** The lag-fail / route-fail classification requires knowing the execution outcome before responding. TradingView does not use the response body but does require a timely `200`; the executor call over the internal Docker network adds negligible latency.

#### Routing Logic

The Order Listener no longer selects an exchange. It resolves the `account_id` for the incoming `strategy_id` from the database (cached in Redis for 5 seconds) and includes it in the `OrderRequest` sent to the executor. The executor owns all routing.

#### Order Status Lifecycle

```
received → dispatched → submitted → filled
                                 └→ rejected
              └→ route_failed  (executor could not reach exchange)
              └→ lag_failed    (signal arrived after acceptable window)
```

Status transitions are written to `order_events` and published to Redis on each change.

#### Dead Letter Queue

Orders with terminal failure statuses (`route_failed`, `rejected`) are recorded in `dead_letter_orders` with full context for manual review and retry.

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

The Order Generator runs trading strategies and emits signals. Like the listener, it does not communicate with exchanges directly — it sends signals to the Order Listener, which routes them through the executor.

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

Note: `platform` has been replaced by `account_id`. The strategy knows *which account* to trade, not which exchange type.

#### Signal Emission

When `on_candle()` returns a `Signal`, the generator constructs a webhook payload and POSTs it to `order-listener:8001/webhook/{strategy_id}` over the internal network, with retry/backoff (up to 3 attempts).

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

The Order Executor is the **single gateway for all exchange communication** in MATP. No other service holds exchange credentials or calls exchange APIs. It manages a registry of live adapter instances, one per active account, loaded on demand from the database.

#### Internal Architecture

```
order-executor/
├── app/
│   ├── main.py               # FastAPI application and registry startup
│   ├── executor.py           # Main execute() handler — resolves account, calls adapter
│   ├── registry.py           # AccountRegistry: in-memory adapter instance cache
│   ├── adapters/
│   │   ├── base.py           # Abstract ExchangeAdapter interface
│   │   ├── blofin.py         # Blofin adapter (stateless, credential-aware)
│   │   └── hyperliquid.py    # Hyperliquid adapter (stateless, credential-aware)
│   ├── credentials.py        # AES-256-GCM decrypt helper
│   ├── database.py           # asyncpg pool for account record reads
│   ├── redis_client.py       # Redis pub/sub client for result events
│   └── models.py             # OrderRequest, OrderResult Pydantic models
├── Dockerfile
└── requirements.txt
```

#### ExchangeAdapter Interface

All adapters implement a common abstract interface. Adapter instances are **stateless except for their injected credentials** — all mutable state lives in the database.

```python
class ExchangeAdapter(ABC):
    def __init__(self, credentials: dict, mode: str):
        """
        credentials: decrypted dict (keys vary by exchange)
        mode: "live" | "demo"
        """
        pass

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> OrderResult:
        pass

    @abstractmethod
    async def close_position(self, symbol: str, side: str) -> OrderResult:
        pass

    @abstractmethod
    async def get_open_positions(self) -> list[Position]:
        pass
```

#### AccountRegistry

The `AccountRegistry` maintains one adapter instance per `account_id`, loaded lazily on first use. Credentials are decrypted once and held in memory for the lifetime of the instance.

```python
class AccountRegistry:
    def __init__(self):
        self._instances: dict[str, ExchangeAdapter] = {}

    async def get(self, account_id: str) -> ExchangeAdapter:
        if account_id not in self._instances:
            self._instances[account_id] = await self._load(account_id)
        return self._instances[account_id]
