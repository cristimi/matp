# notification-service: web-push high-urgency + TTL (fix Android Doze delivery lag)

## Why

Trade notifications were arriving 30–60s late on Android. The `webpush()` call in
`WebPushSink._send_one` passed no `Urgency` header, so FCM treated every message as normal
priority and batched it under Doze. Added `Urgency: high` (deliver immediately) and a 600s
TTL (so a briefly-offline phone still gets the message on reconnect without a much later
stale alert).

## What changed

- `notification-service/app/config.py`: added `webpush_urgency: str = "high"` and
  `webpush_ttl_s: int = 600` to `Settings`, right after the VAPID block.
- `notification-service/app/sinks/webpush.py`: the `webpush(...)` call in `_send_one` now
  also passes `ttl=settings.webpush_ttl_s` and `headers={"Urgency": settings.webpush_urgency}`.

No other lines touched. No new dependencies. No other services touched.

## Deploy

```
./scripts/redeploy.sh notification-service
```
```
 Container matp-notification-service-1 Recreate
 Container matp-notification-service-1 Recreated
 Container matp-postgres-1 Healthy
 Container matp-redis-1 Healthy
 Container matp-notification-service-1 Starting
 Container matp-notification-service-1 Started
▶ Verifying …
NAME                          IMAGE                       COMMAND                  SERVICE                CREATED         STATUS                            PORTS
matp-notification-service-1   matp-notification-service   "uvicorn app.main:ap…"   notification-service   7 seconds ago   Up 3 seconds (health: starting)   8010/tcp
✓ notification-service redeployed.
```

## Verify (real pasted output)

**1. Source landed (both edits present):**
```
$ grep -n "webpush_urgency\|webpush_ttl_s" notification-service/app/config.py
20:    webpush_urgency: str = "high"
21:    webpush_ttl_s:   int = 600

$ grep -n "ttl=settings.webpush_ttl_s\|Urgency" notification-service/app/sinks/webpush.py
68:                ttl=settings.webpush_ttl_s,
69:                headers={"Urgency": settings.webpush_urgency},
```

**2. Change is in the running container, not a cached image** (Dockerfile does
`COPY app/ ./app/` into `/app`, so the in-container path is `/app/app/sinks/webpush.py`):
```
$ docker compose exec notification-service grep -n "Urgency" /app/app/sinks/webpush.py
69:                headers={"Urgency": settings.webpush_urgency},
```

**3. Service came back healthy, clean startup, no traceback:**
```
$ docker compose ps notification-service
NAME                          IMAGE                       COMMAND                  SERVICE                CREATED              STATUS                    PORTS
matp-notification-service-1   matp-notification-service   "uvicorn app.main:ap…"   notification-service   About a minute ago   Up 56 seconds (healthy)   8010/tcp

$ docker compose logs --tail=30 notification-service
INFO:     Started server process [1]
INFO:     Waiting for application startup.
2026-07-04 15:03:08,591 [INFO] app.db: Database pool initialized.
2026-07-04 15:03:08,595 [INFO] app.redis_client: Redis client initialized.
2026-07-04 15:03:08,653 [INFO] app.redis_client: Consumer group notification-service already exists
INFO:     Application startup complete.
2026-07-04 15:03:17,142 [INFO] app.redis_client: Consumer group notification-service already exists
2026-07-04 15:03:17,143 [INFO] app.consumer: Consumer loop starting on stream=notifications:events group=notification-service
INFO:     Uvicorn running on http://0.0.0.0:8010 (Press CTRL+C to quit)
INFO:     127.0.0.1:37452 - "GET /health HTTP/1.1" 200 OK
2026-07-04 15:03:19,982 [INFO] httpx: HTTP Request: GET http://order-executor:8004/health "HTTP/1.1 200 OK"
2026-07-04 15:03:20,185 [INFO] httpx: HTTP Request: GET http://order-listener:8001/health "HTTP/1.1 200 OK"
2026-07-04 15:03:30,441 [INFO] httpx: HTTP Request: GET http://order-executor:8004/health "HTTP/1.1 200 OK"
2026-07-04 15:03:30,571 [INFO] httpx: HTTP Request: GET http://order-listener:8001/health "HTTP/1.1 200 OK"
INFO:     127.0.0.1:49392 - "GET /health HTTP/1.1" 200 OK
2026-07-04 15:03:40,615 [INFO] httpx: HTTP Request: GET http://order-executor:8004/health "HTTP/1.1 200 OK"
2026-07-04 15:03:40,640 [INFO] httpx: HTTP Request: GET http://order-listener:8001/health "HTTP/1.1 200 OK"
INFO:     127.0.0.1:52692 - "GET /health HTTP/1.1" 200 OK
```
Consumer group attached, no traceback.

## Scope

Only `notification-service/app/config.py` and `notification-service/app/sinks/webpush.py`
were touched. No other service, migration, docker-compose, nginx, or service-worker changes.
