# 2026-06-30 — Drop host-published ports (8001/8005/8006) + fix webhook source_ip

## Changes

### Phase 1 — Remove host port mappings (`docker-compose.yml`)

Removed the `ports:` block (two lines each) from three services:

```diff
   order-listener:
     build: ./order-listener
-    ports:
-      - "8001:8001"
     environment:

   ai-signal-generator:
     build: ./ai-signal-generator
-    ports:
-      - "8005:8005"
     environment:

   strategy-tester:
     build:
       context: .
       dockerfile: strategy-tester/Dockerfile
-    ports:
-      - "8006:8006"
     environment:
```

Deployed with `docker compose up -d order-listener ai-signal-generator strategy-tester` (config-only, no rebuild).

### Phase 2 — Fix webhook `source_ip` (`order-listener/app/webhook_handler.py`)

Added `_client_ip()` helper after `router = APIRouter()`:

```diff
+def _client_ip(request: Request) -> Optional[str]:
+    xff = request.headers.get("x-forwarded-for")
+    if xff:
+        return xff.split(",")[0].strip()
+    xri = request.headers.get("x-real-ip")
+    if xri:
+        return xri.strip()
+    return request.client.host if request.client else None
```

Updated `receive_webhook`:

```diff
-    source_ip = request.client.host if request.client else None
+    source_ip = _client_ip(request)
```

Deployed with `./scripts/redeploy.sh order-listener`.

---

## Phase 1 verification output

**1. `docker compose ps` — no `0.0.0.0:` binding on the three services:**

```
NAME                         IMAGE                      SERVICE               STATUS                             PORTS
matp-ai-signal-generator-1   matp-ai-signal-generator   ai-signal-generator   Up About a minute (healthy)        8005/tcp
matp-order-listener-1        matp-order-listener        order-listener        Up 2 minutes (healthy)             8001/tcp
matp-strategy-tester-1       matp-strategy-tester       strategy-tester       Up 2 minutes (healthy)
```

All three show only the internal `<port>/tcp` notation — no `0.0.0.0:` publish.

**2. Negative proof — host ports refused:**

```
port 8001:
curl: (7) Failed to connect to localhost port 8001 after 2 ms: Could not connect to server
  REFUSED (expected)
port 8005:
curl: (7) Failed to connect to localhost port 8005 after 0 ms: Could not connect to server
  REFUSED (expected)
port 8006:
curl: (7) Failed to connect to localhost port 8006 after 0 ms: Could not connect to server
  REFUSED (expected)
```

**3. Positive internal proof — reachable by docker hostname from nginx container:**

```
{"status":"ok","service":"order-listener"}
{"status":"ok","service":"ai-signal-generator"}
{"status":"ok","service":"strategy-tester"}
```

**4. App through proxy chain + health:**

```
{"status":"ok","service":"strategy-tester"}

matp-ai-signal-generator-1   ...   (healthy)   8005/tcp
matp-order-listener-1        ...   (healthy)   8001/tcp
matp-strategy-tester-1       ...   (healthy)
```

---

## Phase 2 verification output

**1. Deployed code in running container:**

```
$ docker compose exec order-listener grep -ni "x-forwarded-for" /app/app/webhook_handler.py
31:    xff = request.headers.get("x-forwarded-for")
```

**2. End-to-end IP forwarding test:**

```
$ curl -sS -m 5 -X POST \
    -H 'X-Forwarded-For: 9.9.9.9, 10.0.0.1' \
    -H 'Content-Type: application/json' \
    -d '{}' \
    http://localhost/api/listener/webhook/zz-verify-ip; echo
{"detail":"7 validation errors for WebhookPayload ..."}   # 422 — expected, row still logged

$ docker compose exec postgres psql -U matp -d matp \
    -c "SELECT id, source_ip FROM signal_log WHERE strategy_id = 'zz-verify-ip';"
 id  | source_ip
-----+-----------
 145 | 9.9.9.9
(1 row)
```

`source_ip = 9.9.9.9` — first XFF entry correctly extracted.

**3. Test row cleanup:**

```
$ docker compose exec postgres psql -U matp -d matp \
    -c "DELETE FROM signal_log WHERE strategy_id = 'zz-verify-ip' RETURNING id;"
 id
-----
 145
(1 row)
DELETE 1
```

---

---

## Real TradingView signal confirmation

After both phases were deployed, a live TradingView alert fired against the `hype-test-7db4` strategy. DB record:

```
$ docker compose exec postgres psql -U matp -d matp \
    -c "SELECT id, strategy_id, received_at, http_status, outcome, source_ip FROM signal_log ORDER BY received_at DESC LIMIT 2;"
 id  |     strategy_id     |          received_at          | http_status | outcome |  source_ip
-----+---------------------+-------------------------------+-------------+---------+-------------
 146 | hype-test-7db4      | 2026-06-30 16:02:11.221686+00 |         200 | filled  | 52.32.178.7
 144 | tv-btc-test-hl-94e1 | 2026-06-30 15:33:50.580711+00 |         200 | filled  | 172.18.0.15
```

- id 146 (`hype-test-7db4`, 16:02:11 UTC): arrived after the fix. `source_ip = 52.32.178.7` — a real TradingView egress IP. Confirms the full path (Zoraxy → nginx → order-listener) is working and forwarding the correct client IP end-to-end.
- id 144 (`tv-btc-test-hl-94e1`, 15:33:50 UTC): arrived before the Phase 2 redeploy — still shows the old internal IP `172.18.0.15`. Expected.

Zoraxy is forwarding the original client IP correctly; no further Zoraxy-side config change needed.

---

## Notes

- `strategy-tester` has no `ports:` entry in the PORTS column after removal (shows blank vs `8005/tcp` for ai-signal-generator and `8001/tcp` for order-listener which expose internally via `EXPOSE` in their Dockerfiles).
