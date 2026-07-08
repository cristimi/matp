# Social Listener — Live-Ingestion Catchup Gap Fix

**Date:** 2026-07-08
**Branch:** main
**Status:** DONE — deployed and verified

---

## Background

While evaluating the social-listener (Phase 2a shadow mode, running since 2026-06-27), found
that the live Telegram handler silently drops messages: comparing `posted_at` vs `ingested_at`
in `social_signal_log` showed messages 9578, 9586, 9587, 9588, 9589 were posted in real time but
only appeared in the DB 2-4 days later, right after an unrelated container restart (Gemini key
rotation) triggered the one-shot startup backfill.

```
 id  | channel_msg_id |       posted_at        |          ingested_at          |          lag
-----+----------------+------------------------+-------------------------------+------------------------
 146 |           9578 | 2026-07-04 23:34:15+00 | 2026-07-08 16:26:11.45616+00  | 3 days 16:51:56.45616
 147 |           9586 | 2026-07-04 23:36:29+00 | 2026-07-08 16:26:14.880586+00 | 3 days 16:49:45.880586
 148 |           9587 | 2026-07-05 22:37:32+00 | 2026-07-08 16:26:22.591236+00 | 2 days 17:48:50.591236
 149 |           9588 | 2026-07-05 22:37:55+00 | 2026-07-08 16:26:26.386786+00 | 2 days 17:48:31.386786
 150 |           9589 | 2026-07-05 22:37:58+00 | 2026-07-08 16:26:31.009742+00 | 2 days 17:48:33.009742
```

Root cause: `app/main.py` only reconciled Telegram history against the DB once, at process
startup (`backfill_limit` messages). After that it relied purely on Telethon's `NewMessage`
live event stream, with no reconnect/gap detection. Any event missed during a reconnect or
dropped-update window stayed missing until the *next full process restart* — which could be
days away given `restart: unless-stopped` and no code changes forcing a redeploy.

This was also a correctness risk beyond latency: replayed messages coming back through the
one-shot startup backfill go through `phase="backfill"` in `statemachine.evaluate()`
(`app/statemachine.py:67-68`), which **acts unconditionally** with no staleness check — the
staleness gate only applies to `phase="live"`. A dropped actionable signal (e.g. a CLOSE) could
therefore have been silently replayed days later at a stale reference price, bypassing the exact
protection built for live signals.

---

## Fix

Added a periodic reconciliation loop (`app/main.py:_catchup_loop`, default every 60s,
`catchup_interval_seconds` / `catchup_limit` in `app/config.py`) that:

1. Reads the highest `channel_msg_id` already recorded (`db.max_channel_msg_id()`, new in
   `app/db.py`).
2. Fetches any Telegram messages newer than that (`client.iter_messages(channel, min_id=last_id,
   reverse=True)`) — oldest first.
3. Replays them through `handle(m, "live")` — the same path a live event takes, so mark-price
   fetch + staleness gating apply. No more unconditional "backfill" replay for messages missed
   in real time.

`handle()`'s existing `already_shadow_evaluated`/`already_seen` guards plus the DB's
`ON CONFLICT DO NOTHING` constraints make this safe against races with the live event handler
processing the same message concurrently.

### Files changed
- `social-listener/app/config.py` — `catchup_interval_seconds: int = 60`, `catchup_limit: int = 200`
- `social-listener/app/db.py` — `max_channel_msg_id()`
- `social-listener/app/main.py` — `_catchup_loop()`, scheduled via `asyncio.create_task()` after
  the live handler is registered

---

## Verification

Redeploy:
```
$ ./scripts/redeploy.sh social-listener
...
 Image matp-social-listener Built
▶ Recreating social-listener …
 Container matp-social-listener-1 Recreated
 Container matp-social-listener-1 Started
▶ Verifying …
NAME                     IMAGE                  COMMAND                SERVICE           CREATED          STATUS         PORTS
matp-social-listener-1   matp-social-listener   "python -m app.main"   social-listener   18 seconds ago   Up 4 seconds
✓ social-listener redeployed.
```

Startup log — clean connect, backfill no-op (already caught up from the earlier manual restart):
```
2026-07-08 16:46:50,247 INFO app.db DB pool initialized
2026-07-08 16:46:50,249 INFO telethon.network.mtprotosender Connecting to 149.154.167.92:443/TcpFull...
2026-07-08 16:46:50,280 INFO telethon.network.mtprotosender Connection to 149.154.167.92:443/TcpFull complete!
2026-07-08 16:46:50,626 INFO social-listener Telegram connected as 8833405539
2026-07-08 16:46:50,704 INFO social-listener Backfilling last 50 messages from AstronomerZero
2026-07-08 16:46:51,678 INFO social-listener Backfill complete (50 messages)
2026-07-08 16:46:51,680 INFO social-listener Listening for new messages...
```

After waiting past the first 60s catchup tick — container still up, zero restarts, no
exceptions (channel had no new activity in the window, so the loop had nothing to recover,
which is the expected quiet-path behavior):
```
$ docker compose ps social-listener
NAME                     IMAGE                  COMMAND                SERVICE           CREATED         STATUS              PORTS
matp-social-listener-1   matp-social-listener   "python -m app.main"   social-listener   2 minutes ago   Up About a minute

$ docker compose logs social-listener --since 5m | grep -iE "error|exception|traceback"
(no output — clean)
```

Safety re-check (still no execution path):
```
$ grep -rn 'order.listener\|order.executor\|webhook\|8001\|8004' social-listener/app/
(no output — clean)
```

## Definition of Done

- [x] Root cause identified with real DB evidence (posted_at vs ingested_at lag).
- [x] Periodic catchup loop implemented, replaying missed messages via the live (staleness-gated)
      path instead of the unconditional-act backfill path.
- [x] Deployed via `./scripts/redeploy.sh social-listener`; container healthy, zero restarts.
- [x] No exceptions in the catchup loop across its first tick.
- [x] Execution-path safety re-verified clean (shadow-only, no order-listener/executor calls).
