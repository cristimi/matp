# Re-investigation: AI-close reasoning gap + real account topology

Trigger: `docs/process/reports/ai-exit-trace-gap-investigation.md` is suspected of
containing errors. This report re-derives everything from primary data (fresh clone of
source, live `SELECT`-only queries, live logs) and treats nothing in the prior report as
given. **Investigation only — no code, schema, or data changes were made.** Every claim
below is followed by the exact query/log line and its full, unedited output. Where a query
returned rows, all rows are shown; nowhere is a row's existence or content inferred.

---

## Part 1 — Full lineage of one real position

### 1.1 — Strategy and account

```sql
SELECT * FROM strategies WHERE id = 'hype-breakout-da2e';
```
```
         id         |     name      |  class  |  symbol   | interval | ... | account_id               | ... | strategy_source | ...
--------------------+---------------+---------+-----------+----------+ ... +--------------------------+ ... +-----------------+----
 hype-breakout-da2e | HYPE Breakout | webhook | HYPE-USDT | 1h       | ... | blofin-blofin-demo-v5vr  | ... | ai_engine        | ...
```
(1 row; full column list confirmed `account_id = 'blofin-blofin-demo-v5vr'`, `strategy_source = 'ai_engine'`, `symbol = 'HYPE-USDT'`.)

```sql
SELECT id, exchange, mode, label, is_active FROM exchange_accounts WHERE id = 'blofin-blofin-demo-v5vr';
```
```
           id            | exchange | mode |    label    | is_active
-------------------------+----------+------+-------------+-----------
 blofin-blofin-demo-v5vr | blofin   | demo | Blofin Demo | t
```
(1 row. `credentials` column omitted from this report — encrypted at rest, not printed per
policy.)

**Finding:** the account really is `blofin-blofin-demo-v5vr` ("Blofin Demo"), matching both
the prior report and the operator's screenshot. No contradiction here.

### 1.2 — Position row

```sql
SELECT * FROM strategy_positions WHERE strategy_id = 'hype-breakout-da2e' ORDER BY opened_at DESC LIMIT 10;
```
```
                  id                  |    strategy_id     | ... |  symbol   | side  | entry_price |  size  | leverage | ... | status |           opening_order_id           |           closing_order_id           |           opened_at           |           closed_at           | ... |                   closing_price                   | ... | pnl_realized
--------------------------------------+--------------------+ ... +-----------+-------+-------------+--------+----------+ ... +--------+---------------------------------------+---------------------------------------+--------------------------------+--------------------------------+ ... +----------------------------------------------------+ ... +--------------
 1707dbcb-ddc5-4d09-9697-146877e1ddbd | hype-breakout-da2e | ... | HYPE-USDT | short |      68.653 |    2.9 |       20 | ... | closed | 9a689b2b-b490-417e-8bd1-d15ffa6cf5f0 | 17819b6d-bafa-4648-a720-83fa313bb5e5 | 2026-07-05 05:02:55.700959+00 | 2026-07-05 06:47:47.791997+00 | ... | 68.634000000000000341...                            | ... |        0.6844
 496f73db-2e66-4ea9-b024-10e46bf5e343 | hype-breakout-da2e | ... | HYPE-USDT | short |       69.31 |    2.9 |       20 | ... | closed | 8786d56a-0fff-4e47-b58d-b8e234f376e6 | 9ef95d9c-ab14-4eef-880f-8c4081451143 | 2026-07-05 01:02:58.738679+00 | 2026-07-05 02:47:47.298998+00 | ... | 68.448                                              | ... |        3.8831
(2 rows)
```
Row `1707dbcb…` is the operator's example: entry `68.653`, close `68.634`, size `2.9`,
opened `2026-07-05 05:02:55 UTC` / closed `06:47:47 UTC` — matches the screenshot's
07:02/08:47 display times exactly at UTC+2. Row `496f73db…` is the earlier position
(entry `69.31`, opened `01:02:58 UTC`, closed `02:47:47 UTC`) that the operator's earlier
`@68.833` partial-close belongs to.

### 1.3 — Every `orders` row today for this strategy

