# notification-service v1 (Android web push) — Phase 1 + Phase 6 report

Branch: `feat/notification-service`. Phase 1 (the additive build) was pushed and reviewed;
the user then said "go ahead with the order-listener hooks" — Phase 6 below implements
exactly the gate proposal from the Phase 1 section further down, with no changes to it.

## What changed, per file

**New service — `notification-service/`** (FastAPI, mirrors `ai-signal-generator`'s layout,
port 8010 internal-only):
- `Dockerfile`, `requirements.txt` (fastapi, uvicorn, asyncpg, redis[asyncio], pywebpush,
  py-vapid, httpx, pydantic/pydantic-settings)
- `app/config.py` — env-driven settings (DB/Redis URLs, VAPID key/subject, stream/group names,
  `exchanges` list — defaults to `blofin`, the only exchange with a running `market-ingestion`
  instance today — listener/executor URLs, poll interval)
- `app/db.py` — asyncpg pool (copy of `ai-signal-generator/app/database.py`'s pattern, minus
  the exchange-resolution helper, which doesn't apply here)
- `app/redis_client.py` — stream producer (`emit_event`) + consumer-group helpers
  (`ensure_group`, `read_group` for new entries, `read_pending` for crash-recovery redelivery,
  `ack`) + heartbeat reader
- `app/render.py` — event → `{title, body, tag, renotify, data}` + `compute_dedup_key`
- `app/consumer.py` — drain-pending-then-blocking-read loop: dedup via `notification_log`
  (24h window) → render → dispatch to sinks → log → ack. A dead phone (sink failure) is
  recorded `status='failed'` and still acked — never blocks the pipeline.
- `app/health_watcher.py` — polls `ingestion:heartbeat:{exchange}` staleness (>60s) and
  `order-executor`/`order-listener` `/health` every 10s, edge-triggered, emits onto the same
  stream.
- `app/sinks/base.py` — abstract `Sink`/`SinkResult`. `app/sinks/webpush.py` — `WebPushSink`
  (pywebpush + VAPID; disables a subscription on 404/410 "gone"; any other exception, incl.
  malformed key data, is recorded as a failed send rather than raised — see bugs found below).
- `app/main.py` — FastAPI app: `/health`, `/vapid-public-key`, `POST/DELETE /subscriptions`,
  `POST /test` (emits a synthetic `service.up` event for end-to-end push testing); lifespan
  starts the consumer loop + health-watcher loop as background tasks.

**Migrations** (next free numbers were 040/041, confirmed via `ls db/migrations`):
- `db/migrations/040_push_subscriptions.sql`
- `db/migrations/041_notification_log.sql`

**Edited:**
- `docker-compose.yml` — new `notification-service` block (mirrors `signal-engine`/
  `ai-signal-generator` style; no host port published, per the "internal only" rule).
- `nginx/nginx.conf` — new `location /api/notifications/` proxying to
  `notification-service:8010`.
- `.env` — appended `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`,
  `NOTIFICATION_EXCHANGES` (private key generated in a throwaway container, written directly
  to a file and appended to `.env` without ever printing it to a terminal/tool-output; the
  temp key file was securely deleted afterward). `.env.example` got the same keys as blank
  placeholders (public key intentionally left blank there too — the real public key isn't
  secret, but there's no reader of `.env.example` who needs it).
- `docs/ROADMAP.md` — appended the 7 deferred-backlog items from the executor prompt's
  section 8 (iOS push, `TelegramSink`, threshold PnL updates, per-account auth-ping health,
  health for remaining services, multi-device onboarding UI, notification-history view,
  `notification_log` retention job).
- `dashboard-ui/public/sw.js` — new service worker: `push` → `showNotification` (tag,
  renotify, icon/badge from existing PWA icons); `notificationclick` → focus/navigate to
  `/positions` if the payload carries a `position_id`, else `/`.
- `dashboard-ui/src/main.tsx` — registers `/sw.js` at root scope on window `load`.
- `dashboard-ui/src/pages/Settings.tsx` — new `NotificationsSection` component: requests
  `Notification.requestPermission()`, fetches `/api/notifications/vapid-public-key`,
  `pushManager.subscribe()`, posts the subscription to `/api/notifications/subscriptions`.
  Rendered in `SettingsPage`'s JSX between the Webhook and System Information sections.

No other services were touched. `order-listener` was **not** edited — see gate proposal below.

## Real bugs found and fixed during verification (not just "looks fine")

1. **Redis client-side timeout raced the server-side `BLOCK`**: `XREADGROUP ... BLOCK 5000`
   was throwing `redis.exceptions.TimeoutError` on every idle poll because the async redis
   client's default socket read timeout was shorter than the block window — the consumer
   loop was permanently stuck in an error/retry cycle and never actually blocking-read
   successfully. Fixed by setting `socket_timeout=None, socket_connect_timeout=5` on the
   client in `redis_client.init_redis()`.
2. **Crash before ack stranded an entry in the PEL forever**: the very first synthetic test
   (before fix #3 below) threw before `ack()`, and since `read_group` only ever reads new
   entries (`>`), that entry was never redelivered — violating the documented crash-safety
   guarantee ("reprocessed after a restart"). Added `read_pending` (`XREADGROUP ... "0"`) and
   a drain-pending-before-blocking-loop step in `consumer.run_consumer_loop`, with a
   no-progress guard (compare consecutive batches of ids) so a genuine poison-pill entry
   can't spin the drain loop forever — it's left for the next restart and logged as an error
   instead.
3. **Non-`WebPushException` errors weren't caught**: a malformed/undecodable subscription key
   raises a plain `binascii.Error`/`ValueError` from inside `pywebpush`'s `WebPusher.__init__`,
   not a `WebPushException` — my original `except WebPushException` let it escape and crash
   the whole entry (defeating "a dead phone must not block acking"). Broadened to catch
   `Exception` generically in `WebPushSink._send_one`.
4. **`NotificationsSection` was defined but never rendered**: Vite/Rollup tree-shook the whole
   component (and its strings, incl. the VAPID-key fetch and subscribe POST) out of the built
   bundle because nothing referenced it in `SettingsPage`'s JSX. Fixed by adding
   `<NotificationsSection />` to the render tree. Caught by grepping the *served* bundle for
   `Enable notifications`/`vapid-public-key`, which came back empty until this was fixed —
   exactly the "don't trust host build output" verification CLAUDE.md calls for.
5. **TS build error**: `Uint8Array.from(...)` typed as `Uint8Array<ArrayBufferLike>`, not
   assignable to `PushSubscriptionOptionsInit.applicationServerKey`'s `BufferSource`. Fixed by
   constructing via `new Uint8Array(length)` (typed `Uint8Array<ArrayBuffer>`) instead.

## Phase 1 verification (real output)

**`docker compose config` parses:**
```
$ docker compose config --quiet && echo COMPOSE_CONFIG_OK
COMPOSE_CONFIG_OK
```

**Migrations applied clean, self-verified:**
```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/040_push_subscriptions.sql
CREATE TABLE
DO
NOTICE:  Migration 040 verified OK

$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/041_notification_log.sql
CREATE TABLE
CREATE INDEX
CREATE INDEX
CREATE INDEX
CREATE INDEX
DO
NOTICE:  Migration 041 verified OK
```

**`\d push_subscriptions` / `\d notification_log`:**
```
                         Table "public.push_subscriptions"
    Column    |           Type           | Collation | Nullable |      Default
--------------+--------------------------+-----------+----------+-------------------
 id           | uuid                     |           | not null | gen_random_uuid()
 endpoint     | text                     |           | not null |
 p256dh       | text                     |           | not null |
 auth         | text                     |           | not null |
 user_agent   | text                     |           |          |
 enabled      | boolean                  |           | not null | true
 created_at   | timestamp with time zone |           | not null | now()
 last_seen_at | timestamp with time zone |           |          |
Indexes:
    "push_subscriptions_pkey" PRIMARY KEY, btree (id)
    "push_subscriptions_endpoint_key" UNIQUE CONSTRAINT, btree (endpoint)

                          Table "public.notification_log"
    Column    |           Type           | Collation | Nullable |      Default
--------------+--------------------------+-----------+----------+-------------------
 id           | uuid                     |           | not null | gen_random_uuid()
 event_type   | text                     |           | not null |
 dedup_key    | text                     |           |          |
 position_id  | uuid                     |           |          |
 title        | text                     |           |          |
 body         | text                     |           |          |
 payload      | jsonb                    |           |          |
 status       | text                     |           | not null |
 error        | text                     |           |          |
 device_count | integer                  |           |          |
 created_at   | timestamp with time zone |           | not null | now()
 sent_at      | timestamp with time zone |           |          |
Indexes:
    "notification_log_pkey" PRIMARY KEY, btree (id)
    "ix_notification_log_created_at" btree (created_at DESC)
    "ix_notification_log_dedup_key" btree (dedup_key)
    "ix_notification_log_event_type" btree (event_type)
    "ix_notification_log_position_id" btree (position_id)
Check constraints:
    "notification_log_status_chk" CHECK (status = ANY (ARRAY['sent'::text, 'failed'::text, 'skipped'::text]))
```

**Container healthy, no more consumer-loop timeout errors (after bug fixes #1-#3):**
```
$ docker compose ps notification-service
NAME                          IMAGE                       COMMAND                  SERVICE                CREATED          STATUS                    PORTS
matp-notification-service-1   matp-notification-service   "uvicorn app.main:ap…"   notification-service   35 seconds ago   Up 28 seconds (healthy)   8010/tcp

$ docker compose logs notification-service --tail 15
...
2026-07-04 11:09:44,473 [INFO] app.db: Database pool initialized.
2026-07-04 11:09:44,477 [INFO] app.redis_client: Redis client initialized.
2026-07-04 11:09:44,507 [INFO] app.redis_client: Consumer group notification-service already exists
INFO:     Application startup complete.
2026-07-04 11:09:49,953 [INFO] app.consumer: Consumer loop starting on stream=notifications:events group=notification-service
INFO:     Uvicorn running on http://0.0.0.0:8010 (Press CTRL+C to quit)
2026-07-04 11:09:50,339 [INFO] httpx: HTTP Request: GET http://order-executor:8004/health "HTTP/1.1 200 OK"
2026-07-04 11:09:50,448 [INFO] httpx: HTTP Request: GET http://order-listener:8001/health "HTTP/1.1 200 OK"
INFO:     127.0.0.1:42624 - "GET /health HTTP/1.1" 200 OK
```

**`/health` and `/vapid-public-key` from inside the network (also re-verified through nginx
after a `--force-recreate` — see nginx note below):**
```
$ docker compose exec nginx wget -qO- http://notification-service:8010/health
{"status":"ok","service":"notification-service"}

$ docker compose exec nginx wget -qO- http://notification-service:8010/vapid-public-key
{"public_key":"BEeqiZxl2du8drXYr2F0roVpu5W8An6ohd3MjNa2xt1hSqwsZCpb7BhrtpeCXowmjg63na3pTYUDPmlBKxFnlss"}

$ curl -s http://localhost/api/notifications/health
{"status":"ok","service":"notification-service"}
$ curl -s http://localhost/api/notifications/vapid-public-key
{"public_key":"BEeqiZxl2du8drXYr2F0roVpu5W8An6ohd3MjNa2xt1hSqwsZCpb7BhrtpeCXowmjg63na3pTYUDPmlBKxFnlss"}
```

**Fake subscription registered → row in DB:**
```
$ docker compose exec nginx wget -qO- --header="Content-Type: application/json" \
  --post-data='{"endpoint":"https://fcm.googleapis.com/fcm/send/test-endpoint-abc123",
                "keys":{"p256dh":"...","auth":"..."},"user_agent":"phase1-verification-script"}' \
  http://notification-service:8010/subscriptions
{"status":"ok"}

$ docker compose exec -T postgres psql -U matp -d matp -c "SELECT count(*) FROM push_subscriptions;"
 count
-------
     1
(1 row)

$ docker compose exec -T postgres psql -U matp -d matp -c "SELECT id, endpoint, enabled FROM push_subscriptions;"
                  id                  |                         endpoint                         | enabled
--------------------------------------+----------------------------------------------------------+---------
 3f8166c0-6efd-42f3-b06b-ae74a9ccf82e | https://fcm.googleapis.com/fcm/send/test-endpoint-abc123 | t
(1 row)
```

**Manual `XADD` of a synthetic `position.closed` event → render + log + ack proven
end-to-end (device send fails, as expected — no real phone subscribed — but the pipeline
does not crash and correctly acks):**
```
$ docker compose exec -T redis redis-cli XADD notifications:events '*' data \
  '{"event":"position.closed","position_id":"387e6c1e-...","symbol":"BTC-USDT","side":"long",
    "size":"0.01","entry_price":"60000","opened_at":"2026-07-04T10:00:00+00:00",
    "closing_price":"61500","pnl_realized":"15.0","close_reason":"take_profit",
    "closed_at":"2026-07-04T11:00:00+00:00"}'
1783163407689-0

$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT event_type, dedup_key, position_id, title, body, status, error, device_count
   FROM notification_log ORDER BY created_at DESC LIMIT 1;"
   event_type    |                      dedup_key                       |             position_id              |          title          |                                      body                                       | status |      error      | device_count
-----------------+------------------------------------------------------+--------------------------------------+-------------------------+----------------------------------------------------------------------------------+--------+-----------------+--------------
 position.closed | position:387e6c1e-3f51-4ee1-8550-f8cb583a0d2d:closed | 387e6c1e-3f51-4ee1-8550-f8cb583a0d2d | 🟢 Closed BTC-USDT LONG | Entry LONG 0.01 @ 60000.0000 → Exit @ 61500.0000  •  PnL +15.00  •  take_profit | failed | Invalid EC key. |            0
```
Note the body carries **both legs** (entry + exit) in one notification, per the "complete,
don't replace" spec — confirmed against the same `tag` the entry notification would use.

**Dedup proven** (re-`XADD` of the identical event → second row is `skipped`, not `failed`
again):
```
$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT event_type, status FROM notification_log WHERE position_id = '387e6c1e-...' ORDER BY created_at;"
   event_type    | status
-----------------+---------
 position.closed | failed
 position.closed | skipped
(2 rows)
```

**Crash-recovery drain proven**: the very first synthetic test (before bug fixes #2/#3)
threw mid-processing and left an entry stuck in the consumer group's pending list; after
the fixes and a redeploy, the drain-on-start logic picked it up automatically:
```
2026-07-04 11:12:10,572 [INFO] app.consumer: Redelivering 1 unacked entry from a previous run
...
$ docker compose exec -T redis redis-cli XPENDING notifications:events notification-service
0
```

**dashboard-ui still loads after the nginx change** (asset hash changes across this session
purely because dashboard-ui itself was rebuilt for the sw.js/Settings.tsx work, not because
of the nginx edit):
```
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-B9CKvQnO.js
$ curl -s http://localhost/ | grep -o "<title>.*</title>"
<title>MATP Dashboard</title>
```

**`sw.js` served as JS (not the SPA HTML fallback) and the new Settings.tsx code survives
minification in the actually-served bundle** (bug #4 above was caught by this exact check
coming back empty before the fix):
```
$ curl -s http://localhost/sw.js | head -3
self.addEventListener('push', (event) => {
  let payload = {};
  try {

$ docker compose exec -T dashboard-ui sh -c "grep -al 'Enable notifications' /usr/share/nginx/html/assets/*.js"
/usr/share/nginx/html/assets/index-B9CKvQnO.js
$ docker compose exec -T dashboard-ui sh -c "grep -ao 'vapid-public-key' /usr/share/nginx/html/assets/*.js | head -1"
vapid-public-key
```

**Environment quirk encountered (not a real bug):** the first `nginx -s reload` appeared not
to pick up the new `location /api/notifications/` block — `docker compose exec nginx cat
/etc/nginx/conf.d/default.conf` showed stale content with a stat/ctime mismatch against the
host file despite `docker inspect`'s bind-mount source being correct. A `docker compose up -d
--force-recreate nginx` (fresh mount, not a live reload) resolved it. Documented here in case
it recurs — normal in-place `nginx -s reload` is still the documented approach for ordinary
config edits per CLAUDE.md.

## Gate — `order-listener` was NOT touched. Proposal below, awaiting "go".

Per the executor prompt, this is the one gate: I stop here and do not edit
`order-listener/app/redis_client.py` or `webhook_handler.py` until explicitly confirmed.

### 1. `emit_notification` helper proposed for `order-listener/app/redis_client.py`

Added alongside the existing `publish`/`cache_*` helpers, same file already imports `json`,
`logging`, and holds the shared `_redis` client:

```python
async def emit_notification(event: str, payload: dict) -> None:
    """xadd onto the notification-service event stream. Never raises — a notification
    failure must not affect order handling."""
    try:
        data = {"event": event, **payload}
        await get_redis().xadd("notifications:events", {"data": json.dumps(data, default=str)})
    except Exception as e:
        logger.warning(f"emit_notification failed for {event}: {e}")
```

### 2. Exact two insertion points in `webhook_handler.py`

**(a) `position.opened`** — inside `_create_strategy_position` (currently ~line 964-984).
The `INSERT` needs `RETURNING id` added (currently a bare `conn.execute`, discards the row) so
the notification can carry `position_id`:

```python
        position_id = await conn.fetchval(
            """
            INSERT INTO strategy_positions (
                strategy_id, exchange, symbol, pair_id, side, entry_price, size,
                leverage, margin_mode, opening_order_id, status, opened_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, 'open', NOW()
            )
            RETURNING id
            """,
            strategy['id'], strategy.get('exchange', 'auto'),
            f"{payload.base_asset}-{payload.quote_asset}", pair_id, pos_side,
            entry_price, db_size, effective_leverage, effective_margin_mode,
            opening_order_id,
        )

    await emit_notification("position.opened", {
        "position_id": str(position_id),
        "strategy_id": strategy['id'],
        "symbol": f"{payload.base_asset}-{payload.quote_asset}",
        "side": pos_side,
        "size": str(db_size),
        "entry_price": str(entry_price),
        "leverage": effective_leverage,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    })
```
(placed after the `async with pool.acquire() as conn:` block closes, so the emit isn't inside
the transaction/connection scope — matches how other post-write side effects in this file are
structured.) This only fires from `_create_strategy_position` — i.e. a genuinely **new** open,
never the "top up an existing leg" path in `_apply_position_fill`, which correctly does not
re-notify.

**(b) `position.closed`** — inside `close_strategy_position` (currently ~line 1069, the
initial `SELECT`, and ~line 1155, the full-close `UPDATE`). The initial `SELECT` needs
`entry_price` and `opened_at` added (currently only fetches `id, size, opening_order_id`) so
the close notification can carry the entry leg:

```python
        pos = await conn.fetchrow(
            """
            SELECT id, size, opening_order_id, entry_price, opened_at
            FROM strategy_positions
            WHERE strategy_id = $1 AND symbol = $2 AND side = $3 AND status = 'open'
            """,
            strategy['id'], symbol, side,
        )
```

Then, guarded on `is_full` (so partial closes are never notified — see item 3 below), placed
after the race-condition guard (`if updated is None: return {...}`) and near the existing
`logger.info(f"{'Closed' if is_full else ...}")` call (~line 1189-1196):

```python
    if is_full:
        await emit_notification("position.closed", {
            "position_id": str(pos['id']),
            "strategy_id": strategy['id'],
            "symbol": symbol,
            "side": side,
            "size": str(pos['size']),
            "entry_price": str(pos['entry_price']),
            "opened_at": pos['opened_at'].isoformat(),
            "closing_price": fill_price_f,
            "pnl_realized": realized_pnl_f,
            "close_reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
```

Because `close_strategy_position` is the single canonical close routine (its own docstring:
"shared by the synchronous market/filled-limit path... and the reconciler's pending-limit
fill detection" — and per the recent `feat/signal-engine` "single authoritative position
state" work, also the flip/native-SL/manual paths), this one hook point covers **every** real
full close, not just the webhook-driven signal path.

Both call sites need `from app.redis_client import ... emit_notification` added to the
existing `from app.redis_client import publish, cache_get, cache_set, cache_delete` import
line (line 23). `datetime`/`timezone` are already imported (line 12).

### 3. Partial closes — confirmed not notified

The `position.closed` emit is placed inside the `if is_full:` branch only. A partial reduce
(`close_size < open size`, `status` stays `'open'`) never reaches it. This matches the v1
scope explicitly ("full close only").

## Deferred

Appended to `docs/ROADMAP.md` → Deferred Backlog: iOS web push, `TelegramSink`,
threshold/interval PnL updates in notifications, per-account auth-ping health, health for the
remaining services (`dashboard-api`/`ai-signal-generator`/`strategy-tester`), multi-device
onboarding UI, notification-history dashboard view, `notification_log` retention/prune job.

## Deviations from the executor prompt

- Report path: the executor prompt specified `docs/process/reports/notification-service-v1.md`
  (this file) rather than CLAUDE.md's generic `.gemini/reports/<NAME>.md` — followed the more
  specific instruction, which also matches where every other report in this repo actually
  lives (`docs/process/reports/`).
- Branch: per explicit mid-task instruction from the user, this work is on
  `feat/notification-service` and will be pushed there (not `main`) pending review, rather
  than CLAUDE.md's default "routine work goes straight to main." The `order-listener` gate
  itself is unaffected — still not touched, still awaiting explicit "go".
- Five real bugs (listed above) were found via actual verification and fixed inline as part of
  Phase 1, since they were required to make the pasted verification output true rather than
  aspirational.

## Phase 6 — order-listener hooks (after explicit "go")

Implemented exactly the gate proposal above, no deviations:
- `order-listener/app/redis_client.py` — added `emit_notification(event, payload)`, wrapped in
  try/except, never raises.
- `order-listener/app/webhook_handler.py`:
  - Import line: added `emit_notification` to the existing `from app.redis_client import ...`.
  - `_create_strategy_position`: the `INSERT` now has `RETURNING id` (`conn.fetchval` instead
    of `conn.execute`), followed by `await emit_notification("position.opened", {...})` after
    the `async with pool.acquire()` block closes.
  - `close_strategy_position`: the initial `SELECT` now also fetches `entry_price, opened_at`;
    `await emit_notification("position.closed", {...})` is guarded on `if is_full:`, placed
    right after the existing `logger.info(...)` call and before `_book_realized_pnl`. Partial
    closes never reach it.

### Redeploy — clean, no import/startup errors

```
$ docker compose ps order-listener
NAME                    IMAGE                 COMMAND                  SERVICE          CREATED          STATUS                    PORTS
matp-order-listener-1   matp-order-listener   "uvicorn app.main:ap…"   order-listener   30 seconds ago   Up 26 seconds (healthy)   8001/tcp

$ docker compose logs order-listener --tail 30
...
2026-07-04 12:03:37,674 [INFO] app.main: Starting Order Listener service...
2026-07-04 12:03:39,151 [INFO] app.database: Database pool initialized.
2026-07-04 12:03:39,158 [INFO] app.redis_client: Redis client initialized.
2026-07-04 12:03:39,161 [INFO] app.main: Order Listener ready.
2026-07-04 12:03:39,266 [INFO] app.main: Reconciler loop started (interval=60s, threshold=3)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)

$ curl -sf http://localhost/api/listener/health
{"status":"ok","service":"order-listener"}
```

(The `service.down`/`service.up` pair visible in the stream dump below, for `order-listener`,
is the health-watcher correctly detecting this exact redeploy's brief downtime — an
incidental real-world proof that edge-triggered service health detection works, not part of
the planned test.)

### End-to-end verification — real webhook path, not a synthetic `XADD`

Used the pre-existing `tv_test_harness` strategy (`account_id=blofin-blofin-demo-v5vr`, a
BloFin **demo/paper** account — this strategy has a long history of small BTC-USDT test
trades in this repo, all previously closed; zero real funds at risk) to drive a real
`open_long` → `close_long` round trip through the actual `/webhook/{strategy_id}` endpoint,
not a manual stream injection.

**Open (`signal=open_long`, size 0.002 BTC-USDT):**
```
$ curl -s -X POST http://localhost/api/listener/webhook/tv_test_harness \
  -H "Content-Type: application/json" \
  -d '{"base_asset":"BTC","quote_asset":"USDT","side":"buy","order_type":"market","size":0.002,
       "signal":"open_long","timestamp":"...","token":"...","signal_source":"phase6-verification"}'
{"order_id":"ce49b2f6-5546-4edc-b6ff-fd76c4ce2524","status":"received","message":"OK"}

$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT id, status, actual_fill_price FROM orders WHERE id='ce49b2f6-5546-4edc-b6ff-fd76c4ce2524';"
                  id                  | status | actual_fill_price
--------------------------------------+--------+-------------------
 ce49b2f6-5546-4edc-b6ff-fd76c4ce2524 | filled |           62524.3

$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT id, symbol, side, size, entry_price, status FROM strategy_positions WHERE strategy_id='tv_test_harness' AND status='open';"
                  id                  |  symbol  | side |          size           | entry_price | status
--------------------------------------+----------+------+-------------------------+-------------+--------
 c02f2434-a729-452d-bc0d-bf8ed53921c2 | BTC-USDT | long | 0.002000000000000000000 |     62524.3 | open
```

**Stream entry for the open (`XRANGE notifications:events`):**
```
1783166725543-0
data
{"event": "position.opened", "position_id": "c02f2434-a729-452d-bc0d-bf8ed53921c2",
 "strategy_id": "tv_test_harness", "symbol": "BTC-USDT", "side": "long",
 "size": "0.002000000000000000000", "entry_price": "62524.3", "leverage": 10,
 "opened_at": "2026-07-04T12:05:25.537570+00:00"}
```

**Close (`signal=close_long`, same size):**
```
$ curl -s -X POST http://localhost/api/listener/webhook/tv_test_harness \
  -H "Content-Type: application/json" \
  -d '{"base_asset":"BTC","quote_asset":"USDT","side":"sell","order_type":"market","size":0.002,
       "signal":"close_long","timestamp":"...","token":"...","signal_source":"phase6-verification"}'
{"order_id":"63e13808-3902-4177-a33d-580adf0eb254","status":"received","message":"OK"}

$ docker compose exec -T postgres psql -U matp -d matp -c \
  "SELECT id, status, closing_price, pnl_realized, closed_at FROM strategy_positions WHERE id='c02f2434-a729-452d-bc0d-bf8ed53921c2';"
                  id                  | status |    closing_price    | pnl_realized |           closed_at
--------------------------------------+--------+----------------------+--------------+-------------------------------
 c02f2434-a729-452d-bc0d-bf8ed53921c2 | closed |            62532.9   |       0.0172 | 2026-07-04 12:06:28.350225+00
```

**Both `notification_log` rows for this position — the close row proves the "complete,
don't replace" spec (same `tag`/dedup base, body carries both legs):**
```
   event_type    |                      dedup_key                       |            title            |                                    body                                    | status
-----------------+------------------------------------------------------+------------------------------+-----------------------------------------------------------------------------+--------
 position.opened | position:c02f2434-a729-452d-bc0d-bf8ed53921c2:opened | 🟢 Opened BTC-USDT LONG 10x | 0.002 @ 62524.3000                                                          | failed
 position.closed | position:c02f2434-a729-452d-bc0d-bf8ed53921c2:closed | 🟢 Closed BTC-USDT LONG      | Entry LONG 0.002 @ 62524.3000 → Exit @ 62532.9000  •  PnL +0.02  •  manual  | failed
```
(`status='failed'` on both is expected and correct — the only registered `push_subscriptions`
row is the fake test device from Phase 1 verification with garbage key material; no real
phone is subscribed in this environment. The point proven here is the full pipeline —
webhook → DB write → stream emit → dedup → render → sink dispatch → log — not a successful
phone delivery.)

**No regression to order handling — listener logs show the exact same flow as any other
close (executor call, fill, position update), with zero errors/exceptions/warnings across
the whole test window:**
```
2026-07-04 12:06:24,517 [INFO] app.redis_client: Published to Redis channel orders:received: orders:received
INFO:     172.18.0.15:35782 - "POST /webhook/tv_test_harness HTTP/1.1" 200 OK
2026-07-04 12:06:24,546 [INFO] app.executor_client: Calling executor close-position: account=blofin-blofin-demo-v5vr symbol=BTC-USDT side=long size=None
2026-07-04 12:06:28,345 [INFO] httpx: HTTP Request: POST http://order-executor:8004/close-position "HTTP/1.1 200 OK"
2026-07-04 12:06:28,386 [INFO] app.webhook_handler: Closed position c02f2434-a729-452d-bc0d-bf8ed53921c2 for strategy tv_test_harness (BTC-USDT long), close_size=0.002, fill=62532.9, pnl=0.0172
2026-07-04 12:06:28,497 [INFO] app.redis_client: Published to Redis channel orders:filled: orders:filled

$ docker compose logs order-listener --since 5m | grep -iE "error|exception|traceback|warning" | grep -v "GET /health"
(empty)
```

notification-service is now feature-complete for v1 scope. Remaining gaps are tracked in
`docs/ROADMAP.md`'s Deferred Backlog (iOS push, TelegramSink, threshold PnL updates, etc.).
