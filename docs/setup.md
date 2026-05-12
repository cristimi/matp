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

Edit `.env`:

```env
POSTGRES_PASSWORD=<strong-random-password>
WEBHOOK_SECRET=<min-32-char-random-string>
MASTER_KEY=<min-32-char-random-string>
BLOFIN_API_KEY=<your-blofin-api-key>
BLOFIN_API_SECRET=<your-blofin-api-secret>
HYPERLIQUID_PRIVATE_KEY=<your-hl-private-key>
```

Generate secrets easily:
```bash
openssl rand -hex 32
```

### 3. Start all services

```bash
docker compose up -d --build
```

Check all containers are running:
```bash
docker compose ps
```

### 4. Verify

- Dashboard: http://localhost
- Listener health: http://localhost/api/listener/health
- Generator health: http://localhost/api/generator/health

### 5. Mobile access (same Wi-Fi)

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

1. Create a YAML file in the `strategies_config` volume:

```bash
# Find the volume path
docker volume inspect matp_strategy_configs

# Or exec into the container
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
