# Pricing the bracket where the trade fills, an R:R floor, honest trend lines, readable logs

Follow-up to `btc-ai-position-chart-investigation.md` and
`chart-candles-from-the-trades-own-market.md`. Four things asked for and done:

1. the AI's stop/target are now priced against the market the order fills in;
2. they are re-anchored to the price the position actually opened at;
3. an entry whose reward/risk is below **1.0** is refused before the exchange;
4. the stale "trend lines" are gone, and `docker logs` works again.

---

## 1 + 2. The bracket now belongs to the market that fills it

### What was wrong

`ai-signal-generator` fetches candles through ccxt with no sandbox flag
(`app/data/ohlcv.py:105-109`), so a **demo** account's model reasons about
**mainnet** prices. `node_guard` then turned the model's percentages into absolute
prices using that same mainnet price, and the order filled on testnet ~1% away.
The LLM asked for a 1.0% stop and a 1.5% target (R:R **1.5**); solving the stored
levels back proves it:

```
sl=62654.1  tp=64236.305
cp=63287.0  ->  sl_pct=1.0000  tp_pct=1.5000   (best fit of 8000 candidates, err 5e-5)
```

confirmed independently by the sizing metadata (`0.0032 × 63287 × 1.0% = 2.02 USD`
≈ the recorded `effective_risk_usd: 2.0`). What reached the exchange:

```
cp=63287.0 (AI's mainnet price) -> sl 1.0000%  tp 1.5000%  R:R 1.50   ← asked
cp=63786.0 (testnet mark)       -> sl 1.7745%  tp 0.7060%  R:R 0.40
cp=63918.4 (actual fill)        -> sl 1.9780%  tp 0.4974%  R:R 0.25   ← got
```

### The change

**Step 1 — the AI sends distances, order-listener prices them.** `node_guard` no
longer converts percentages to prices for market entries; it passes
`resolved_sl_pct` / `resolved_tp_pct`, the dispatcher sends `sl_pct` / `tp_pct`, and
`webhook_handler` prices them against `_ref_price` — the account's own exchange
mark, which it was already fetching for sizing. Limit entries are untouched: there
the limit price *is* the entry, so the bracket is already anchored correctly.

**Step 2 — re-anchor to the real fill.** `stop_revalidation.py` already repaired
wrong-side and degenerate stops after a fill, but a merely *degraded* one looked
valid: 0.50% is more than the 0.1% floor, so it was left alone. It now takes a
`distance_based` flag — set when the bracket arrived as percentages — and then
re-anchors any leg whose distance drifted more than 2% of itself. A level-based
bracket (TradingView sending structural support/resistance) keeps the old
conservative behaviour, because there the level, not the distance, is the request.

Composed, on the incident's own numbers:

```
$ pytest tests/test_stop_revalidation.py::test_distance_based_reanchors_a_slipped_bracket -v
PASSED
    asked 1.0% / 1.5% off 63786, filled 0.21% higher at 63918.4
    → sl 63279.22   tp 64877.18   reward/risk 1.5   (was 0.25)
```

## 3. Reward/risk floor of 1.0

New guard in `webhook_handler`, after the guaranteed-SL injection and before the
order is created — the last point before the exchange, so it covers every signal
source, not just the AI. It judges only entries carrying **both** legs (reward/risk
is meaningless without a target, and most TradingView strategies send a stop only),
and measures both distances **signed** from the same reference, so a target on the
wrong side of the entry fails here too instead of hiding behind an absolute value.

Blast radius, measured before changing anything:

```sql
SELECT signal_source, count(*), min(rr), avg(rr), max(rr), count(*) FILTER (WHERE rr < 1)
```
```
  signal_source  | n  | min_rr | avg_rr | max_rr | below_1
-----------------+----+--------+--------+--------+---------
 ai_engine       | 99 |   0.18 |   4.80 |  84.60 |      17
 social_listener |  1 |   0.30 |   0.30 |   0.30 |       1
```

Only these two sources send a full bracket at all, and 17 of the AI's last 99
entries would have been refused — which is the point.

## 4. Trend lines

Three separate faults, all three fixed:

