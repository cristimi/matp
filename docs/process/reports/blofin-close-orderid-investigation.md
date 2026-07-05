# Investigation: Blofin closes frequently land with no orderId → no fill price / pnl / fee

Date: 2026-07-05
Investigation only — no code, schema, or data changes. All exchange calls below are
read-only (GET history/fills/positions endpoints); no order was placed, closed, amended,
or cancelled.

## Two-line root-cause summary

- **Full close (`/api/v1/trade/close-position`):** the account_id used is confirmed
  `blofin-blofin-demo-v5vr`. Every historical close on this account is a full close (see
  Part 1) — no partial close has ever executed on it. Public API documentation (fetched
  read-only, not directly observed against our own traffic — see caveat in Part 2) describes
  this endpoint's response `data` as a dict with only `instId`/`positionSide`/`clientOrderId`,
  no `orderId`; the adapter's `order_info.get("orderId")` on that shape returns `None` by
  construction, not intermittently. The code also handles a list-shaped `data` (mirroring the
  order-placement endpoint, which reliably includes `orderId` — confirmed from real stored
  data), consistent with the observed ~50/50 split reflecting two different response shapes
  from the same endpoint rather than a timing issue.
- **History-lookup miss:** not observed in current data at all — every historical close with a
  present `exchange_order_id` also had `actual_fill_price`/`pnl` populated (8/8). The fill
  itself is never actually missing at the exchange: a real close with **zero** orderId in its
  own response was independently found in `orders-history` by instId+time+side+reduceOnly
  matching (see Part 3), meaning the order the exchange executed always has a proper history
  record — the current gap is specifically that the caller has no key to look it up by when
  the close-position response omits `orderId`.

## Part 1 — Quantify and split the failure

Confirmed the account id first:

```
docker compose exec postgres psql -U matp -d matp -c "SELECT id, exchange FROM exchange_accounts;"
 blofin-blofin-demo-v5vr      | blofin
 hyperliquid-hyperliquid-hqdy | hyperliquid
```

By signal + declared `order_type` (all rows are `market` — `order_type` does not
distinguish full vs partial in this schema):

```sql
SELECT signal, order_type,
       count(*) FILTER (WHERE exchange_order_id IS NULL) AS null_oid,
       count(*) FILTER (WHERE exchange_order_id IS NOT NULL) AS has_oid, count(*) AS total
FROM orders
WHERE account_id = 'blofin-blofin-demo-v5vr' AND signal IN ('close_long','close_short')
  AND received_at > NOW() - INTERVAL '7 days'
GROUP BY signal, order_type ORDER BY total DESC;
```
```
   signal    | order_type | null_oid | has_oid | total
 close_short | market     |        7 |       6 |    13
 close_long  | market     |        3 |       2 |     5
```

`order_type` doesn't carry full-vs-partial, so re-split by whether the close's linked
`strategy_positions` row ended up `closed` (full) or stayed `open` at a reduced size
(partial), via `closes_position_id`:

```sql
SELECT o.signal,
       CASE WHEN sp.status='closed' THEN 'full' WHEN sp.status='open' THEN 'partial' ELSE 'unknown' END AS close_kind,
       count(*) FILTER (WHERE o.exchange_order_id IS NULL) AS null_oid,
       count(*) FILTER (WHERE o.exchange_order_id IS NOT NULL) AS has_oid, count(*) AS total
FROM orders o LEFT JOIN strategy_positions sp ON sp.id = o.closes_position_id
WHERE o.account_id='blofin-blofin-demo-v5vr' AND o.signal IN ('close_long','close_short')
  AND o.received_at > NOW() - INTERVAL '7 days'
GROUP BY o.signal, close_kind ORDER BY total DESC;
```
```
   signal    | close_kind | null_oid | has_oid | total
 close_short | full       |        6 |       6 |    12
 close_long  | full       |        3 |       2 |     5
 close_short | unknown    |        1 |       0 |     1
```

The single "unknown" row is `2d7b3c03-...`, `status='no_position_to_close'` — a guard
rejection before any exchange call, unrelated to this investigation. Re-run over the
account's **entire** order history (2026-07-02 through 2026-07-05, 18 close rows total —
the account's full lifetime, not just 7 days) gives the identical split: **zero partial
closes**, ever, on this account. `full` closes are 100% of the executed-close population
(17/17, excluding the one rejection), split 9 null / 8 has-oid ≈ 53%/47% — consistent with
the 55% figure in the prior fee-fix report.

