# Outcome Instrumentation — 2026-07-28

Three phases of write-only telemetry closing the gaps
`docs/process/reports/2026-07-28-paper-trade-forensics.md` §4 identified. **No trading
behaviour was changed by any of this work.** Nothing added here is read by any branch that
places, sizes, gates, or exits a trade.

| Phase | What | Migration | Status |
|---|---|---|---|
| 1 | MFE/MAE on positions | 069 | **Done, verified live** |
| 2 | Decision-time mark price on orders | 070 | Deployed; **verification incomplete** — see §2.3 |
| 3 | Regime values on AI signals | 071 | **Done, verified live** |

---

## 1. Phase 1 — MFE/MAE excursion on positions

### What was added

Migration 069 adds seven columns to `strategy_positions`: `mfe_price`, `mae_price`,
`mfe_r`, `mae_r`, `excursion_samples`, `excursion_first_at`, `excursion_last_at`.

The reconciler (`order-listener/app/reconciler.py`) now folds one mark price per open
position into those columns on every pass, reusing the existing 60s cadence and the
`strategies` join that already resolves `account_id` (`strategy_positions` has no
`account_id` of its own). Reads run concurrently via `asyncio.gather`; a `None` mark is
skipped **without** incrementing `excursion_samples`; the whole block is wrapped so a
telemetry failure can never disturb reconciliation.

R uses the same definition as the forensics report — `|entry − opening order sl_price|`
with `COALESCE(actual_fill_price, entry_price)` as entry — so old and new numbers stay
comparable. Missing stop or zero denominator leaves `mfe_r`/`mae_r` NULL rather than
writing a garbage number.

### The sampling-resolution caveat, plainly

**These columns are a floor, not the true extreme.** Sampling once per ~60s cannot see a
wick between two reads. So:

- true |MFE| ≥ |mfe_price − entry|, true |MAE| ≥ |mae_price − entry|
- a trade stopped out between samples can show an `mae_r` milder than −1R even though
  price demonstrably traded through the stop
- `mae_r` can be **positive** and `mfe_r` **negative** — those mean "never observed at a
  loss" and "never observed in profit", not zero excursion

**Always read these against `excursion_samples`, and compare `excursion_first_at` against
`opened_at`.** A position first sampled hours after entry has no record of what happened
before. This is stated in the migration header and in every column comment.

### Verification

```
$ ls db/migrations | tail -5
068_social_add_and_levels.sql
069_position_excursion.sql
070_order_decision_mark_price.sql
071_ai_signal_regime_snapshot.sql
_archive
README.md

$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/069_position_excursion.sql
ALTER TABLE
COMMENT (×7)
CREATE INDEX
NOTICE:  Migration 069 verified OK: excursion columns present on strategy_positions
         (existing rows intentionally left NULL — not backfillable)
```

Schema:

```
 mfe_price               | numeric                  |           |          |
 mae_price               | numeric                  |           |          |
 mfe_r                   | numeric                  |           |          |
 mae_r                   | numeric                  |           |          |
 excursion_samples       | integer                  |           | not null | 0
 excursion_first_at      | timestamp with time zone |           |          |
 excursion_last_at       | timestamp with time zone |           |          |
```