- **A stale read was served.** `latestGeometry()` had no age limit, so a
  `no_pattern`/`weak` read from **2026-08-02** was returned for a strategy whose
  `use_geometry` is off. It now requires the read to be inside the charted window
  **and** newer than 24h. The age cap also stands in for the strategy's own switch:
  the generator writes `geometry_data` only while geometry is on, so a strategy with
  it off has nothing recent.
- **The boundary was pinned to the wrong bar.** `geometryLines.ts` assumed the
  boundary values belonged to the newest bar on screen. A ten-day-old fit projected
  back at 21.98/bar drifted 1648 points: the lower line sat below every candle and
  the "upper" line *started below the lower one*. It now pins them at
  `geometry_at`, where the fit was measured. Writing the test for this exposed a
  second half of the same bug — only the line's start point was being projected
  while its end was left at the raw boundary value, which bent the line to a value
  the fit never reported. Both endpoints are now projected.
- **A "no pattern" verdict was drawn as a pattern.** The old guard skipped a read
  only when both boundaries were 0; this read had real numbers alongside
  `shape: no_pattern`. The verdict is now trusted.

## 5. Logging

Both services had produced no readable output for 33 hours. The cause, found in the
container's own log file:

```
$ python -c "find the first NUL byte in the container json log"
order-listener      size 43490168  first NUL at 37926665  run 392 bytes
  before: ...{"log":"INFO: ... 200 OK\n","time":"2026-08-11T09:27:49.131441454Z"}
  after :    {"log":"INFO:     Started server process [1]\n","time":"2026-08-11T09:31:07.82688282Z"}

ai-signal-generator size 2979982  first NUL at 111824  run 130 bytes
  before: ..."time":"2026-08-11T09:27:30.787255952Z"}
  after :    {"log":"INFO:     Started server process [1]\n","time":"2026-08-11T09:31:28.796443482Z"}
```

The host stopped uncleanly at **09:27 on 2026-08-11** and every container's json log
was left with a few hundred NUL bytes where the unflushed tail had been; each
service restarted at 09:31 and kept appending after the hole. `docker logs` reads
the file sequentially and gives up at the hole, so it replayed only the hours
**before** the crash — while `--tail` seeks from the end and still worked, which is
exactly why the loss was easy to miss.

Fixed by adding rotation to every service (`x-logging` anchor, `max-size: 20m`,
`max-file: 5`) and recreating the containers, which replaces the damaged files. A
future hole can now only ruin the segment it lands in, and that segment ages out.
Unbounded was a problem in itself: order-listener's single log file had reached
**43 MB** with no ceiling.

```
$ docker inspect $(docker compose ps -q order-listener) --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}'
json-file map[max-file:5 max-size:20m]

$ docker compose logs order-listener | grep -oE "2026-08-12 [0-9][0-9]:" | sort -u | tail -3
2026-08-12 20:
```
(The full sequential dump reaches the current hour again, instead of stopping at
2026-08-11 09:27.)

---

## Verification

### Deployed code is the new code

```
$ docker compose exec -T order-listener sh -c 'grep -c "_MIN_RISK_REWARD" app/webhook_handler.py; grep -c "stops_from_pct" app/webhook_handler.py; grep -c "distance_based" app/stop_revalidation.py; grep -c "tp_pct" app/models.py'
3
4
5
1
$ docker compose exec -T ai-signal-generator sh -c 'grep -c "resolved_tp_pct" app/graph/nodes/node_guard.py app/webhook/dispatcher.py'
app/graph/nodes/node_guard.py:1
app/webhook/dispatcher.py:1
```

### Tests, run inside the live containers

```
$ docker compose exec -T order-listener python -m pytest tests -q
97 passed, 2 warnings in 72.20s

$ docker compose exec -T order-listener python -m pytest tests/test_webhook_handler.py tests/test_stop_revalidation.py -q
28 passed

$ docker compose run --rm --no-deps ... ai-signal-generator python -m pytest tests -q
129 passed

$ npx vitest run          (dashboard-ui)
Test Files  2 passed (2)     Tests  45 passed (45)

$ npx tsc --noEmit        (dashboard-api, dashboard-ui)   clean
```