**Full vs partial verdict:** the failure is observed **exclusively on full closes** in this
account's data — there is no partial-close evidence to compare against. A real partial close
was exercised once historically, documented in
`docs/process/reports/trigger-resize-and-dup-sl-fix-report.md`, but it went through the
manual `POST /positions/{id}/close` route (`close_position_by_id` in `webhook_handler.py`),
which calls `close_strategy_position` **without** `closing_order_id` — so it never creates an
`orders` row at all, and no `raw_response` from that event exists anywhere. **Undetermined
from available data:** whether `_partial_close`'s reduce-only order path reliably returns an
`orderId` in practice, in *this* system. (Indirect evidence in Part 2 suggests it likely does,
since it reuses the same endpoint as regular opens — but no primary observation of an actual
partial-close-through-the-orders-table exists to confirm it.)

**`raw_response` — a second, orthogonal gap found while pulling stored responses (Part 1
step 2):**

```sql
SELECT count(*) FILTER (WHERE raw_response IS NOT NULL) AS has_raw, count(*) AS total
FROM orders WHERE account_id='blofin-blofin-demo-v5vr' AND signal IN ('close_long','close_short');
```
```
 has_raw | total
       0 |    18
```
System-wide (all accounts, all exchanges, all time):
```sql
SELECT signal, count(*) FILTER (WHERE raw_response IS NOT NULL) AS has_raw, count(*) AS total
FROM orders WHERE signal_source != 'reconciler' GROUP BY signal;
```
```
   signal    | has_raw | total
 open_short  |      35 |    40
 open_long   |      56 |    68
 close_long  |       0 |    44
 close_short |       0 |    50
```
`raw_response` is **never** populated for any close order, on either exchange, in this
system's entire history (0/94) — vs. reliably populated for opens (91/108). Root cause:
`webhook_handler.py`'s close handlers (`close_long`/`close_short` and the `target_position:
"flat"` handler in `_process_order`) build `order_result`/`flat_order_result` manually from
the `close_strategy_position` result dict, copying `success`/`status`/`exchange_order_id`/
`actual_fill_price`/`realized_pnl`(/`fee` as of the recent fix) — but never `raw_response`.
This means **no historical close's actual exchange response body can be inspected
retroactively, at all** — a materially important constraint on this whole investigation
(see Part 2) and a distinct gap from the `orderId`-omission issue, worth its own fix
(out of scope here — investigation only).

## Part 2 — Real close-response shapes

**Full close response body:** cannot be recovered for any historical close — see the
`raw_response` finding above. No log line captures it either: `blofin.py`'s `close_position`
only logs on the HTTP-status-!=200 failure branch (`logger.warning(...response.text)`); the
success path (`str(code) in ["0","200"]`) never logs the body. Container logs additionally
only retain history since the last redeploy (this session's Phase-3 redeploy reset them), so
even that failure-path log is unavailable for anything before ~12:56 today. **Undetermined
from available data**, from this system's own traffic.

**Supplementary, not primary evidence** (public API documentation, fetched read-only,
included because it is directly relevant but must be weighted lower than an actual observed
response — it was not cross-checked against Blofin's raw docs page directly due to fetch
truncation, only via a documentation-summarizing search): the close-position endpoint's
documented response is `{"code":"0","msg":"success","data":{"instId":"...",
"positionSide":"net","clientOrderId":""}}` — a **dict**, with no `orderId` field at all.
This lines up exactly with the code's defensive handling of two possible shapes:
```python
if isinstance(data_payload, list) and data_payload:
    order_info = data_payload[0]
elif isinstance(data_payload, dict):
    order_info = data_payload          # <- matches the documented dict shape, no orderId
else:
    order_info = {}
