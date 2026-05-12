# Modular Automated Trading Platform (MATP)

A locally-hosted, Docker-based system for automated cryptocurrency trading.

## Architecture

```
TradingView Alerts ──┐
                     ├──▶ Order Listener ──▶ Blofin Signal Bot
Order Generator ─────┘    (webhook router)──▶ Hyperliquid
                               │
                           PostgreSQL
                               │
                          Dashboard UI
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| `nginx` | 80 | Reverse proxy — single entry point |
| `order-listener` | 8001 (internal) | Webhook receiver & exchange router |
| `order-generator` | 8002 (internal) | Strategy engine |
| `dashboard-api` | 8003 (internal) | REST + WebSocket API |
| `dashboard-ui` | 3000 (internal) | React frontend |
| `postgres` | 5432 (internal) | Database |
| `redis` | 6379 (internal) | Pub/Sub message bus |

## Quick Start

### 1. Clone & configure

```bash
git clone <your-repo-url> matp
cd matp
cp .env.example .env
# Edit .env with your credentials
```

### 2. Start all services

```bash
docker compose up -d --build
```

### 3. Access

- **Dashboard:** http://localhost (or `http://<your-local-ip>` from mobile)
- **Webhook endpoint:** `POST http://localhost/api/listener/webhook`

## Development Phases

- **Phase 1** — Core Infrastructure: webhook → DB → Blofin
- **Phase 2** — Dashboard MVP: orders UI, live feed, stats
- **Phase 3** — Hyperliquid integration
- **Phase 4** — Full controls: positions, retry, mobile polish
- **Phase 5** — Order Generator / Strategy Engine

See [`docs/SDD.md`](docs/SDD.md) for the full Software Design Document.

## TradingView Alert Setup

Set your TradingView alert webhook URL to:
```
http://<your-ip>/api/listener/webhook
```

Alert message JSON:
```json
{
  "symbol": "{{ticker}}",
  "side": "buy",
  "signal": "open_long",
  "orderType": "market",
  "size": "0.01",
  "leverage": 10,
  "marginMode": "cross",
  "platform": "auto",
  "strategyId": "my-strategy",
  "timestamp": "{{timenow}}",
  "token": "your-webhook-secret"
}
```

## Security Notes

- Exchange API keys are stored AES-256-GCM encrypted in the database
- Webhook authentication uses constant-time HMAC comparison
- PostgreSQL and Redis are not exposed outside Docker
- Never expose port 80 to the internet without additional auth (use VPN or Nginx basic auth)

## Environment Variables

See [`.env.example`](.env.example) for all required variables.