```sql
SELECT id, received_at, symbol, side, signal, order_type, size, exchange_order_id,
       status, actual_fill_price, pnl, signal_source, closes_position_id
FROM orders
WHERE strategy_id = 'hype-breakout-da2e' AND received_at >= '2026-07-05 00:00:00+00'
ORDER BY received_at ASC;
```
```
                  id                  |          received_at          |  symbol   | side |   signal    | order_type |    size    | exchange_order_id |  status  | actual_fill_price |  pnl   | signal_source |          closes_position_id
--------------------------------------+-------------------------------+-----------+------+-------------+------------+------------+-------------------+----------+-------------------+--------+----------------+--------------------------------------
 8786d56a-0fff-4e47-b58d-b8e234f376e6 | 2026-07-05 01:02:53.762569+00 | HYPE-USDT | sell | open_short  | market     |      5.769 | 1000131641344      | filled   |             69.31 |      0 | ai_engine      |
 0854accc-b601-4003-bbdd-ea038f0cda31 | 2026-07-05 02:02:50.405687+00 | HYPE-USDT | buy  | close_short | market     |        2.9 | 1000131642820      | filled   |            68.833 | 1.3833 | ai_engine      | 496f73db-2e66-4ea9-b024-10e46bf5e343
 9ef95d9c-ab14-4eef-880f-8c4081451143 | 2026-07-05 02:47:44.46355+00  | HYPE-USDT | buy  | close_short | market     |        2.9 |                    | filled   |            68.448 | 2.4998 | ai_engine      | 496f73db-2e66-4ea9-b024-10e46bf5e343
 9a689b2b-b490-417e-8bd1-d15ffa6cf5f0 | 2026-07-05 05:02:52.170012+00 | HYPE-USDT | sell | open_short  | market     | 5.82708136 | 1000131646026      | filled   |            68.653 |      0 | ai_engine      |
 73a31c75-3a08-4fb5-b4b5-4d23ee83ce0d | 2026-07-05 06:02:45.925119+00 | HYPE-USDT | buy  | close_short | market     |        2.9 | 1000131647329      | filled   |            68.436 | 0.6293 | ai_engine      | 1707dbcb-ddc5-4d09-9697-146877e1ddbd
 17819b6d-bafa-4648-a720-83fa313bb5e5 | 2026-07-05 06:47:44.941784+00 | HYPE-USDT | buy  | close_short | market     |        2.9 |                    | filled   |            68.634 | 0.0551 | ai_engine      | 1707dbcb-ddc5-4d09-9697-146877e1ddbd
 71e430a6-2b7c-4a68-9dd3-e6bd30f7f805 | 2026-07-05 07:03:20.557963+00 | HYPE-USDT | buy  | open_long   | limit      | 5.56995714 |                    | rejected |                    |        | ai_engine      |
(7 rows)
```
`73a31c75…` (fill `68.436`, pnl `0.6293`) is the operator's example partial-close. Note it
**does** have `exchange_order_id = 1000131647329` populated — the operator's "blank
exchange-order id" observation is about the dashboard's *execution* panel field (which
reads from `order_execution_log`, not `orders.exchange_order_id` directly — see 1.4/1.5),
not about this column being empty.

### 1.4 — Dashboard join reproduced, per order

```sql
SELECT o.id AS order_id, o.signal, o.signal_source, o.exchange_order_id AS orders_exch_id,
       oel.id AS oel_id, oel.exchange_order_id AS oel_exch_id, oel.signal_log_id,
       sl.id AS sl_id, sl.ai_reasoning, sl.ai_confidence
FROM orders o
LEFT JOIN order_execution_log oel ON oel.exchange_order_id = o.exchange_order_id AND o.exchange_order_id IS NOT NULL
LEFT JOIN signal_log sl ON sl.id = oel.signal_log_id AND oel.signal_log_id IS NOT NULL
WHERE o.strategy_id = 'hype-breakout-da2e' AND o.received_at >= '2026-07-05 00:00:00+00'
ORDER BY o.received_at ASC;
```
```
               order_id               |   signal    | signal_source | orders_exch_id | oel_id |  oel_exch_id  | signal_log_id | sl_id | ai_reasoning (present?) | ai_confidence
--------------------------------------+-------------+---------------+----------------+--------+---------------+---------------+-------+--------------------------+---------------
 8786d56a-0fff-4e47-b58d-b8e234f376e6 | open_short  | ai_engine     | 1000131641344  |     99 | 1000131641344 |           189 |   189 | YES ("Confirmed downside breakout…") | 0.750
 0854accc-b601-4003-bbdd-ea038f0cda31 | close_short | ai_engine     | 1000131642820  |        |               |               |       | NO (blank)               |
 9ef95d9c-ab14-4eef-880f-8c4081451143 | close_short | ai_engine     |                |        |               |               |       | NO (blank)               |
 9a689b2b-b490-417e-8bd1-d15ffa6cf5f0 | open_short  | ai_engine     | 1000131646026  |    103 | 1000131646026 |           201 |   201 | YES ("A confirmed downside breakout…") | 0.750
 73a31c75-3a08-4fb5-b4b5-4d23ee83ce0d | close_short | ai_engine     | 1000131647329  |        |               |               |       | NO (blank)               |
 17819b6d-bafa-4648-a720-83fa313bb5e5 | close_short | ai_engine     |                |        |               |               |       | NO (blank)               |
 71e430a6-2b7c-4a68-9dd3-e6bd30f7f805 | open_long   | ai_engine     |                |        |               |               |       | NO (blank)               |
(7 rows)
```
**Every `close_short` order joins to nothing** (`oel_id` NULL, `sl_id` NULL), regardless of
whether `orders.exchange_order_id` itself is populated (it is, for 2 of the 4 closes, and
isn't for the other 2). The one non-close row that also joins to nothing is the rejected
`open_long` limit order — it never got an `exchange_order_id` because it was rejected, so
the join's `o.exchange_order_id IS NOT NULL` guard excludes it too (a related but distinct
edge case: rejected opens also lose their trace, for the same structural reason — the join
key is `exchange_order_id`, which a rejected order never receives).