exchange_order_id = order_info.get("orderId")
```
If Blofin's live behavior matches this doc for the dict-shaped response, `exchange_order_id`
is `None` **by construction**, not flakily, whenever that shape comes back. This is a
plausible, well-supported explanation for the ~50% NULL rate — not a confirmed one, since no
raw response was ever captured to prove which shape actually occurred on any given close.

**Partial-close / order-placement endpoint** (`/api/v1/trade/order`, which `_partial_close`
reuses with `reduceOnly: true`): real stored `raw_response` from **opens** (same endpoint,
non-reduce-only) shows a reliable list-shaped, orderId-bearing response:
```
docker compose exec postgres psql ... -c "SELECT raw_response FROM orders WHERE signal IN ('open_long','open_short') ORDER BY received_at DESC LIMIT 2;"
{"msg": "", "code": "0", "data": [{"msg": "Order placed", "code": "0", "orderId": "1000131655662", "clientOrderId": ""}]}
{"msg": "", "code": "0", "data": [{"msg": "Order placed", "code": "0", "orderId": "1000131653258", "clientOrderId": ""}]}
```
Checked whether this endpoint ever omits `orderId` on this account: 6/29 opens have NULL
`exchange_order_id`, but every one of the 6 is a genuine rejection (guard-rejected before any
exchange call, or a real exchange error with `"orderId": null` in the error payload) — e.g.:
```
raw_response: {"msg": "All operations failed", "code": "1", "data": [{"msg": "stop loss
trigger price must be lower than the best ask price", "code": "102050", "orderId": null,
"clientOrderId": ""}]}
```
**On this endpoint, when the call succeeds (`code="0"`), `orderId` is always present** in
every sample available (29 opens, 23 successful). This is evidence about the *shared*
endpoint from *open* traffic only — no real partial-close (reduce-only) call through this
endpoint exists in the data to confirm the reduce-only variant behaves identically, so this
is corroborating, not conclusive, for `_partial_close` specifically.

**Item 5 — a close where orderId was present but fill data still blank:** searched for this
case and it does not exist in current data. Every historical close with a non-NULL
`exchange_order_id` (8/8, listed below) also has `actual_fill_price` and `pnl` populated:
```
exchange_order_id  | actual_fill_price | pnl
1000131653256      |             62760 | -0.3298
1000131651512      |           62463.6 |  0.619
1000131648985      |           63080.3 |  0.6406
1000131647329      |            68.436 |  0.6293
1000131646018      |           62742.9 | -0.0454
1000131642820      |            68.833 |  1.3833
1000131642818      |           62816.1 |  0.3476
1000131534278      |             65.98 | -0.6176829268292683
```
**Undetermined from available data:** no example of "orderId present, fill data still
blank" exists to reproduce. As a related, read-only sanity check, re-ran the *current*
(post-fix) `_get_order_details` against one of these real historical orderIds
(`1000131653256`, ~2.5h old at test time) to confirm the lookup mechanism itself still
resolves correctly for older-but-real IDs still within Blofin's rolling history window —
it does:
```
_get_order_details('BTC-USDT', '1000131653256') ->
{"orderId": "1000131653256", "state": "filled", "averagePrice": "62760",
 "pnl": "-0.3298", "fee": "0.075312", "reduceOnly": "true", "side": "buy", ...}
```
(Note this order carries `reduceOnly: "true"` — i.e. even a **full** close, executed via the
dedicated close-position endpoint, shows up in `orders-history` as an ordinary reduce-only
order. The exchange always creates a normal, queryable order record for the fill regardless
of which endpoint triggered it — directly relevant to Part 3.)

## Part 3 — Is the fill recoverable without the close-response orderId?

Took the known blank close from the fee-fix report (`b8b866c1-...`, `hype-test-7db4`,
HYPE-USDT, `close_short` size 5, `received_at 2026-07-05 13:19:36.758865+00`, exchange call
completed ~13:19:37 per executor logs) and, read-only, queried `orders-history` for
HYPE-USDT without using any orderId:

```
docker compose exec order-executor python3 -c "... adapter._headers/httpx GET orders-history?instId=HYPE-USDT ..."
orderId          side reduceOnly state  filledSize createTime      updateTime      fee         pnl
1000131655671    buy  true       filled 29         1783257577117   1783257577197   0.12045324  0.0348
1000131655662    sell false      filled 29         1783257528538   1783257528581   0.12047412  0
...
```
`1783257577117` = 2026-07-05 13:19:37.117 UTC — 359ms after the webhook's `received_at`, and
`side=buy, reduceOnly=true` matches a `close_short` exactly. This is an unambiguous match:
**the close is fully recoverable** — real fee `0.12045324`, pnl `0.0348` — via instId + tight
time-window + side + `reduceOnly=true` matching, with no orderId needed at all. (The very next
entry down, `1000131655662, sell, reduceOnly=false`, is independently confirmable as the
**open** leg of this same position — its fee `0.12047412` matches the value already recorded
on that open order in the DB, a useful cross-check that the matching logic is sound.)

**Does the reconciler's existing helper already do this?** Ran `get_closed_position_details`
(what `_recover_manual_close_pnl`/`_handle_full_external_close` already use) read-only for the
same window:
```
get_closed_position_details('HYPE-USDT', since_ms=1783257576000) ->
{"close_reason": "Closed on exchange", "closing_price": "69.226",
 "pnl_realized": "-0.44705472", "fee": "-0.24092736", "closed_at": "2026-07-05 13:19:37.206"}
