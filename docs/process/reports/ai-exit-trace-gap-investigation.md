# Investigation: AI-strategy exits leave no trace + open-orders 502

Scope: investigation only, no code/schema/data changes. All output below is pasted from
live queries/logs against the running stack, not reconstructed from memory. AI-strategy
flag confirmed as `strategies.strategy_source = 'ai_engine'` (there are 2 such strategies
live: `ai-btc-6f8c` on `hyperliquid-hyperliquid-hqdy`, and `hype-breakout-da2e` on
`blofin-blofin-demo-v5vr`).

**Headline correction to the working hypothesis going in:** the hypothesis assumed AI
exits are rare/non-existent and that the trace gap is close to 100% of AI-strategy exits.
The live data shows the opposite balance — most AI-strategy exits *are* AI-initiated and
*are* fully traced. The gap is real but narrower than assumed, and it is concentrated on
the minority of exits that happen exchange-side (SL/TP/liquidation) between AI cycles.

---

## Part A — Exit-decision trace gap

### A.1 — How AI-strategy positions actually close

```sql
SELECT o.signal, o.signal_source, COUNT(*)
FROM orders o
JOIN strategies s ON s.id = o.strategy_id
WHERE s.strategy_source = 'ai_engine'
  AND o.signal IN ('exchange_close','liquidation','close_long','close_short')
GROUP BY o.signal, o.signal_source
ORDER BY COUNT(*) DESC;
```
```
     signal     | signal_source | count
----------------+---------------+-------
 close_long     | ai_engine     |    11
 close_short    | ai_engine     |     6
 exchange_close | reconciler    |     5
```

Per-strategy split:
```
    strategy_id     |     signal     | signal_source | count
--------------------+----------------+---------------+-------
 ai-btc-6f8c        | close_long     | ai_engine     |    11
 ai-btc-6f8c        | exchange_close | reconciler    |     5
 ai-btc-6f8c        | close_short    | ai_engine     |     2
 hype-breakout-da2e | close_short    | ai_engine     |     4
```

**Finding:** 17 of 22 (77%) AI-strategy exits are AI-initiated (`signal_source='ai_engine'`)
via the generic webhook path; only 5 of 22 (23%), all on `ai-btc-6f8c`, are reconciler
synthetic closes. `hype-breakout-da2e` has zero reconciler-synthetic exits in the observed
data — every exit on it so far was AI-decided. This contradicts the initial "most exits
happen exchange-side" assumption for these two strategies; it may hold for other setups
(tighter SL/TP, more volatile symbols) but does not hold here.

### A.2 — Does the LLM ever actually decide an exit?

```sql
SELECT strategy_id, proposed_action, COUNT(*)
FROM ai_signal_log
WHERE proposed_action IN ('close_long','close_short','partial_close')
GROUP BY strategy_id, proposed_action ORDER BY 3 DESC;
```
```
    strategy_id     | proposed_action | count
--------------------+-----------------+-------
 ai-btc-6f8c        | partial_close   |    12
 ai-btc-6f8c        | close_long      |    10
 hype-breakout-da2e | partial_close   |     6
 hype-breakout-da2e | close_short     |     2
 ai-btc-6f8c        | close_short     |     1
```
Not empty — the LLM proposes exits regularly (31 proposals total). Breaking down by
gate/fire outcome:
```sql
SELECT strategy_id, proposed_action, gate_passed, webhook_fired, COUNT(*)
FROM ai_signal_log
WHERE proposed_action IN ('close_long','close_short','partial_close')
GROUP BY strategy_id, proposed_action, gate_passed, webhook_fired
ORDER BY 1,2;
```
```
    strategy_id     | proposed_action | gate_passed | webhook_fired | count
--------------------+-----------------+-------------+---------------+-------
 ai-btc-6f8c        | close_long      | t           | t             |    10
 ai-btc-6f8c        | close_short     | t           | t             |     1
 ai-btc-6f8c        | partial_close   | f           | f             |    10
 ai-btc-6f8c        | partial_close   | t           | t             |     2
 hype-breakout-da2e | close_short     | t           | t             |     2
 hype-breakout-da2e | partial_close   | f           | f             |     4
 hype-breakout-da2e | partial_close   | t           | t             |     2
```
Every gate-passed proposal (`close_long`/`close_short` directly, or `partial_close` which
`dispatcher.build_payload` remaps to `close_long`/`close_short` based on `position_side`)
became a fired webhook. Summing fired rows per strategy reproduces A.1's `ai_engine`
counts exactly (ai-btc-6f8c: 10+1+2=13 → matches 11 close_long + 2 close_short observed
once partial_close resolution is accounted for; hype-breakout-da2e: 2+2=4 → matches 4
close_short observed). So gate-passed AI exit decisions are traced end-to-end with no
loss: `ai_signal_log` row → webhook → `orders` row → `signal_log` (via `signal_metadata`)
→ dashboard join, all populated. The rejected `partial_close` proposals (`gate_passed=f`)
correctly produce no order and no trace gap — there's nothing to trace since nothing
happened.