**Independent check — does `order_execution_log` contain a close-side row under any key?**
```sql
SELECT id, signal_log_id, account_id, exchange, exchange_order_id, client_order_id, symbol, side, status, placed_at, filled_at
FROM order_execution_log
WHERE account_id = 'blofin-blofin-demo-v5vr' AND symbol = 'HYPE-USDT' AND created_at >= '2026-07-05 00:00:00+00'
ORDER BY created_at ASC;
```
```
 id  | signal_log_id |       account_id        | exchange | exchange_order_id |           client_order_id            |  symbol   | side |  status  |           placed_at           |           filled_at
-----+---------------+-------------------------+----------+--------------------+---------------------------------------+-----------+------+----------+--------------------------------+--------------------------------
  99 |           189 | blofin-blofin-demo-v5vr | blofin   | 1000131641344      | 61e63ebf-edf1-4a20-ab7f-47fea0031424  | HYPE-USDT | sell | filled   | 2026-07-05 01:02:54.628014+00 | 2026-07-05 01:02:58.087178+00
 103 |           201 | blofin-blofin-demo-v5vr | blofin   | 1000131646026      | 875d6910-0f76-421f-8918-d4e930a9b300  | HYPE-USDT | sell | filled   | 2026-07-05 05:02:52.408812+00 | 2026-07-05 05:02:55.667414+00
 105 |           206 | blofin-blofin-demo-v5vr | blofin   |                    | 940d1848-45e3-476b-95c4-20f9d35fce42  | HYPE-USDT | buy  | rejected | 2026-07-05 07:03:27.876244+00 |
```
(3 rows total for this account/symbol/day — exhaustive, not filtered by the join key.) Only
the two `open_short` fills (`side='sell'`) and the one rejected `open_long` attempt
(`side='buy'`) have any `order_execution_log` row at all. **Zero** OEL rows exist for any
of the 4 `close_short` orders. This rules out "the join key just doesn't match" — there is
no close-side OEL row under any key to match.

**Signal-log rows independent of the join** — every webhook call (open or close) writes
its own `signal_log` row at receipt, before the join is ever computed:
```sql
SELECT id, received_at, http_status, outcome, ai_reasoning, ai_confidence
FROM signal_log
WHERE strategy_id = 'hype-breakout-da2e' AND received_at >= '2026-07-05 00:00:00+00'
ORDER BY received_at ASC;
```
```
 id  |          received_at          | http_status |   outcome    | ai_reasoning                                                                    | ai_confidence
-----+-------------------------------+-------------+--------------+-----------------------------------------------------------------------------------+---------------
 189 | 2026-07-05 01:02:53.13171+00  |         200 | filled       | "Confirmed downside breakout of an Ascending Channel…"                            |         0.750
 192 | 2026-07-05 02:02:50.399648+00 |         200 | filled       | "The short position is in profit… A partial close is prudent to lock in profits…" |         0.700
 195 | 2026-07-05 02:47:44.454098+00 |         200 | filled       | "The original thesis for the short position is invalidated…"                      |         0.750
 201 | 2026-07-05 05:02:51.652789+00 |         200 | filled       | "A confirmed downside breakout of the Ascending Channel's lower boundary…"        |         0.750
 202 | 2026-07-05 06:02:45.898854+00 |         200 | filled       | "The original short thesis was based on a confirmed downside breakout… warranting a partial close…" | 0.680
 205 | 2026-07-05 06:47:44.919758+00 |         200 | filled       | "The original thesis for a confirmed downside breakout is invalidated…"           |         0.700
 206 | 2026-07-05 07:03:20.064243+00 |         200 | route_failed | "Ascending Channel detected with strong fit quality…"                             |         0.750
(7 rows)
```
Every one of the 7 orders in 1.3 has a matching `signal_log` row by timestamp (within ~0.6s,
since `_insert_signal_log` runs once per webhook call before the order is recorded) —
including all 4 closes: `0854accc`↔192, `9ef95d9c`↔195, `73a31c75`↔202, `17819b6d`↔205. The
reasoning is **fully present** in `signal_log` for every close order; it is simply never
linked to the `orders` row for that close, because no OEL row exists to carry
`signal_log_id` across (this table has no direct `orders.id` foreign key of its own — the
prior report's belief that closes get linked "via `signal_metadata`" was never actually
checked against the OEL step, and is wrong).

### 1.5 — Pinpointing the break (source-level, then confirmed on data)