Live sampling across successive reconciler passes, with the mark moving between reads
(BNB's favourable extreme 566.07 vs its adverse extreme 567.12 are from different passes):

```
  symbol  | side  | entry_price | mfe_price | mae_price | mfe_r  | mae_r  | excursion_samples |      excursion_first_at       |       excursion_last_at
----------+-------+-------------+-----------+-----------+--------+--------+-------------------+-------------------------------+-------------------------------
 BNB-USDT | short |      572.37 |    566.07 |    567.12 | 1.0547 | 0.8790 |                 4 | 2026-07-28 14:03:19.754681+00 | 2026-07-28 14:05:27.293732+00
 BTC-USDT | short |     65385.3 |   63128.9 |   63231.9 | 0.7581 | 0.7235 |                 4 | 2026-07-28 14:03:19.779355+00 | 2026-07-28 14:05:27.314891+00
 SOL-USDT | short |       73.61 |      72.7 |     72.88 | 1.4089 | 1.1302 |                 4 | 2026-07-28 14:03:19.672469+00 | 2026-07-28 14:05:27.274672+00
```

Reconciler logs, clean:

```
2026-07-28 14:03:19,839 [INFO] app.reconciler: reconciler: excursion sampled 3/3 open position(s)
2026-07-28 14:04:23,703 [INFO] app.reconciler: reconciler: excursion sampled 3/3 open position(s)
2026-07-28 14:05:27,347 [INFO] app.reconciler: reconciler: excursion sampled 3/3 open position(s)
```

### First real payoff, within hours

`sol-ai-6486`'s short closed at 15:03 with 57 samples recorded:

```
 symbol   | status | entry | sl_at_entry | tp_at_entry | mfe_price | mfe_r | closing_price | pnl
 SOL-USDT | closed | 73.61 |     74.2559 |     72.3783 |     72.55 | 1.641 |       73.5025 | 0.0523
```

**The trade ran to at least +1.64 R and closed at +0.17 R** — it gave back nearly all of it
to a trailed stop, for +0.05 USDT. That is exactly the "winner given back" pattern the
forensics report could only infer from the R histogram (21 of 81 AI trades bunched in the
0..0.4R band). It is now a measured fact on a specific trade rather than an inference.

The caveat applies in full: sampling began at 14:03 and the position opened at 01:01, so 13
of its 14 hours are unobserved. The true MFE may be higher.

---

## 2. Phase 2 — decision-time mark price on orders

### The `indicator_price` finding — the report was wrong, and it mattered

The forensics report proposed reusing `orders.indicator_price`, calling it "exists but is
unused". **That is true of the data and false of the code**, and acting on it would have
changed trading behaviour.

- **Data:** 0 of 508 rows populated. Genuinely empty.
- **Code:** very much alive. It is an accepted webhook payload field
  (`order-listener/app/models.py:61`) and the **first term of the sizing reference price**:

  ```python
  _ref_price = float(payload.indicator_price or payload.price or 0)
  ```

  in `webhook_handler.py:879`, which feeds the margin-per-trade size clamp, the
  guaranteed-SL injection and the min-order-size estimate (`:1667`). It is read again as an
  entry-price fallback at `:1016`, `:1077`, `:1746`, and rendered by two dashboard-ui pages
  (`Orders.tsx:329`, `StrategyTree.tsx:1204`).

Writing a mark price into it would have silently altered position sizes and stop placement.
**A separate column was added instead**: `orders.mark_price_at_decision` (migration 070).
The forensics report has been corrected in place.

### What was added

`order-listener` captures the exchange mark at order creation and passes it to `_log_order`.
Slippage is computed **at read time**, not stored:

```
long  entry: (actual_fill_price - mark_price_at_decision) / mark_price_at_decision
short entry: (mark_price_at_decision - actual_fill_price) / mark_price_at_decision
```

Cost control: a market open already fetches a mark for sizing, so that value is reused —
**zero extra executor calls on the dominant entry path** (market orders are 148 of 163
historical entries and never carry a price). Only priced limit orders and close signals add
one bounded 10s call that never raises.

### 2.3 Where instrumentation could not be populated, and why

| Case | Behaviour | Reason |
|---|---|---|
| Synthetic close orders | NULL, by design | Written after the fact by `reconciler.py:993/1163` and `ai-signal-generator/app/scheduler.py:322` when an external close is discovered. There was no local decision instant for a mark to belong to; inventing one would make exit-slippage figures fictional. |
| Mark-price read fails | NULL | `get_mark_price` returns None and never raises; the order proceeds unchanged. Telemetry never blocks a trade. |
| Rows before 2026-07-28 | NULL | Not backfillable — no historical marks are kept. |

### Verification status — INCOMPLETE, stated plainly

**Phase 2 does not have the verification the brief asked for.** It required populated
snapshot values alongside `actual_fill_price` on at least three real orders placed after
deploy. In the ~4.5 hours since deploy, **one** order arrived:

```
  symbol  |     signal     | order_type | status | mark_at_decision |  actual_fill_price  |          received_at
----------+----------------+------------+--------+------------------+---------------------+-------------------------------
 SOL-USDT | exchange_close | market     | filled |                  | 73.5025             | 2026-07-28 15:03:47.578805+00
```

That row is NULL — and **correctly so**: it is the reconciler's synthetic close order for
SOL's external stop-out, exactly the documented NULL case above. So it is a small positive
signal for the design, but it is not the verification required.

Order flow simply stopped: SOL closed at 15:03, leaving only BNB and BTC open, and the
scheduler moved to candle-close cadence. No entry has been attempted since. Synthetic
close-to-flat webhooks were considered and rejected as verification — they produce no
`actual_fill_price`, so they would prove nothing.

**This phase should be treated as unverified until three genuine filled orders land.** The
query to run:

```sql
SELECT symbol, signal, order_type, status, mark_price_at_decision, actual_fill_price,
       CASE WHEN side = 'buy'
            THEN (actual_fill_price - mark_price_at_decision) / mark_price_at_decision
            ELSE (mark_price_at_decision - actual_fill_price) / mark_price_at_decision
       END * 100 AS slippage_pct
FROM orders
WHERE mark_price_at_decision IS NOT NULL AND actual_fill_price IS NOT NULL
ORDER BY received_at;
```

### Process note

Phase 2's files (migration 070, the `webhook_handler.py` change, the ROADMAP additions, the
forensics correction) were swept into commit `ca4e57e` — a chart fix — by a careless
`git add -A`. They landed under a misleading message and before verification. History is
already pushed and is not being rewritten; recording it here instead so the commit log is not
read at face value.

