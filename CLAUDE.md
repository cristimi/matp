# MATP Project Context

- Docker Compose project, all services managed via `docker compose` (never `docker-compose`)
- DB: PostgreSQL, user=matp, database=matp
- order-executor: Python service, rebuild with `docker compose build order-executor`
- Never print private keys, credentials, or secrets
- Always read relevant files before making any changes
