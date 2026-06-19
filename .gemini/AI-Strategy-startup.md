# Investigation: AI Strategy Not Firing After Creation

**Date:** 2026-06-19  
**Strategy:** `ai-btc-6f8c` (AI BTC, BTC-USDT, HyperLiquid)

---

## Symptom

A newly created AI strategy never fires — no LangGraph cycles, no signal log entries,
no orders. The `ai-signal-generator` service is running and healthy.

---

## Root Cause

`start_all_schedulers` runs **once at service startup** (inside the FastAPI lifespan
context in `main.py:53`). It queries all enabled AI strategies at that moment and
launches one `AdaptiveScheduler` per result. If a strategy is created after the service
has already started, it is never picked up.

### Timeline

| Event | Time (UTC) |
|-------|-----------|
| `ai-signal-generator` container started | 2026-06-19 06:24:17 |
| `ai-btc-6f8c` strategy + `ai_strategy_config` row created | 2026-06-19 07:37:22 |

The strategy was created **73 minutes after** the service started. The startup query
had already run and returned zero rows. No scheduler was ever instantiated.

### Confirmation

```
GET /internal/schedulers
→ {"schedulers": [], "count": 0}
```

Zero schedulers running despite a valid, enabled strategy with a complete
`ai_strategy_config` row in the DB.

---

## Relevant Code

**`ai-signal-generator/app/main.py:53`**
```python
schedulers = await start_all_schedulers(pool, graph)   # called once at boot
```

**`ai-signal-generator/app/scheduler.py:369–391`** — `start_all_schedulers`:
```python
rows = await conn.fetch("""
    SELECT s.id
    FROM strategies s
    JOIN ai_strategy_config a ON a.strategy_id = s.id
    WHERE s.enabled = true
      AND COALESCE(s.is_deleted, false) = false
""")
for row in rows:
    scheduler = AdaptiveScheduler(sid, db_pool, graph)
    await scheduler.start()
    schedulers[sid] = scheduler
```

The event watcher loops (`event_watcher.py`) have the same startup-only limitation —
they are also launched per-strategy at boot, not on strategy creation.

---

## Fix Options

### Immediate (no code change)
Restart `ai-signal-generator`. `start_all_schedulers` will re-run and pick up the
strategy. Use `./scripts/redeploy.sh ai-signal-generator` (no `--clean` needed —
no source changed).

### Permanent fix (code change needed)
Add a `POST /internal/schedulers` endpoint that registers and starts a scheduler for
a given `strategy_id` dynamically:

```python
@app.post("/internal/schedulers")
async def register_scheduler(body: RegisterRequest):
    pool = get_pool()
    sid  = body.strategy_id
    if sid in app.state.schedulers:
        return {"status": "already_running", "strategy_id": sid}
    scheduler = AdaptiveScheduler(sid, pool, app.state.graph)
    await scheduler.start()
    app.state.schedulers[sid] = scheduler
    # also start its event watcher
    ...
    return {"status": "started", "strategy_id": sid}
```

`dashboard-api` would call this endpoint after saving a new AI strategy row, so
operators never need to restart the service manually.

---

## DB State at Time of Investigation

```
strategies row:
  id=ai-btc-6f8c, enabled=true, webhook_enabled=true,
  strategy_source=ai_engine, symbol=BTC-USDT,
  account_id=hyperliquid-hyperliquid-hqdy

ai_strategy_config row:
  strategy_id=ai-btc-6f8c, dry_run=false,
  interval_no_position=4h, interval_position_open=15m, interval_at_risk=5m,
  llm_provider=google, llm_model=gemini-2.5-flash,
  confidence_threshold=0.720, cooldown_entry_minutes=0

ai_risk_config: no row (LEFT JOIN returns NULL — treated as defaults by scheduler)
```