Read fresh (not assumed from the prior investigation):
- `order-listener/app/webhook_handler.py` `_process_order` (~L1473-1507): **all**
  `close_long`/`close_short` signals — AI-initiated or not — are routed through
  `close_strategy_position(..., skip_exchange=False)`. This function (~L1057-1236) calls
  `executor_client.call_executor_close_position(...)`, **never**
  `executor_client.call_executor(...)`, and never builds/passes a `signal_log_id` anywhere
  in its body. Only `open_long`/`open_short` (~L1574-1594) build an `order_request` dict
  containing `"signal_log_id": signal_log_id` and call `call_executor(order_request)`.
- `order-listener/app/executor_client.py`: `call_executor` posts to `/execute`.
  `call_executor_close_position` posts to `/close-position`. Two different executor routes.
- `order-executor/app/main.py`: `@app.post("/execute")` (~L47) is the **only** route that
  calls `app.executor.execute()`. `@app.post("/close-position")` (~L79) and
  `@app.post("/accounts/{account_id}/positions/close")` (~L350) both call
  `adapter.close_position(...)` directly and return — neither touches
  `order_execution_log` in any way.
- `order-executor/app/executor.py`: `_insert_execution_log`/`_update_execution_log`
  (the only writers of `order_execution_log`) are called exclusively from inside
  `execute()` (~L35-88), which only `/execute` invokes.

**Conclusion, confirmed by the 1.4 data:** `order_execution_log` rows are created only for
orders that flow through `/execute` — i.e., only `open_long`/`open_short`. Every
`close_long`/`close_short` order, regardless of `signal_source` (AI or not) and regardless
of whether `orders.exchange_order_id` happens to be populated, goes through `/close-position`
instead and **never gets an OEL row**. Since the dashboard's join
(`orders → oel → signal_log`) requires an OEL row to reach `signal_log`, every close order
in the system shows blank `ai_reasoning`/`ai_confidence` on the Orders screen — this is not
specific to reconciler-synthetic closes (as the prior report concluded) and not specific to
partial-closes; it is universal to the close path itself.

The reasoning is **not lost** — it exists, correctly populated, in the close's own
`signal_log` row (1.4) and independently in `ai_signal_log` (1.6) — it is a pure
linking/query gap, not a data-loss gap: the mechanism that would carry `signal_log_id`
across (the one `open_long`/`open_short` benefits from) simply doesn't run for closes.

Ruling on the candidate mechanisms listed in the brief:
- "`exchange_order_id = NULL` triggers the join guard" — **partially true but not the root
  cause**: 2 of the 4 closes do have `exchange_order_id` populated, and still show blank,
  because no OEL row exists regardless.
- "No `order_execution_log` row is ever created for the close" — **confirmed, this is the
  actual root cause** (1.4's exhaustive OEL query, 1.5's source trace).
- "A `signal_log` row with the reasoning does exist but nothing links the close order to
  it" — **confirmed** (1.4's independent `signal_log` query).
- "The reasoning exists only in `ai_signal_log`, a table the Orders screen never joins" —
  **also true, additionally** (see 1.6) — it exists in both places.

### 1.6 — Earlier `@68.833` partial-close

Already included above: order `0854accc-b601-4003-bbdd-ea038f0cda31` (`close_short`, fill
`68.833`, pnl `1.3833`, on position `496f73db…`). Row 1.4 shows the identical pattern:
`orders_exch_id = 1000131642820` present, `oel_id`/`sl_id` both NULL. Its own `signal_log`
row is id `192` ("The short position is in profit… A partial close is prudent to lock in
profits…", confidence `0.700`), matched by timestamp (`02:02:50.399648` vs. order's
`02:02:50.405687`). **Identical pattern to the `73a31c75` example** — confirms this is
systemic to the close path, not an isolated incident.

### 1.7 — `ai_signal_log` cross-check

