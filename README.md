# Modular Automated Trading Platform (MATP)

MATP is a locally-hosted, Docker-based system designed for automated cryptocurrency trading. It provides a modular, strategy-centric architecture for signal generation, automated order routing to exchanges, and real-time monitoring and analytics.

## Key Features

- **Strategy-Centric:** All trading activity (orders, positions, performance) is linked to specific, configurable trading strategies.
- **Automated Order Routing:** Receives trade signals via webhooks, validates them, and routes them to exchanges (Blofin, Hyperliquid).
- **Extensible Architecture:** Easily integrate new strategies or exchange adapters.
- **Real-time Monitoring:** React-based dashboard with WebSocket-driven live feed, performance metrics, and order management.
- **Secure by Design:** HMAC-SHA256 webhook authentication, AES-256-GCM encrypted credential storage, and internal-only service networking.
- **Strategy Management:** Full lifecycle management including enabling/disabling strategies and performance tracking.

## Prerequisites

- **Docker & Docker Compose** (v2+)
- **Node.js** (v20+) and **npm** (for local development)
- **Python** (v3.12+) (for local development)

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url> matp
   cd matp
   ```

2. **Configure environment:**
   Copy the example environment file and set the required variables:
   ```bash
   cp .env.example .env
   # Edit .env and provide your API keys and webhook secrets
   ```

3. **Start the platform:**
   ```bash
   docker compose up -d --build
   ```

## Usage Guide

- **Dashboard:** Access at `http://localhost`.
- **Webhook endpoint:** `POST http://localhost/api/listener/webhook`.
- **TradingView Setup:** Configure your alert URL as `http://<your-ip>/api/listener/webhook` and use the JSON payload format defined in `docs/tradingview.md`.

## Project Status & Roadmap

MATP is under active development. Current focus includes platform stability, refining the strategy performance tracking engine, and adding new exchange adapters. Refer to `docs/process/ACTION_PLAN.md` for the current development roadmap and `docs/MATP.SDD.md` for detailed architectural information.

## Documentation

- [`docs/MATP.SDD.md`](docs/MATP.SDD.md) - Comprehensive Software Design Document.
- [`docs/setup.md`](docs/setup.md) - Detailed setup and installation instructions.
- [`docs/tradingview.md`](docs/tradingview.md) - Guide for setting up TradingView alerts.
- [`docs/sync.md`](docs/sync.md) - Documentation maintenance guide.

---
*Built with React, FastAPI, Node.js, PostgreSQL, and Redis.*