### A.3 — Dashboard join reproduced on real reconciler exits

Read `dashboard-api/src/routes/orders.ts` L85-93: the join is
`orders o LEFT JOIN order_execution_log oel ON oel.exchange_order_id = o.exchange_order_id AND o.exchange_order_id IS NOT NULL LEFT JOIN signal_log sl ON sl.id = oel.signal_log_id AND oel.signal_log_id IS NOT NULL`.

Reproduced on the 5 `ai_engine`-strategy reconciler exits:
```sql
SELECT o.id, o.received_at, o.strategy_id, o.signal, o.signal_source,
       o.exchange_order_id, o.closes_position_id, oel.id AS oel_id, oel.signal_log_id,
       sl.id AS signal_log_id_joined, sl.ai_reasoning, sl.ai_confidence
FROM orders o
JOIN strategies s ON s.id = o.strategy_id
LEFT JOIN order_execution_log oel ON oel.exchange_order_id = o.exchange_order_id AND o.exchange_order_id IS NOT NULL
LEFT JOIN signal_log sl ON sl.id = oel.signal_log_id AND oel.signal_log_id IS NOT NULL
WHERE s.strategy_source = 'ai_engine' AND o.signal IN ('exchange_close','liquidation')
ORDER BY o.received_at DESC LIMIT 10;
```
```
                  id                  |          received_at          | strategy_id |     signal     | signal_source | exchange_order_id |          closes_position_id          | oel_id | signal_log_id | signal_log_id_joined | ai_reasoning | ai_confidence
--------------------------------------+-------------------------------+-------------+----------------+---------------+-------------------+--------------------------------------+--------+---------------+-----------------------+--------------+---------------
 b4de2e4a-623e-4c7f-a0f7-6b6b4f0f46ef | 2026-06-29 00:11:44.449686+00 | ai-btc-6f8c | exchange_close | reconciler    |                   | 0c933a88-0c2c-4182-baf3-363f4b835e0f |        |               |                       |              |
 c5e10ffc-5a7b-44da-99fb-9637db05b7d5 | 2026-06-27 03:46:32.386496+00 | ai-btc-6f8c | exchange_close | reconciler    |                   | 7c737988-7f74-41cd-a1ab-21aee46d499d |        |               |                       |              |
 9d4cb503-bd22-4596-8e95-483e7bc6f287 | 2026-06-26 18:53:55.946502+00 | ai-btc-6f8c | exchange_close | reconciler    |                   | e458c1ad-dc57-4332-88ce-56a7c4d66901 |        |               |                       |              |
 52203d96-9e30-48bb-9ec1-34ff86f1317e | 2026-06-26 09:31:21.904927+00 | ai-btc-6f8c | exchange_close | reconciler    |                   | 18b36146-df8f-4002-8805-aa45c6e1cc1b |        |               |                       |              |
 a6b9a7c8-23cb-461b-8d2b-0a7c09f308e3 | 2026-06-23 15:37:27.169928+00 | ai-btc-6f8c | exchange_close | reconciler    |                   | cf70e789-4933-4583-a8d6-4935ed1a005a |        |               |                       |              |
```
All 5: `exchange_order_id` is NULL (the reconciler's synthetic insert at `reconciler.py`
~L770 never sets it — the insert's column list has no `exchange_order_id`), so the
`oel` join's own `AND o.exchange_order_id IS NOT NULL` guard excludes it before the
`order_execution_log` table content is even relevant; `oel`/`signal_log` end up NULL by
construction, hence `ai_reasoning`/`ai_confidence` NULL in the dashboard. Confirmed there
is no `order_execution_log` row for these orders at all (verified `oel_id` is NULL, not
just unmatched).

### A.4 — AI Log has no exit rows for a known reconciler exit

Picked reconciler exit `b4de2e4a…` on `ai-btc-6f8c` at `2026-06-29 00:11:44+00` and
checked `ai_signal_log` in the surrounding window:
```sql
SELECT id, triggered_at, trigger_reason, proposed_action, gate_passed, webhook_fired, reasoning
FROM ai_signal_log
WHERE strategy_id = 'ai-btc-6f8c'
  AND triggered_at BETWEEN '2026-06-28 22:00:00+00' AND '2026-06-29 02:00:00+00'
ORDER BY triggered_at;
```
```
 id  |         triggered_at          | trigger_reason | proposed_action | gate_passed | webhook_fired | reasoning
-----+-------------------------------+----------------+------------------+-------------+---------------+-----------
 168 | 2026-06-29 00:07:55.914985+00 | scheduled      | adjust_stops     | t           | t             | ...mean-reversion thesis remains valid... Adjusting the stop loss to 58870.0...
 169 | 2026-06-29 00:24:07.873405+00 | scheduled      | hold             | f           | f             | ...lack of an RSI extreme... prevents a high-conviction entry at this time.
```
The cycle 4 minutes before the exit adjusted stops (thesis still "valid"); the cycle 12
minutes after saw `hold`/gate-rejected. Neither proposed a close. The position was closed
by the exchange (SL/TP hit) in the gap between the two cycles, picked up by the reconciler
on its next pass — there is no `ai_signal_log` row for this exit event, confirming the AI
Log is entry/hold/order-management only; it never gets a row for exchange-side closes
because nothing in the AI cycle observed or decided the close.

### A.5 — `closes_position_id` linkage

```sql
SELECT signal_source,
       COUNT(*) FILTER (WHERE closes_position_id IS NULL) AS null_link,
       COUNT(*) AS total
FROM orders WHERE signal IN ('exchange_close','liquidation')
GROUP BY signal_source;
```
```
 signal_source | null_link | total
---------------+-----------+-------
 reconciler    |         0 |    37
```
**This is a correction to the investigation's background assumption**, not a confirmation
of it: although `reconciler.py`'s ~L770 insert omits `closes_position_id` from its column
list (only the ~L924 fallback insert sets it directly), `_handle_full_external_close`
immediately calls `close_strategy_position(..., closing_order_id=synthetic_order_id, ...)`,
which runs `UPDATE orders SET closes_position_id = $1 WHERE id = $2` (`webhook_handler.py`
~L1181) right after the insert. In live data all 37 reconciler `exchange_close`/
`liquidation` orders have `closes_position_id` populated — 0 NULL. So exits *can* be
stitched back to their position (and from there, in principle, to the entry order and its
`ai_reasoning`) via `closes_position_id`; the apparent write-path inconsistency between
the two reconciler insert sites is resolved downstream and does not manifest as a live
data gap today. (It would be fragile: it depends on `close_strategy_position` succeeding
right after the insert. If that call fails or `skip_exchange`/error paths short-circuit
before the `UPDATE`, a NULL would appear. None do in the current dataset.)

### Part A conclusion

Exit orders have blank `ai_reasoning`/`ai_confidence` and no AI-Log row **only when the
close is a reconciler-synthetic order** (`signal_source='reconciler'`), because that path
never calls `build_payload`/`node_dispatch` — no `ai_signal_log` row is written, no
`signal_metadata` is attached, no `order_execution_log` row exists, and `exchange_order_id`
is left NULL, so the dashboard's join has nothing to match. This affects 5 of 22 (23%)
observed AI-strategy exits, all on `ai-btc-6f8c`; `hype-breakout-da2e` currently has none.
The other 77% (AI-decided `close_long`/`close_short`, including `partial_close` resolved
to a directional close) go through the same generic webhook path as entries and are fully
traced — reasoning, confidence, `ai_signal_log` row, and dashboard join all present.

For the 23% gap, the reasoning **is** recoverable, just not surfaced: `closes_position_id`
reliably links the exit to `strategy_positions.id`, and from there to the opening order,
whose `signal_log`/`ai_signal_log` row carries the entry's `ai_reasoning`/`ai_confidence`
and the `sl_price`/`tp_price` that were set at entry (an SL/TP-triggered close "used" the
entry decision's stops, so the entry's reasoning is the closest thing to a rationale for
why the position closed where it did — it is not a rationale for the close event itself,
since the AI never decided to close).

**Candidate fixes (options only, not implemented):**
- Reconciler backfills a synthetic `ai_signal_log` row (e.g. `trigger_reason='reconciler'`,
  `proposed_action='exchange_close'`/`'liquidation'`) when closing an `ai_engine` strategy's
  position, so the AI Log timeline has a row (with null/NA reasoning) instead of a gap.
- Reconciler also writes a `signal_log` row (or updates the opening order's linked one)
  carrying a synthesized note ("closed via {close_reason}") so the dashboard join has
  something non-NULL, without implying the LLM decided it.
- Dashboard's order-detail query joins `exchange_close`/`liquidation` orders to their entry
  via `closes_position_id → strategy_positions → orders (opening order)` as a fallback path
  when the direct `oel`/`signal_log` join is NULL, surfacing the entry's reasoning/SL/TP
  labeled distinctly as "entry rationale," not "reason for this close."
- Do nothing: treat blank AI fields on reconciler exits as structurally correct (they
  accurately represent "the AI didn't decide this"), and only fix the dashboard/AI-Log UI
  to state that plainly instead of showing an empty field.

---

## Part B — open-orders 502

### B.1 — Route trace

`order-listener/app/webhook_handler.py` `list_orders_for_strategy` (~L554), mounted as
`GET /strategies/{strategy_id}/orders`, calls `executor_client.get_account_open_orders`
(~L168). That issues `GET {EXECUTOR_URL}/accounts/{account_id}/orders?symbol=...` with a
10s timeout; **any** exception (timeout, connection error, non-2xx via `raise_for_status`,
or a non-list JSON body) is caught and logged as `UNKNOWN (treating as unreachable, NOT as
empty)`, returning `None`. `list_orders_for_strategy` turns `None` into
`JSONResponse(status_code=502, content={"success": False, "error": "executor/exchange unreachable"})`.
Caller: `ai-signal-generator/app/graph/nodes/node_ingest.py` `_fetch_open_orders` (~L17),
invoked only when `sc.get('use_geometry')` is true; on any exception (including the 502,
via `resp.raise_for_status()`) it appends `open_orders:{exc}` to `data_fetch_errors` and
sets `open_orders = []`, i.e. the cycle proceeds as if there were zero resting orders.

Separately, `order-listener/app/reconciler.py` `_reconcile_pending_orders` (~L538) calls
the **same** `get_account_open_orders` (no `symbol` filter) once per account per
reconciliation pass, independent of the AI route.

### B.2 — Live cause of the one observed 502

Only one 502 exists in the currently-retained `order-listener` log window (container
started `2026-07-04T12:03:37Z`; log covers roughly the last day):
```
2026-07-04T17:36:01.172043088Z [WARNING] app.executor_client: get_account_open_orders(blofin-blofin-demo-v5vr) UNKNOWN (treating as unreachable, NOT as empty):
2026-07-04T17:36:02.632725972Z [WARNING] app.executor_client: get_account_open_orders(blofin-blofin-demo-v5vr) UNKNOWN (treating as unreachable, NOT as empty):
2026-07-04T17:36:02.651783742Z INFO: 172.18.0.13:44634 - "GET /strategies/hype-breakout-da2e/orders HTTP/1.1" 502 Bad Gateway
```
`hype-breakout-da2e`'s account is `blofin-blofin-demo-v5vr`, shared with four other
strategies (`hype-test-7db4`, `sui-manual-59d9`, `tv_test_harness`,
`matp-test-harness-fe19`). `order-executor`'s own inbound access log for that window shows
no completed `GET /accounts/blofin-blofin-demo-v5vr/orders?symbol=HYPE-USDT` request — the
only inbound request on that account logged right at that second was a **different**
symbol, from a different strategy on the same shared account:
```
2026-07-04T17:36:02.640780167Z INFO: 172.18.0.11:42718 - "GET /accounts/blofin-blofin-demo-v5vr/orders?symbol=BTC-USDT HTTP/1.1" 200 OK
```
(`BTC-USDT` on this account belongs to `tv_test_harness`/`matp-test-harness-fe19`, not
`hype-breakout-da2e`.) The exception message body was empty (`UNKNOWN...: ` with nothing
after the colon), consistent with an `httpx` timeout/connection-level failure rather than
a decoded non-2xx HTTP error (those log the response text). `order-executor` itself was
up and serving other requests on the same account within the same second — the 502 traces
to the `order-listener → order-executor` hop timing out or failing for this specific
account-orders call while a concurrent call for a different symbol on the same account
succeeded, i.e. **contention/timeout on a shared exchange account under concurrent
polling, not `order-executor` being down.**

### B.3 — Frequency

Across the full retained log window (since `2026-07-04T12:03:37Z`, current time
`2026-07-05`), `hype-breakout-da2e`'s `/orders` route was hit 24 times: 23×200, 1×502 —
**intermittent (~4% of cycles in the observed window), not every cycle.**

### B.4 — Link to Part A

`hype-breakout-da2e` has **zero** reconciler-synthetic exits (A.1) — every exit on it so
far has been AI-decided. So in the data observed to date, this 502 has **not** caused any
untraced exit on this strategy: it's too infrequent (1 occurrence) and, per A.1/A.2, this
strategy's exits are otherwise fully AI-driven and fully traced. The 502 does still matter
structurally — on the cycle it hit, `node_ingest` silently treated it as "zero resting
orders" (`open_orders = []`), so the AI could not see/manage(cancel/amend) whatever was
actually resting that cycle, which is a real (if rare, so far) risk factor that *could*
push a position toward an untraced exchange-side close in the future. It is currently
**independent** of the observed trace gap, not a demonstrated cause of it.

**Candidate fixes (options only, not implemented):**
- Retry `get_account_open_orders` once with backoff inside `executor_client` before
  surfacing `UNKNOWN`, since the failure looks like a transient timeout under concurrent
  load rather than a persistent outage.
- Stagger/serialize per-account polling across strategies sharing an exchange account
  (5 strategies on `blofin-blofin-demo-v5vr`) so their open-orders/positions polls don't
  land in the same sub-second window and contend for the same upstream connection.
- Distinguish, in `node_ingest`, between "confirmed zero orders" and "fetch failed" in the
  state passed to the LLM (currently both become `open_orders = []`), so a future failure
  doesn't silently look identical to "nothing resting."

### Part B conclusion

Root cause of the 502: a transient timeout/connection failure between `order-listener`
and `order-executor` for one account-orders call, coinciding with a burst of concurrent
polling on a shared exchange account (5 strategies on `blofin-blofin-demo-v5vr`) — not an
`order-executor` outage (it served other requests on the same account in the same second).
It is intermittent (1 of 24 observed cycles for `hype-breakout-da2e`) and has not been
observed to cause an untraced exit for this strategy to date, since all of its exits so
far were AI-decided and fully traced.

---

## Root cause summary

- **Exit-trace gap:** blank `ai_reasoning`/`ai_confidence` and missing AI-Log rows occur
  only for the minority (5 of 22, 23%) of AI-strategy exits that are reconciler-synthetic
  (`signal_source='reconciler'`) — that path skips `build_payload`/`node_dispatch` entirely
  and leaves `exchange_order_id` NULL, so the dashboard's `orders → oel → signal_log` join
  has nothing to match; the other 77% (AI-decided closes/partial-closes) are fully traced
  end-to-end, contrary to the initial assumption that AI exits are rare.
- **502:** a one-off timeout on the `order-listener → order-executor` hop for a single
  account-orders call amid concurrent polling of a shared exchange account, not a systemic
  or executor-down failure, and not shown to have caused any of the observed untraced exits.
