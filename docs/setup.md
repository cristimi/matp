# Setup Guide

## Prerequisites

- Docker Desktop (or Docker Engine + Docker Compose v2)
- Git
- A local machine on your home network (for mobile access)

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url> matp
cd matp
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with at minimum:

```env
POSTGRES_PASSWORD=matp                 # see note in .env.example before changing
MASTER_KEY=<generate: openssl rand -hex 32>
GEMINI_API_KEY=<your Gemini key>       # or OPENAI_API_KEY / ANTHROPIC_API_KEY
PUBLIC_HOST=192.168.1.xxx              # your LAN IP or domain
```

Generate secrets:
```bash
openssl rand -hex 32
```

Exchange API credentials are **not** configured in `.env` — they are added after
startup via the dashboard Accounts page (see "Connect an exchange account" below).

### 3. Start all services

```bash
docker compose up -d --build
```

> **Fresh deploy note:** On the first `docker compose up`, Postgres initialises
> automatically from `db/init.sql` — the full schema and seed data are applied
> with no manual migration step. For an existing instance applying new migrations,
> run the numbered SQL files in `db/migrations/` in sequence
> (see `db/migrations/MANIFEST.md`).

Check all containers are running:
```bash
docker compose ps
```

### 4. Verify

| Service | How to check |
|---------|-------------|
| Dashboard UI | http://localhost |
| Tester UI | http://localhost/tester/ |
| order-listener | `curl http://localhost/api/listener/health` |
| order-generator | `docker compose ps order-generator` (no `/health` endpoint) |
| order-executor | `docker compose ps order-executor` (internal only, not on nginx) |
| dashboard-api | `curl http://localhost/api/dashboard/health` |
| strategy-tester | `curl http://localhost/api/tester/health` |
| ai-signal-generator | `curl http://localhost:8005/health` (direct host port; no nginx route) |

All `curl` health checks return `{"status":"ok"}` when healthy.

### 5. Connect an exchange account

Exchange credentials are stored **encrypted** in the database — they are never
placed in `.env`.

1. Open the dashboard → **Accounts** page.
2. Click **Add account**, choose exchange (Blofin or Hyperliquid), mode
   (`live` or `demo`), and a label. This creates a placeholder row in the DB.
3. Select the new account → **Update credentials**, paste your API key/secret
   (or private key for Hyperliquid). `order-executor` encrypts them with
   `MASTER_KEY` before writing to the database — credentials are never stored
   in plaintext.
4. The account is now available to assign to strategies.

> Underlying API: `POST /api/dashboard/accounts` (create),
> `PUT /api/dashboard/accounts/:id/credentials` (set credentials).

### 6. Mobile access (same Wi-Fi)

Find your local IP:
```bash
# macOS / Linux
ipconfig getifaddr en0    # or ip addr

# Windows
ipconfig
```

Access from phone: `http://192.168.1.xxx`

## Development Workflow

### Rebuild a single service after code changes

```bash
docker compose up -d --build order-listener
```

### View logs

```bash
docker compose logs -f order-listener
docker compose logs -f order-generator
docker compose logs -f dashboard-api
```

### Reset the database

```bash
docker compose down -v   # ⚠️ destroys all data
docker compose up -d
```

### Connect to PostgreSQL

```bash
docker compose exec postgres psql -U matp -d matp
```

## Adding a Strategy

### Via the dashboard (primary path)

1. Open the dashboard → **Strategies** page → **Add strategy**.
2. Fill in name, exchange, mode, and (for AI strategies) the AI configuration.
   The strategy is created in the database via `POST /api/dashboard/strategies`
   and immediately available for webhook signals.
3. The dashboard shows the per-strategy webhook URL to paste into TradingView
   (requires `PUBLIC_HOST` to be set — see `.env.example`).

### Via order-generator YAML configs (order-generator path)

<!-- TODO: confirm order-generator strategy path still supported -->

1. Create a YAML file in the `strategies_config` volume:

```bash
# Exec into the container
docker compose exec order-generator sh
# then edit /app/strategies_config/my_strategy.yaml
```

2. Restart the generator:
```bash
docker compose restart order-generator
```

3. The strategy appears in the Dashboard → Strategies page.

## Phase Checklist

- [ ] Phase 1: TradingView → Listener → DB → Blofin working
- [ ] Phase 2: Dashboard showing orders and live feed
- [ ] Phase 3: Hyperliquid adapter complete
- [ ] Phase 4: Positions page, retry UI, mobile polish
- [ ] Phase 5: Order Generator strategies running
