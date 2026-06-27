# Social Listener Phase 2a — Shadow State Machine + Gates

**Date:** 2026-06-27  
**Branch:** main  
**Status:** DONE — all Definition-of-Done items verified

---

## Migration Result

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/029_social_state_shadow.sql
CREATE TABLE
CREATE TABLE
CREATE INDEX
NOTICE:  Migration 029 verified OK
DO
```

Both tables (`social_position_state`, `social_shadow_orders`) created and verified.

---

## Implementation Notes

### Fix applied during bring-up

The initial deploy had a flow bug: `handle()` returned early via `already_seen()` for all 50
backfill messages (already in `social_signal_log` from Phase 1). The brain never ran.

Fix: added `already_shadow_evaluated()` as the new early-return guard (checks `social_shadow_orders`),
and `load_signal()` to fetch the pre-extracted record from `social_signal_log` without re-calling
the LLM. On restart, already-evaluated messages are skipped entirely; already-extracted but not yet
brain-evaluated messages are replayed cheaply from DB.

### New files
- `social-listener/app/marketdata.py` — reads `candle:forming:blofin:{asset}-USDT:1m` from Redis, returns `c` field as float mark price
- `social-listener/app/statemachine.py` — pure evaluate() function: gates (confidence floor, whitelist), absolute-target state machine, staleness check

### Modified files
- `social-listener/app/config.py` — added `redis_url`, `ingestion_exchange`, `execution_mode`, `confidence_floor`, `staleness_pct`, `entry_on_missing_price`
- `social-listener/app/db.py` — added `already_shadow_evaluated`, `load_signal`, `get_state`, `set_state`, `insert_shadow_order`
- `social-listener/app/main.py` — wired brain into `handle(msg, phase)`, phase param passed from backfill loop and live handler
- `social-listener/requirements.txt` — added `redis[asyncio]>=4.2`
- `docker-compose.yml` — added `REDIS_URL` env var and `redis: service_healthy` depends_on to social-listener

---

## Verification Queries

### Final inferred state per asset
```
 asset | state | last_msg_id |          updated_at           
-------+-------+-------------+-------------------------------
 BTC   | LONG  |        9542 | 2026-06-27 10:55:47.696504+00
(1 row)
```

### Shadow decisions by reason
```
  phase   | decision |     reason      | count 
----------+----------+-----------------+-------
 backfill | acted    | backfill_replay |     1
 backfill | skipped  | low_confidence  |     1
 backfill | skipped  | no_state_change |     3
(3 rows)
```

### Transition trail (newest first)
```
 channel_msg_id |  phase   | asset | from_state | to_state | intended_signal | decision |     reason      | reference_price | mark_price 
----------------+----------+-------+------------+----------+-----------------+----------+-----------------+-----------------+------------
           9562 | backfill | BTC   | LONG       | LONG     | none            | skipped  | no_state_change |                 |           
           9560 | backfill |       | FLAT       | FLAT     | none            | skipped  | low_confidence  |                 |           
           9552 | backfill | BTC   | LONG       | LONG     | none            | skipped  | no_state_change |           62600 |           
           9544 | backfill | BTC   | LONG       | LONG     | none            | skipped  | no_state_change |           67000 |           
           9542 | backfill | BTC   | FLAT       | LONG     | open_long       | acted    | backfill_replay |           67000 |           
(5 rows)
```

---

## Analysis

**State trail correctness:** The channel produced exactly one actionable entry in the 50-message
backfill window — msg 9542, a BTC OPEN LONG at 67000. State advances FLAT→LONG and stays there.
Subsequent actionable-looking messages (9544, 9552, 9562) were already LONG→LONG, so they correctly
collapsed to `no_state_change`. State ends at BTC LONG, which matches the channel's current posture.

**Dup-flip collapse:** Three messages with `no_state_change` confirm the idempotent guard works —
duplicate LONG signals while already LONG are skipped without advancing state.

**Live decisions:** No live messages processed yet (container just restarted). The `stale_price` and
`priceless_market` paths will appear in live traffic. Mark fetching is wired: backfill always skips
the mark fetch; live priced signals hit Redis `candle:forming:blofin:{asset}-USDT:1m`.

**Safety:** `grep -rn 'order.listener\|order.executor\|webhook\|8001\|8004' social-listener/app/`
returned CLEAN — no execution calls anywhere.

**execution_mode:** defaults to `shadow`; `live` path logs a warning and behaves as shadow.

---

## Definition of Done

- [x] Migration 029 applied (`NOTICE: Migration 029 verified OK`); both tables present.
- [x] `marketdata.get_mark` implemented against real Redis mark source (`candle:forming:blofin:{asset}-USDT:1m`).
- [x] State machine + gates wired into `handle`; state advances only on `acted`.
- [x] Backfill builds coherent state trail (FLAT→LONG on first actionable signal); dup-flips collapse to `no_state_change`.
- [x] `execution_mode` defaults to `shadow`; NO order-listener/executor calls; no `/webhook` POSTs.
- [x] Report written with pasted real output.