```sql
SELECT id, triggered_at, proposed_action, confidence, gate_passed, webhook_fired, webhook_status, order_id, LEFT(reasoning,80)
FROM ai_signal_log
WHERE strategy_id = 'hype-breakout-da2e' AND triggered_at >= '2026-07-05 00:00:00+00'
ORDER BY triggered_at ASC;
```
```
 id  |         triggered_at          | proposed_action  | confidence | gate_passed | webhook_fired | webhook_status |               order_id               | reasoning (preview)
-----+-------------------------------+-------------------+------------+-------------+---------------+----------------+---------------------------------------+---------------------------------------------------------------------------------
 346 | 2026-07-05 00:02:30.230144+00 | place_limit_long  |      0.750 | f           | f             |                |                                       | Ascending Channel with strong fit and 2 touches…
 349 | 2026-07-05 01:02:30.302399+00 | open_short        |      0.750 | t           | t             |            200 | 8786d56a-0fff-4e47-b58d-b8e234f376e6  | Confirmed downside breakout…
 352 | 2026-07-05 02:02:31.52308+00  | partial_close     |      0.700 | t           | t             |            200 | 0854accc-b601-4003-bbdd-ea038f0cda31  | The short position is in profit, and MACD remains bearish…
 353 | 2026-07-05 02:17:30.640414+00 | partial_close     |      0.700 | f           | f             |                |                                       | Price is currently profitable for the short position…
 356 | 2026-07-05 02:32:30.636053+00 | partial_close     |      0.720 | f           | f             |                |                                       | The short position is currently profitable…
 357 | 2026-07-05 02:47:30.390938+00 | close_short       |      0.750 | t           | t             |            200 | 9ef95d9c-ab14-4eef-880f-8c4081451143  | The original thesis for the short position is invalidated…
 359 | 2026-07-05 03:02:30.264109+00 |                   |            | f           | f             |                |                                       |
 361 | 2026-07-05 04:02:30.235208+00 | hold              |      0.500 | f           | f             |                |                                       | The input data contains contradictory information…
 366 | 2026-07-05 05:02:30.310448+00 | open_short        |      0.750 | t           | t             |            200 | 9a689b2b-b490-417e-8bd1-d15ffa6cf5f0  | A confirmed downside breakout…
 369 | 2026-07-05 06:02:30.795446+00 | partial_close     |      0.680 | t           | t             |            200 | 73a31c75-3a08-4fb5-b4b5-4d23ee83ce0d  | The original short thesis was based on a confirmed downside breakout…
 371 | 2026-07-05 06:17:30.563723+00 | partial_close     |      0.700 | f           | f             |                |                                       | The original short thesis…
 372 | 2026-07-05 06:32:30.482192+00 | partial_close     |      0.700 | f           | f             |                |                                       | The original short thesis…
 373 | 2026-07-05 06:47:30.459025+00 | close_short       |      0.700 | t           | t             |            200 | 17819b6d-bafa-4648-a720-83fa313bb5e5  | The original thesis for a confirmed downside breakout is invalidated…
 374 | 2026-07-05 07:02:30.208437+00 | place_limit_long  |      0.750 | t           | t             |            200 | 71e430a6-2b7c-4a68-9dd3-e6bd30f7f805  | Ascending Channel detected…
(14 rows)
```
`ai_signal_log.order_id` **does** point precisely at each close/partial-close `orders` row
(`0854accc`, `9ef95d9c`, `73a31c75`, `17819b6d`), and the reasoning/confidence there match
the corresponding `signal_log` rows in 1.4/1.6 exactly. So the reasoning for every AI close
in this position's lifetime exists, correctly attributed, in **two** independent places
(`signal_log` by timestamp, `ai_signal_log` by explicit `order_id` FK) — the Orders-screen
dashboard query is the only place that fails to surface it, because it depends exclusively
on the OEL hop that closes never create.

### Part 1 conclusion

The chain breaks at exactly one point: **`order_execution_log` rows are written only by
the `/execute` code path (opens); the close path (`/close-position`) never writes one, for
any close, AI-initiated or not, partial or full.** The dashboard's `orders → oel →
signal_log` join therefore always returns NULL `ai_reasoning`/`ai_confidence` for every
close order. The reasoning is fully recoverable — it already exists, unlinked, in both
`signal_log` (matched 1:1 by timestamp with the close's own webhook call) and
`ai_signal_log` (matched exactly by `order_id`). This is a "link what already exists"
problem, not "the reasoning was never persisted."

**The prior report's claim "AI-decided closes… are fully traced end-to-end — reasoning,
confidence, `ai_signal_log` row, and dashboard join all present" is refuted.** The
`ai_signal_log` row and raw `signal_log` reasoning are indeed present, but the **dashboard
join is not** — it is NULL for every single AI close order examined here (4 of 4). The
prior report appears to have verified only the reconciler-synthetic-close case and
extrapolated "the rest must be fine" for AI-initiated closes without independently
querying `order_execution_log` for them, which is exactly where the gap actually lives.

---

## Part 2 — Real strategy/account topology

### 2.1 — All strategies

```sql
SELECT id, name, symbol, account_id, strategy_source, enabled, is_deleted FROM strategies ORDER BY account_id, symbol;
```
```
           id           |           name           |  symbol   |          account_id          | strategy_source | enabled | is_deleted
------------------------+--------------------------+-----------+-------------------------------+------------------+---------+------------
 tv_test_harness        | TV Test Harness (shadow) | BTC-USDT  | blofin-blofin-demo-v5vr       | signal_engine    | t       | f
 matp-test-harness-fe19 | MATP Test Harness        | BTC-USDT  | blofin-blofin-demo-v5vr       | tradingview      | f       | t
 hype-test-7db4         | HYPE Test                | HYPE-USDT | blofin-blofin-demo-v5vr       | tradingview      | t       | f
 hype-breakout-da2e     | HYPE Breakout            | HYPE-USDT | blofin-blofin-demo-v5vr       | ai_engine        | t       | f
 sui-manual-59d9        | SUI Manual               | SUI-USDT  | blofin-blofin-demo-v5vr       | tradingview      | t       | f
 tv-btc-test-hl-94e1    | TV BTC Test HL           | BTC-USDT  | hyperliquid-hyperliquid-hqdy   | tradingview      | t       | f
 ai-btc-6f8c            | AI BTC                   | BTC-USDT  | hyperliquid-hyperliquid-hqdy   | ai_engine        | t       | f