The listener suite was **5 tests red before this work** and is green now. Two were
stale (`_create_strategy_position` moved from `conn.execute` to `conn.fetchval`),
two needed a mark price mocked (a market open is correctly refused without one), and
one tested the daily-signal cap that `db/migrations/030_drop_dead_columns.sql`
deliberately removed — that one is deleted. Verified pre-existing by running the
suite against the old image before deploying (`grep -c _MIN_RISK_REWARD` → 0 there).

New tests, 12 in the listener + 3 on the chart:

```
tests/test_webhook_handler.py
  test_pct_bracket_is_priced_from_the_exchange_mark       63000 → sl 62370.0, tp 63945.0
  test_pct_bracket_mirrors_for_a_short                    63000 → sl 63630.0, tp 62055.0
  test_reward_risk_below_floor_is_rejected                the incident's 0.5%/2% shape → 422
  test_reward_risk_exactly_one_is_allowed                 the floor is inclusive
  test_reward_risk_floor_also_judges_absolute_prices      applies to TradingView too
  test_target_on_the_wrong_side_is_rejected               signed distances, not abs()
  test_stop_only_open_is_not_judged_on_reward_risk        the common TV case is untouched
tests/test_stop_revalidation.py
  test_distance_based_reanchors_a_slipped_bracket         0.25 → 1.5 restored
  test_distance_based_leaves_an_unslipped_bracket_alone   no exchange call for nothing
  test_distance_based_tolerates_a_hair_of_slippage        1bp of slip is left alone
  test_distance_based_mirrors_for_a_short
  test_level_based_bracket_is_still_respected             structural levels kept
riskReward.test.ts
  refuses to draw a shape:no_pattern read even when it carries boundaries
  pins the boundary where the read was taken, not on the newest bar
  still anchors on the newest bar when the payload carries no geometry_at
```

### The stale trend lines are gone, live

```
$ GET /positions/9c43a165-.../candles          (ai-btc-6f8c, use_geometry off)
candle_source: exchange | exchange: hyperliquid demo
geometry: None | geometry_at: None
```

Before, this returned the 2026-08-02 read. And a strategy that really does fit
ranges still gets its lines — now landing on the candles:

```
$ GET /ai/signals/6183/candles                 (eth-ai-34d2, descending_channel)
window 08-09 08:15 -> 08-12 11:00 | geometry_at 08-12 01:00
shape descending_channel | boundaries u=1866.09 l=1851.92 | slopes u=-1.2223 l=-0.6348
upper line: 1917.43 -> 1853.87
lower line: 1878.59 -> 1845.58
candles low 1854.10 high 1938.00
swing points inside window: 12 of 23
```

Both lines now run through the price range with 12 swing points behind them to
attach to — against the earlier read, whose two lines sat at 61994→63643 and
62823→62474 under candles ranging 63211–65455, with **0 of 25** swings in view.

### Stack

```
$ docker compose ps
all 15 services up; order-listener, order-executor, dashboard-api,
ai-signal-generator, notification-service, strategy-tester healthy

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-DUubTE_U.js
$ docker compose exec -T dashboard-ui grep -rl 'Candles from' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-DUubTE_U.js
```

---

## Deliberately not changed

- **What the AI analyses.** Indicators, structure and regime still come from the
  venue's public market. Demo-market candles are thin and jumpy — one 15-minute
  testnet bar showed a 400-point spike on 0.03 BTC of volume — so training the
  model's *reading* on them would be worse, not more consistent. Only the pricing
  moved. This is the open question worth a decision.
- **Position sizing** still uses the analysis price, so on a demo account the size
  is off by whatever the two markets differ by (~1%). The listener's margin clamp
  already re-derives size from the real reference when the notional exceeds the cap.
- **Limit entries** anchor their bracket to the limit price, which is correct, but
  that limit price is still chosen from the analysis market — so on a demo account
  a resting order may sit somewhere the local market never reaches. Different
  failure (fill probability, not reward/risk); untouched here.
- A rejected-entry audit row is written to `ai_signal_log` / `strategy_webhook_calls`
  as usual when the new floor fires, so refusals are visible rather than silent.
