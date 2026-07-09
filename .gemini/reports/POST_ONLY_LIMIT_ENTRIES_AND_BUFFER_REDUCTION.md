# Post-only AI limit entries (HL + BloFin) and candle-close buffer reduction

Follow-up to `AI_LIMIT_ORDER_INSTANT_STOP_INVESTIGATION.md` (2026-07-09): two AI limit
entries filled at market because price had traded through the limit during the ~3-minute
decision-to-order latency, landing at/beyond their own stop loss.

## Changes

### 1. Hyperliquid adapter — ALO (post-only) for entry limits

`order-executor/app/adapters/hyperliquid.py`

- `_submit_order`: non-reduce-only limit orders now use `{"limit": {"tif": "Alo"}}`
  instead of `Gtc`. If the limit would immediately match as taker, Hyperliquid rejects it
  (`error` in the response status), which the existing parser already maps to
  `success=False, status="rejected"` — the executor does not retry `rejected`, so the
  order row lands as rejected with a clear error and the AI re-evaluates next cycle.
  Reduce-only (close) limits keep `Gtc` — taker fills are acceptable when exiting.
- `amend_order`: the modify action's replacement spec uses the same rule
  (`Alo` unless reduce-only), so amending a resting entry to a crossing price cancels
  instead of taker-filling.

### 2. BloFin adapter — `post_only` orderType for entry limits

`order-executor/app/adapters/blofin.py`

- `submit_order`: limit orders that are not closes are sent with
  `"orderType": "post_only"` (BloFin's documented order type: cancels the order if it
  would execute immediately as taker). Closes and market orders unchanged. The
  cancel-then-replace `amend_order` path builds its replacement with `signal="amend"`,
  so it inherits post_only automatically.
- Post-placement state check: BloFin *accepts* a crossing post_only order (code 0) and
  then cancels it, so the existing "not pending anymore → filled" fallback would have
  recorded a phantom fill. Added an explicit `state == "canceled"` branch returning
  `success=False, status="rejected"` with error
  `"post-only limit canceled by exchange: price already through the limit"`.

### 3. Candle-close buffer 150s → 10s

The 150s buffer existed "to give the exchange time to finalize the candle". Investigation
showed it is unnecessary at that size: ccxt `fetch_ohlcv` (no `since`) returns the most
recent candles including the just-closed one within ~1–2s of the boundary, and
`ohlcv.py::_split_closed_candles()` already drops the still-forming trailing candle by
timestamp — correctness never depended on the buffer. The buffer added 2.5 min of pure
decision-to-order latency.

- `db/migrations/050_reduce_candle_close_buffer.sql`: column default 150 → 10, existing
  rows at 150 moved to 10 (deliberate non-150 values untouched), self-verifying.
- `ai-signal-generator/app/scheduler.py`: fallback default 150 → 10.
- `db/init.sql`: default 150 → 10 for fresh installs.

Remaining latency is data collection (~2 min, dominated by sentiment/OI venue fetches) +
LLM (~30 s); that is untouched here — but with post-only entries a stale limit price can
no longer produce a bad fill, only a clean cancel.

## Verification (pasted output)

Migration applied to live DB:

```
BEGIN
ALTER TABLE
UPDATE 5
COMMIT
psql:<stdin>:48: NOTICE:  Migration 050 verified OK: default=10, no rows left at 150s
```

order-executor tests (inside the redeployed container):

```
..........                                                               [100%]
10 passed in 26.77s
```

New code confirmed inside the running containers:

```
$ docker compose exec -T order-executor grep -n "Alo" /app/app/adapters/hyperliquid.py
407:            tif = "Gtc" if reduce_only else "Alo"
912:                "t": {"limit": {"tif": "Gtc" if reduce_only else "Alo"}},
$ docker compose exec -T order-executor grep -n "post_only" /app/app/adapters/blofin.py
444:                wire_order_type = "post_only"
524:                            # post_only entry that would have crossed the market —
$ docker compose exec -T ai-signal-generator grep -n "candle_close_buffer_seconds', 10" /app/app/scheduler.py
115:        buffer_seconds = int(config.get('candle_close_buffer_seconds', 10))
```

Health after redeploy:

```
{"status":"ok","service":"order-executor","version":"1.0.0"}
{"status":"ok","service":"ai-signal-generator","collector":{"running":true,...}}
```

Scheduler wake times with the 10s buffer — wakes now target hh:00:10 instead of
hh:02:30. Wake-math check inside the container plus the first live scheduler line
after redeploy:

```
$ docker compose exec -T ai-signal-generator python -c "...seconds_until_aligned_wake('1h', 17:37:30, 10)..."
1h strategy finishing cycle at 17:37:30 wakes at 18:00:10 (sleep 1360s)

2026-07-09 17:38:29,063 [INFO] app.scheduler: Scheduler strategy=hype-breakout-da2e
sleeping 1301s until candle-close+buffer wake (21.7min)   # 17:38:29 + 1301s = 18:00:10
```

DB after migration:

```
    strategy_id     | candle_close_buffer_seconds
--------------------+-----------------------------
 all 5 strategies   |                          10
```

## Notes / expected new behavior

- A `place_limit_*` whose price has been overtaken now produces an order row with
  `status=rejected` and a descriptive `error_msg` instead of an instant-stopped position.
  This is the intended outcome; the AI simply re-evaluates on its next cycle.
- BloFin `post_only` maker-or-cancel semantics per BloFin docs
  (docs.blofin.com, "Order Types on BloFin"); Hyperliquid `Alo` = add-liquidity-only.