```
This is **not** the same figure as the order-level fee (`0.12045324`) — it's a different
metric entirely. Raw `/api/v1/account/positions-history` for this instrument shows why:
```
{"historyId": "100786121", "positionId": "1000000277470", "instId": "HYPE-USDT",
 "openAveragePrice": "69.238", "closeAveragePrice": "69.226",
 "createTime": "1783257528581", "updateTime": "1783257577206",
 "realizedPnl": "-0.20612736", "fee": "-0.24092736"}
```
`fee` here (`-0.24092736`) is the **round-trip** fee for the whole open→close position
lifecycle, negative-signed (a debit), and its magnitude is exactly the sum of the two
per-order fees found above: `0.12045324 (close) + 0.12047412 (open) = 0.24092736`. So
`get_closed_position_details`/`positions-history` operates at **position-lifecycle**
granularity (open+close combined, inverted sign), not **per-order** granularity (single
leg, positive magnitude) — it cannot be substituted directly for populating a single order
row's `exchange_fee` without double-counting the open leg's fee (which is already recorded
separately on the open order) and flipping its sign.

**Ambiguity risk — confirmed real, not hypothetical:** `positions-history` is keyed by
account+instrument only (`positionId=1000000277470` recurs across many historical entries,
representing Blofin's single net position slot per instrument, per `positionSide: "net"`).
**Two strategies trade HYPE-USDT on this same account** (`hype-test-7db4` and
`hype-breakout-da2e`) — confirmed via `strategies` table. If both were ever live/overlapping
in time, `positions-history`'s account+instrument view has no way to attribute a given
position-cycle to one strategy vs the other; it would need to be split by matching to each
strategy's own order sizes/timestamps, same as the order-level approach. The order-level
(`orders-history`/`fills-history`) approach mitigates this by also matching on `size` (this
account's two HYPE strategies use different position sizes in practice), but is not
bulletproof if two strategies' sizes coincide within the same tight window — worth noting as
a residual risk of any instId+time-based recovery, not just the naive position-level one.

**Verdict:** a robust order-level recovery **does** exist and was demonstrated live,
read-only, against a real blank close. It does **not** currently exist as reusable code in
the reconciler's position-level helper — that helper answers a different question (aggregate
position PnL over an unknown gap) at coarser granularity and inverted fee sign, and is
already exposed to the same multi-strategy-per-instrument ambiguity this investigation
surfaced.

**Fix options (descriptions only — nothing implemented):**
1. In `blofin.py`'s `close_position`/`_partial_close`, when `order_info.get("orderId")` comes
   back empty, fall back to querying `orders-history`/`fills-history` for the instrument and
   selecting the entry matching `reduceOnly=true`, `side` = the close's own side, `filledSize`
   ≈ the requested close size, within a short time window after the close call — the same
   matching primitive demonstrated above, just made reusable and threshold-tuned (window
   width, size tolerance) rather than ad hoc.
2. Separately (independent of option 1): fix the `raw_response` gap found in Part 1 so future
   closes at least retain the actual response body for later inspection/backfill, regardless
   of whether `orderId` parsing succeeds.
3. Do not repurpose `get_closed_position_details`/`positions-history` for this — it is the
   wrong granularity (round-trip, not per-leg) and shares the same account+instrument
   ambiguity across strategies; any fix should stay at the order/fill level.