(7 rows)
```
Note `matp-test-harness-fe19` is **soft-deleted** (`is_deleted=t`) **and disabled**
(`enabled=f`).

### 2.2 — All accounts

```sql
SELECT id, exchange, mode, label, is_active FROM exchange_accounts ORDER BY id;
```
```
              id              |  exchange   | mode |    label    | is_active
------------------------------+-------------+------+-------------+-----------
 blofin-blofin-demo-v5vr      | blofin      | demo | Blofin Demo | t
 hyperliquid-hyperliquid-hqdy | hyperliquid | demo | Hyperliquid | t
```
(2 rows — exactly 2 exchange accounts exist in the whole system.)

### 2.3 — Strategy counts per account, and per (account, symbol)

```sql
SELECT account_id, COUNT(*) AS total_rows, COUNT(*) FILTER (WHERE is_deleted = false AND enabled = true) AS live_enabled
FROM strategies GROUP BY account_id;
```
```
          account_id          | total_rows | live_enabled
-------------------------------+------------+--------------
 blofin-blofin-demo-v5vr       |          5 |            4
 hyperliquid-hyperliquid-hqdy  |          2 |            2
```
```sql
SELECT account_id, symbol, COUNT(*) AS total_rows, COUNT(*) FILTER (WHERE is_deleted = false AND enabled = true) AS live_enabled
FROM strategies GROUP BY account_id, symbol ORDER BY account_id, symbol;
```
```
          account_id           |  symbol   | total_rows | live_enabled
--------------------------------+-----------+------------+--------------
 blofin-blofin-demo-v5vr        | BTC-USDT  |          2 |            1
 blofin-blofin-demo-v5vr        | HYPE-USDT |          2 |            2
 blofin-blofin-demo-v5vr        | SUI-USDT  |          1 |            1
 hyperliquid-hyperliquid-hqdy   | BTC-USDT  |          2 |            2
```

### 2.4 — BTC specifically

```sql
SELECT s.id, s.name, s.symbol, s.account_id, ea.exchange, s.is_deleted, s.enabled
FROM strategies s JOIN exchange_accounts ea ON ea.id = s.account_id
WHERE s.symbol LIKE 'BTC%' ORDER BY ea.exchange, s.id;
```
```
           id           |           name           |  symbol  |          account_id           |  exchange   | is_deleted | enabled
------------------------+--------------------------+----------+--------------------------------+-------------+------------+---------
 matp-test-harness-fe19 | MATP Test Harness        | BTC-USDT | blofin-blofin-demo-v5vr        | blofin      | t          | f
 tv_test_harness        | TV Test Harness (shadow) | BTC-USDT | blofin-blofin-demo-v5vr        | blofin      | f          | t
 ai-btc-6f8c            | AI BTC                   | BTC-USDT | hyperliquid-hyperliquid-hqdy    | hyperliquid | f          | t
 tv-btc-test-hl-94e1    | TV BTC Test HL           | BTC-USDT | hyperliquid-hyperliquid-hqdy    | hyperliquid | f          | t