---

## 3. Phase 3 — regime values on `ai_signal_log`

### What was added

Migration 071 adds `ai_signal_log.regime_snapshot jsonb`, built in
`node_dispatch.py::_regime_snapshot` from the same fetched state that produces
`missing_inputs`, so requested / delivered / value stay mutually consistent on every row.

Captured, as a compact numeric summary (not the whole payload):

| Key | Fields |
|---|---|
| `volatility_regime` | `atr_percentile`, `bb_width_percentile`, `squeeze_flag` |
| `funding_rate` | `rate`, `interpretation` |
| `fear_greed` | `value`, `label` |
| `open_interest` | `change_24h_pct`, `long_short_ratio` |
| `cvd` | `cvd_window_usd`, `cvd_trend`, `cvd_divergence` |
| `mtf_structure` | `{timeframe: trend_direction}` |
| `btc_dominance` | `value`, `trend` |

### On "BTC trend"

The brief asked for BTC trend "if available". **It is not available**, and neither near-miss
is a substitute:

- `btc_dominance` is BTC's share of total market cap, not its trend, **and it has been
  disabled on every strategy since 2026-07-05**. Expect the key to be absent on all current
  rows — confirmed absent on all six verification rows.
- `mtf_structure` is the trend of the **strategy's own symbol** across 1h/4h/1d. For a BTC
  strategy that is BTC's trend; for `eth-ai-34d2` it is ETH's. It must not be read as a
  market-wide regime.

A true BTC-trend regime field would have to be fetched. It does not exist today.

### The three-state convention

The brief required that "requested but missing" stay distinguishable from "not requested".
It does:

| State | Encoding | Meaning |
|---|---|---|
| key **absent** | `NOT (regime_snapshot ? 'x')` | never enabled; says nothing about the market |
| key present, **null** | `regime_snapshot->'x' = 'null'::jsonb` | enabled but did not arrive — the model decided blind to it |
| key present, **value** | otherwise | delivered, and this is what the model saw |

An empty `{}` (strategy enabled none of these) stays distinct from a pre-071 NULL row.

### Verification

```
$ docker compose exec -T postgres psql -U matp -d matp < db/migrations/071_ai_signal_regime_snapshot.sql
ALTER TABLE
COMMENT
CREATE INDEX
NOTICE:  Migration 071 verified OK: ai_signal_log.regime_snapshot present
         (existing rows intentionally left NULL — not backfillable)
```

```
 regime_snapshot       | jsonb                    |           |          |
```

**All three states, on six real signals generated after deploy**, with `key_is_null`
agreeing with `missing_inputs` on every row:

```
  id  |        strategy_id         | cfg_requests_fg | key_present | key_is_null | delivered_value | in_missing_inputs |       state
------+----------------------------+-----------------+-------------+-------------+-----------------+-------------------+--------------------
 5189 | xrp-ai-3844                | t               | t           | f           | 29              | f                 | DELIVERED
 5190 | xrp-ai-3844                | t               | t           | t           |                 | t                 | REQUESTED, MISSING
 5191 | eth-ai-34d2                | t               | t           | t           |                 | t                 | REQUESTED, MISSING
 5192 | ai-btc-6f8c                | f               | f           |             |                 | f                 | NOT REQUESTED
 5193 | sol-ai-6486                | t               | t           | t           |                 | t                 | REQUESTED, MISSING
 5194 | tao-ai-range-rotation-d257 | t               | t           | t           |                 | t                 | REQUESTED, MISSING
```

A row where a requested field was missing (5190 — `fear_greed` present as an explicit null,
matching `missing_inputs = {fear_greed}`), beside one where it was never requested (5192 —
key entirely absent, `missing_inputs` empty):

```
  id  | strategy_id | missing_inputs |             regime_snapshot
------+-------------+----------------+-----------------------------------------
 5190 | xrp-ai-3844 | {fear_greed}   | {
      |             |                |     "cvd": {"cvd_trend":"rising","cvd_divergence":"none","cvd_window_usd":-3236087.82},
      |             |                |     "fear_greed": null,
      |             |                |     "funding_rate": {"rate":0.00016533439250458043,"interpretation":"neutral"},
      |             |                |     "mtf_structure": {"1d":"downtrend","1h":"downtrend","4h":"downtrend"},
      |             |                |     "open_interest": {"change_24h_pct":-2.66,"long_short_ratio":null},
      |             |                |     "volatility_regime": {"squeeze_flag":false,"atr_percentile":82.0,"bb_width_percentile":73.0}
      |             |                | }
 5192 | ai-btc-6f8c | {}             | {
      |             |                |     "cvd": {"cvd_trend":"rising","cvd_divergence":"none","cvd_window_usd":1602736.95},
      |             |                |     "funding_rate": {"rate":0.0000039241,"interpretation":"neutral"},
      |             |                |     "mtf_structure": {"1d":"downtrend","1h":"downtrend","4h":"sideways"},
      |             |                |     "open_interest": {"change_24h_pct":-5.44,"long_short_ratio":null},
      |             |                |     "volatility_regime": {"squeeze_flag":false,"atr_percentile":91.5,"bb_width_percentile":39.5}
      |             |                | }
```

Note `open_interest.long_short_ratio: null` on both — that is a null *inside* a delivered
payload (the field arrived, that sub-value was unavailable), which is a different thing from
a top-level null and reads correctly as such.

Builder logic was also exercised offline before deploy, asserting that an unrequested source
yields an absent key, a requested-but-empty one yields an explicit null, and that null always
agrees with `missing_inputs`.

---

## 4. Deferred, as instructed

Appended to `docs/ROADMAP.md` Deferred Backlog rather than implemented — both touch
`order-executor` and the exchange fill record, a materially larger blast radius than
write-only telemetry:

- **Fee capture backfill.** `orders.exchange_fee` is only written on immediate fill
  (`reconciler.py:615`), so it is NULL on 61/163 opening and 78/163 closing orders.
  `sync_position_pnl` COALESCEs each gap to 0, so `pnl_realized` — and the strategy
  allocation booked from it, which the drawdown auto-disable reads — is optimistic by an
  unknown amount.
- **Per-position funding capture.** Never recorded anywhere. On perpetuals held across
  funding intervals this is a real unmeasured cost, so long holds look better than they were.

---

## 5. What is now answerable that was not

| Forensics §4 question | Status |
|---|---|
| Would stopped-out trades have reached target? | **Answerable going forward**, to 60s resolution |
| Are stops too tight or too wide? | **Answerable going forward**, same caveat |
| Does market regime affect performance? | **Answerable going forward** |
| How much did entry slippage cost? | **Instrumented, not yet verified** (§2.3) |
| How much did funding cost? | Still unanswerable — deferred |
| What were the true fees? | Still unanswerable — deferred |
| Did unfilled limit proposals turn out right? | Still unanswerable — out of scope |
| What exactly was the model shown? | Partially — regime values yes, full prompt no |

None of this makes the historical 163 trades any more diagnosable. It makes the next 163 so.
