#!/usr/bin/env bash
# redeploy.sh — rebuild + recreate a MATP service, then verify it's actually live.
#
# Usage:
#   ./scripts/redeploy.sh <service>           # fast layer-cached build + force-recreate
#   ./scripts/redeploy.sh <service> --clean   # full --no-cache rebuild (slow; cache reset)
#   ./scripts/redeploy.sh all                 # rebuild + recreate the whole stack
#
# Why this exists: `docker compose restart` reuses the old image/container and ignores
# source changes; a plain `up -d` after a build can leave the old container running.
# This script always force-recreates and prunes the old image, then prints proof of the
# live bundle so you don't have to trust the host-side build output.

set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE="${1:-}"
if [[ -z "$SERVICE" ]]; then
  echo "Usage: $0 <service|all> [--clean]" >&2
  exit 1
fi
shift || true

NOCACHE=""
if [[ "${1:-}" == "--clean" ]]; then
  NOCACHE="--no-cache"
fi

echo "▶ Building ${SERVICE} ${NOCACHE:+(no cache)} …"
if [[ "$SERVICE" == "all" ]]; then
  docker compose build $NOCACHE
  echo "▶ Recreating the full stack …"
  docker compose up -d --force-recreate
else
  docker compose build $NOCACHE "$SERVICE"
  echo "▶ Recreating ${SERVICE} …"
  docker compose up -d --force-recreate "$SERVICE"
fi

# Reclaim the now-dangling old image(s) so disk doesn't creep.
docker image prune -f >/dev/null 2>&1 || true

echo "▶ Verifying …"
sleep 2
docker compose ps ${SERVICE/all/} 2>/dev/null || docker compose ps

# For the UI, print the asset hash nginx is actually serving right now.
if [[ "$SERVICE" == "dashboard-ui" || "$SERVICE" == "all" ]]; then
  HASH="$(curl -s http://localhost/ 2>/dev/null | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1 || true)"
  echo "   live dashboard-ui asset: ${HASH:-<could not read http://localhost/>}"
fi

echo "✓ ${SERVICE} redeployed."
echo "  If a device still shows the old UI, that's client-side cache (index.html is no-store"
echo "  server-side) — clear the browser cache once on that device."