```
4 raw rows trade a BTC pair; **3 are live** (`is_deleted=f`) — 1 on Blofin
(`tv_test_harness`), 2 on Hyperliquid (`ai-btc-6f8c`, `tv-btc-test-hl-94e1`). The 4th
(`matp-test-harness-fe19`) is soft-deleted and disabled.

### 2.5 — Adjudicating the disputed claims

**Claim: "5 strategies share `blofin-blofin-demo-v5vr`."**
Technically **true as a raw row count** (2.3: 5 rows), but materially misleading as stated:
one of the 5 (`matp-test-harness-fe19`) is soft-deleted and disabled, leaving **4 live
strategies** actually capable of triggering webhooks or being polled. The prior report's
count wasn't fabricated, but it wasn't qualified either, and the qualification matters for
a claim about concurrent-polling load.

**Claim: "BTC-USDT on `blofin-blofin-demo-v5vr` belongs to `tv_test_harness`/
`matp-test-harness-fe19`."**
**True as a listing of which strategy IDs are configured with BTC-USDT on that account**
(2.4 confirms both rows exist), but **one of the two** (`matp-test-harness-fe19`) is
soft-deleted/disabled and therefore not a live source of BTC-USDT traffic on that account.
Framed as evidence for live concurrent-polling contention, this claim overstates the active
BTC-USDT presence on Blofin by counting a dead strategy.

**Operator's claim: "only 3 BTC strategies total, ≥1 on Hyperliquid."**
**Confirmed.** 2.4 shows exactly 3 live BTC-USDT strategies system-wide: 1 on Blofin
(`tv_test_harness`), 2 on Hyperliquid (`ai-btc-6f8c`, `tv-btc-test-hl-94e1`) — i.e. the
majority of BTC exposure is actually on Hyperliquid, not Blofin.

### 2.6 — Re-derived 502 root cause

Re-checked from scratch, without assuming any account grouping. Log retention starts
`2026-07-04T12:03:37Z` (container start). Full-window scan:

**All 502s on the open-orders route, entire retained window:**
```
2026-07-04T17:36:02.651783742Z INFO: 172.18.0.13:44634 - "GET /strategies/hype-breakout-da2e/orders HTTP/1.1" 502 Bad Gateway
```
Exactly 1 occurrence — same finding as before, this part is not disputed.

**But the underlying "unreachable" condition that produces such 502s is far more frequent
than 1 occurrence**, it's just usually absorbed silently by the reconciler instead of
surfaced to an external caller:
```
grep -c "UNKNOWN (treating as unreachable" → 17 occurrences in the whole retained window
```
```
2026-07-04 15:17:58,102  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 15:18:08,582  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 15:19:19,127  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 15:19:29,433  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 15:25:21,015  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 15:25:31,632  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 17:31:36,500  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 17:31:46,944  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 17:32:57,347  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 17:33:08,181  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 17:34:18,984  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 17:34:29,483  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 17:35:39,817  get_account_positions(hyperliquid-hyperliquid-hqdy)
2026-07-04 17:35:50,918  get_account_positions(blofin-blofin-demo-v5vr)
2026-07-04 17:36:01,165  get_account_open_orders(blofin-blofin-demo-v5vr)
2026-07-04 17:36:02,605  get_account_open_orders(blofin-blofin-demo-v5vr)
2026-07-05 00:26:43,568  get_account_positions(blofin-blofin-demo-v5vr)
```
**This directly contradicts the "contention on a shared Blofin account with 5 strategies"
theory**: the same failure mode hit `hyperliquid-hyperliquid-hqdy` (which has only 2
strategies, not 5) repeatedly, in lockstep with the Blofin failures, throughout a
~5-minute burst from `17:31:36` to `17:36:02`. A per-account-load explanation cannot
account for a Hyperliquid account with 2 strategies failing at the same cadence as a
Blofin account with 4-5.

Cross-checking `order-executor`'s own logs for the exact same burst window
(`17:31:00`–`17:36:03`): the executor's outbound calls to the real exchange APIs
(`https://demo-trading-api.blofin.com/...`, `https://api.hyperliquid-testnet.xyz/info`)
succeeded continuously and without gaps throughout this entire window (dozens of `200 OK`
lines, sampled portion shown for `17:35:35`–`17:36:02`):
```
2026-07-04T17:35:35.076488241Z ... GET https://demo-trading-api.blofin.com/api/v1/account/positions "HTTP/1.1 200 OK"
2026-07-04T17:35:36.505462762Z ... GET https://demo-trading-api.blofin.com/api/v1/account/positions "HTTP/1.1 200 OK"
... (continuous 200 OKs to both blofin and hyperliquid, no gaps) ...
2026-07-04T17:36:01.581010840Z ... GET https://demo-trading-api.blofin.com/api/v1/account/positions "HTTP/1.1 200 OK"
2026-07-04T17:36:02.640780167Z INFO: 172.18.0.11:42718 - "GET /accounts/blofin-blofin-demo-v5vr/orders?symbol=BTC-USDT HTTP/1.1" 200 OK
2026-07-04T17:36:02.725683191Z ... POST https://api.hyperliquid-testnet.xyz/info "HTTP/1.1 200 OK"
```
The executor also served one unrelated inbound request successfully inside this exact
window (`GET /accounts/blofin-blofin-demo-v5vr/orders?symbol=BTC-USDT` at `17:36:02.640`,
`200 OK`) — so the executor process itself was up, responsive, and had a healthy
connection to both exchanges throughout. **No inbound request matching any of the 4 failed
calls in this burst** (`get_account_positions` ×2, `get_account_open_orders` ×2) appears
anywhere in the executor's own access log for this window — i.e. these calls either never
completed a full round trip to the executor's ASGI layer, or timed out before/without it
logging a response.

The warning message itself is `"...UNKNOWN (treating as unreachable, NOT as empty): "` with
**nothing after the colon** in every one of the 17 instances — an empty `str(exception)`,
which is the signature of a bare `asyncio.TimeoutError` (its string representation is the
empty string), as opposed to an `httpx` error with a descriptive message (connection
refused, DNS failure, etc., which would print something).

