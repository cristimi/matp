# Streamline Builds — Task Report

**Date:** 2026-06-16
**Branch:** main
**Files changed:** `docker-compose.yml`, `scripts/redeploy.sh` (new), `CLAUDE.md`

---

## Summary

All three changes applied and verified end-to-end:
1. `docker-compose.yml` — shadowing bind-mount removed from `dashboard-ui`
2. `scripts/redeploy.sh` — deploy script created and made executable
3. `CLAUDE.md` — replaced with full project context doc

---

## Step 1 — `docker-compose.yml` diff

```diff
   dashboard-ui:
     build: ./dashboard-ui
     environment:
       VITE_API_BASE: /api/dashboard
       VITE_WS_URL: /ws/orders
-    volumes:
-      - ./dashboard-ui/dist:/usr/share/nginx/html
     depends_on:
       dashboard-api:
         condition: service_healthy
     networks: [matp_net]
     restart: unless-stopped
```

**Validation:**
```
docker compose config >/dev/null && echo "compose OK"
→ compose OK

grep -c ':/usr/share/nginx/html' docker-compose.yml
→ 0
```

---

## Step 2 — `scripts/redeploy.sh`

- Created at `scripts/redeploy.sh`
- Executable bit set: `-rwxrwxr-x`

---

## Step 3 — `CLAUDE.md`

Replaced the old 7-line stub with the full project context document covering:
- Golden rules
- Deploy workflow (use `./scripts/redeploy.sh`)
- UI image-as-source-of-truth explanation (with explicit warning against re-adding the mount)
- nginx config reload procedure
- Deploy verification commands
- "Old UI still showing" browser cache explanation

---

## Step 4 — Verification output (raw)

### `./scripts/redeploy.sh dashboard-ui --clean`
```
▶ Building dashboard-ui (no cache) …
...
#11 [builder 4/6] RUN npm ci
#11 added 175 packages, and audited 176 packages in 52s
...
#13 [builder 6/6] RUN npm run build
#13 vite v5.4.21 building for production...
#13 ✓ 860 modules transformed.
#13 dist/index.html                   0.81 kB │ gzip:   0.40 kB
#13 dist/assets/index-BQKF-5_P.css   22.88 kB │ gzip:   4.66 kB
#13 dist/assets/index-DvGiQb5U.js   686.58 kB │ gzip: 185.39 kB
#13 ✓ built in 43.16s
...
 Container matp-dashboard-ui-1 Recreated
 Container matp-dashboard-ui-1 Started
▶ Verifying …
NAME                  IMAGE               COMMAND   SERVICE        CREATED         STATUS         PORTS
matp-dashboard-ui-1   matp-dashboard-ui   ...       dashboard-ui   8 seconds ago   Up 3 seconds   80/tcp, 3000/tcp
   live dashboard-ui asset: index-DvGiQb5U.js
✓ dashboard-ui redeployed.
```

### 1) Container state
```
NAME                  IMAGE               COMMAND                  SERVICE        CREATED          STATUS         PORTS
matp-dashboard-ui-1   matp-dashboard-ui   "/docker-entrypoint.…"   dashboard-ui   14 seconds ago   Up 9 seconds   80/tcp, 3000/tcp
```

### 2) Known string present in image-baked bundle
```
$ docker compose exec -T dashboard-ui grep -rl 'Signal Source' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-DvGiQb5U.js
```
String found under `/usr/share/nginx/html/assets/` — bundle is served from the image.

### 3) Live asset hash via reverse proxy
```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DvGiQb5U.js
```
Matches the hash produced by the Docker build (`dist/assets/index-DvGiQb5U.js`).

### 4) Container mounts — dist bind-mount is gone
```
$ docker inspect "$(docker compose ps -q dashboard-ui)" \
    --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
(no output)
```
**Zero mounts** on the container. The `./dashboard-ui/dist:/usr/share/nginx/html` bind-mount
is confirmed absent. The image is the sole source of truth for what nginx serves.

---

## Result

Fix verified end-to-end. `./scripts/redeploy.sh dashboard-ui` will now reliably replace what
nginx serves on every run. The split-brain condition (host `dist/` shadowing the image) is
eliminated.