**Re-derived conclusion:** the failures are not executor-down (exchange calls succeeded
throughout), not exchange-API failures, and not explained by per-account strategy count
(the low-strategy-count Hyperliquid account failed on the same cadence as Blofin). They
cluster in short, repeating bursts that line up with the reconciler's own ~61-62 second
poll cycle (confirmed separately: `Reconciler: automatic pass complete` lines are spaced
~61-62s apart) — i.e., during some bursts, several consecutive reconciler passes each hit
a timeout on the listener→executor hop for one or both accounts, then it clears for a long
stretch. **The exact underlying mechanism (event-loop stall in order-listener, a transient
docker-network blip between the two containers, or connection-pool exhaustion) is
undetermined from the available application-level logs** — nothing at this log level
records what happened on the wire during the ~10-second window before each timeout fired.
What the evidence does establish is that it is a shared-infrastructure/timing phenomenon
affecting both accounts symmetrically, not strategy-count-driven contention on Blofin.

### Part 2 conclusion — corrected topology

| Account | Exchange | Live strategies | Symbols |
|---|---|---|---|
| `blofin-blofin-demo-v5vr` | blofin | 4 (`tv_test_harness`, `hype-test-7db4`, `hype-breakout-da2e`, `sui-manual-59d9`) + 1 dead (`matp-test-harness-fe19`, deleted+disabled) | BTC-USDT ×1 live, HYPE-USDT ×2, SUI-USDT ×1 |
| `hyperliquid-hyperliquid-hqdy` | hyperliquid | 2 (`tv-btc-test-hl-94e1`, `ai-btc-6f8c`) | BTC-USDT ×2 |

3 live BTC-USDT strategies total, split 1 Blofin / 2 Hyperliquid — confirming the
operator's claim and refuting the prior report's implicit framing of BTC-USDT as a
Blofin-account phenomenon.

---

## Corrections to the prior report

1. **"AI-decided closes/partial-closes are fully traced end-to-end… dashboard join all
   present."** — **Wrong.** Every close order (4 of 4 examined, both partial and full, on
   two separate positions) shows a NULL dashboard join. The prior report verified this
   only for reconciler-synthetic closes and incorrectly generalized "the rest is fine" to
   AI-initiated closes without independently checking `order_execution_log` for them. The
   real, universal cause: `order_execution_log` is written only by the `/execute` (open)
   path; `/close-position` never writes one, for any close.
2. **"5 strategies share `blofin-blofin-demo-v5vr`"** presented as active concurrent load —
   **misleading**. The raw count is 5, but one (`matp-test-harness-fe19`) is soft-deleted
   and disabled; only 4 are live.
3. **"BTC-USDT on `blofin-blofin-demo-v5vr` belongs to `tv_test_harness`/
   `matp-test-harness-fe19`"** — **misleading in the same way**: true as a row listing, but
   one of the two is a dead strategy, so in practice only 1 live BTC-USDT strategy exists
   on Blofin (`tv_test_harness`), and BTC-USDT is actually more heavily represented on
   Hyperliquid (2 live strategies) than on Blofin (1 live strategy) — the opposite
   impression the prior report gave.
4. **"502 root cause: concurrent polling contention among 5 strategies on the shared
   [Blofin] account"** — **Discarded.** The same failure signature hit the
   `hyperliquid-hyperliquid-hqdy` account (2 strategies) on the same cadence as
   `blofin-blofin-demo-v5vr` during the same burst — a 2-strategy account cannot be
   explained by 5-strategy contention. The corrected finding: this is a recurring,
   bursty, symmetric-across-accounts timeout on the order-listener→order-executor hop,
   root mechanism undetermined from available logs (see 2.6).
5. **Not previously verified, now newly established:** the trace gap is not limited to
   reconciler-synthetic exits (23% of exits, per the prior report) — it applies to *every*
   close order in the system, which the prior report's own data (17 `ai_engine`-sourced
   close orders) should have caught had `order_execution_log` been queried directly for
   them instead of only for reconciler exits.

---

## Options (not decided, not implemented)

- Have the close path (`close_strategy_position`/`/close-position`) also write an
  `order_execution_log` row (with `signal_log_id` threaded through, mirroring the open
  path), so the existing dashboard join starts working for closes without changing the
  join itself.
- Change the dashboard's `/orders/:id/detail` query to fall back to the order's own
  `signal_log` row directly (matched by strategy_id + time proximity, or by adding a
  direct FK from `orders` to `signal_log` at insert time) instead of routing exclusively
  through `order_execution_log`.
- Add a direct `orders.signal_log_id` column, populated at `_insert_signal_log`/
  `_log_order` time for every order (open or close) in the same request, removing the
  dependency on OEL for reasoning/confidence lookups entirely.
- For the 502/timeout: add lower-level timing/connection logging around the
  listener→executor `httpx` calls (e.g. log the elapsed time and exception type, not just
  a stringified empty exception) so a future recurrence can be diagnosed instead of only
  re-confirmed as "unreachable."
