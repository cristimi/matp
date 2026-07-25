# Stranded resting orders: LLM-skip carve-out + management-action confidence exemption

Started from a question about the ETH strategy's two resting orders and how close price
came to them. That turned up orders that had been un-manageable for 8 hours, and two
independent gate defects behind it.

## 1. The observation that started it

Both ETH orders are genuinely resting on the exchange (queried through the executor, not
just the DB):

```
$ docker compose exec -T nginx wget -qO- --timeout=20 \
    "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/orders?symbol=ETH-USDT"
[{"order_id":"56947163834","symbol":"ETH-USDT","side":"buy","price":1821.9,"size":0.1098,
  "filled_size":0.0,"status":"resting","created_at_ms":1784930464811},
 {"order_id":"56936760362","symbol":"ETH-USDT","side":"sell","price":1898.6,"size":0.1051,
  "filled_size":0.0,"status":"resting","created_at_ms":1784908867971}]
```

Closest approach since placement, on 5m Hyperliquid candles:

```
current_price: 1874.6
candles: 914  from 2026-07-22 14:55:00+00:00 to 2026-07-25 19:00:00+00:00 (UTC)
SELL 1898.564 (placed 07-24 12:01) | since placed  | bars=372 | closest high=1885.30 at 07-24 12:25 UTC | gap=13.26 (0.70%)
SELL 1898.564 (placed 07-24 12:01) | since amended | bars=324 | closest high=1875.80 at 07-25 18:00 UTC | gap=22.76 (1.20%)

BUY  1821.940 (placed 07-24 13:01) | since placed  | bars=360 | closest low=1847.00 at 07-24 13:55 UTC | gap=25.06 (1.38%)
BUY  1821.940 (placed 07-24 13:01) | since amended | bars=252 | closest low=1850.40 at 07-25 08:50 UTC | gap=28.46 (1.56%)
```

Neither ever came close. What matters more is *why they were still sitting there*:

```
 2026-07-25 01:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 02:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 03:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 04:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 05:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 06:00 | cancel_order | 0.780 | f | target_order_id_missing
 2026-07-25 07:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 08:00 |              |       | f | llm_failed
 2026-07-25 09:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 10:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 11:00 | amend_order  | 0.680 | f | confidence_below_threshold
 2026-07-25 12:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 13:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 14:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 15:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 16:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 17:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 18:00 | hold         |       | f | no_range_llm_skipped
 2026-07-25 19:00 | hold         |       | f | no_range_llm_skipped
```

## 2. Defect A — the skip predicate ignores resting orders

`should_skip_llm_no_range` was written **2026-07-06** (`de669f7`, "skip LLM for
geometric_range when no tradeable range exists"). The resting-limit workflow landed
**2026-07-08** (`fa3d6d5`, "Regime Router template + resting-limit expansion"). The
predicate therefore had exactly one carve-out — `position_open` — because when it was
written an open *position* was the only thing that could still need attention on a weak
fit.

The skip is terminal (`graph.py`: `skip_geometry → dispatch → END`), so Phases 3/4/5 of
the template cannot run. And nothing else in the stack can clean up: the order-listener's
reconciler only syncs status; there is no TTL or stale-limit sweep anywhere. **The LLM is
the only thing that can cancel or amend a resting order.**

Scope, over all history:

```
$ ... select l.strategy_id, count(*) from ai_signal_log l
      where l.gate_rejection_reason='no_range_llm_skipped'
        and exists (select 1 from orders o where o.strategy_id=l.strategy_id
                    and o.order_type='limit' and o.received_at < l.triggered_at
                    and (o.status='pending' or (o.status in ('cancelled','filled')
                                                and o.updated_at > l.triggered_at)))
      group by 1 order by 2 desc;

    strategy_id     | skipped_cycles_with_resting_order |             first             |             last
--------------------+-----------------------------------+-------------------------------+-------------------------------
 eth-ai-34d2        |                                89 | 2026-07-10 10:00:12.894341+00 | 2026-07-25 19:00:24.009909+00
 ai-btc-6f8c        |                                29 | 2026-07-07 09:02:30.016168+00 | 2026-07-08 06:30:00.105532+00
 hype-breakout-da2e |                                29 | 2026-07-06 15:32:03.09415+00  | 2026-07-07 09:06:18.170006+00

 gate_rejection_reason | count
-----------------------+-------
 no_range_llm_skipped  |   331
```

147 of 331 skipped cycles — 44% — ran while an order was resting unmanaged.

**Fix** (`app/graph/gating.py`), mirroring the existing position carve-out:

```python
    if state.get('position_open'):
        return False
    if state.get('open_orders'):
        return False
```

Safe by construction: `open_orders` is always populated for `geometric_range`
(`need_open_orders = use_geometry or use_limit_orders`, node_ingest.py:119) and is in state
before the routing branch; empty/`None` falls through, so the token-saving case — weak fit,
nothing outstanding — is unchanged.

## 3. Defect B — management actions gated by the *entry* confidence threshold

`node_guard.py` step 3 applied `confidence_threshold` to every non-hold action, including
`cancel_order` and `amend_order`. Neither can open new exposure: cancel is pure
de-risking, amend re-prices an order that already passed this same gate when it was placed.
This is the failure direction the file already reasons about for `partial_close`, which was
deliberately excluded from the entry-cooldown group ("refusing to de-risk because an entry
happened recently is the wrong failure direction").

Fleet-wide impact:

```
 proposed_action |   gate_rejection_reason    | count |   first    |    last
-----------------+----------------------------+-------+------------+------------
 amend_order     |                            |    64 | 2026-07-02 | 2026-07-24
 amend_order     | confidence_below_threshold |    12 | 2026-07-10 | 2026-07-25
 cancel_order    |                            |     8 | 2026-07-02 | 2026-07-10
 amend_order     | stop_wrong_side            |     2 | 2026-07-17 | 2026-07-17
 amend_order     | amend_missing_price        |     1 | 2026-07-17 | 2026-07-17
 cancel_order    | target_order_id_missing    |     1 | 2026-07-25 | 2026-07-25
```

**Fix** (`app/graph/nodes/node_guard.py`): the threshold check is now scoped to
`action not in ('cancel_order', 'amend_order')`. Every other guard for those actions is
untouched — `target_order_id_missing`, `amend_missing_price` and `stop_wrong_side` still
apply (regression-tested).

## 4. Verification

### Live state, deployed container

```
$ docker compose exec -T ai-signal-generator grep -n "open_orders" /app/app/graph/gating.py
20:    open_orders is the same kind of carve-out, added later: this predicate was
35:    if state.get('open_orders'):
$ docker compose exec -T ai-signal-generator grep -n "if action not in ('cancel_order', 'amend_order')" /app/app/graph/nodes/node_guard.py
133:    if action not in ('cancel_order', 'amend_order'):
```

Predicate run inside the running container against the **real** listener response and the
exact geometry the 19:00 cycle logged:

```
live open_orders from listener: [{"order_id": "56947163834", "symbol": "ETH-USDT", "side": "buy", "price": 1821.9, "size": 0.1098, "filled_size": 0.0, "status": "resting", "created_at_ms": 1784930464811}, {"order_id": "56936760362", "symbol": "ETH-USDT", "side": "sell", "price": 1898.6, "size": 0.1051, "filled_size": 0.0, "status": "resting", "created_at_ms": 1784908867971}]
skip WITH the two resting orders : False
skip if no orders were resting   : True
```

### Tests

Full suite, in the service image:

```
$ docker run --rm -v /home/cristi/matp/ai-signal-generator:/w -w /w matp-ai-signal-generator \
    sh -c "pip install -q pytest && python -m pytest tests -q"
........................................................................ [ 63%]
..........................................                               [100%]
114 passed, 2 warnings in 19.05s
```

New tests (6):

- `test_llm_skip_no_range.py`: `test_geometric_range_resting_order_weak_fit_does_not_skip`,
  `test_geometric_range_no_resting_orders_still_skips` (both `[]` and `None`).
- `test_guard_sizing.py`: `test_amend_below_confidence_threshold_passes`,
  `test_cancel_below_confidence_threshold_passes`,
  `test_entry_below_confidence_threshold_still_rejected` (exemption stays scoped),
  `test_cancel_still_needs_target_order_id`.

### Deploy

```
$ ./scripts/redeploy.sh ai-signal-generator
...
NAME                         IMAGE                      COMMAND                  SERVICE               CREATED          STATUS
matp-ai-signal-generator-1   matp-ai-signal-generator   "uvicorn app.main:ap…"   ai-signal-generator   17 seconds ago   Up 3 seconds (health: starting)
✓ ai-signal-generator redeployed.
```

## 5. Found but NOT fixed (deliberate)

- **`target_order_id_missing`** (1 occurrence ever, 2026-07-25 06:00). Confidence 0.78
  passed the gate, but the model named the order in prose only — *"Long resting order (id
  56947163834) is far below lower boundary"* — without filling `target_order_id`. The
  prompt does instruct it (`builder.py:152-153`). Left alone at this frequency.
- **Failed open-orders fetch renders as "no resting orders."** `node_ingest.py:316` sets
  `open_orders = []` on exception, and `builder.py:144-145` renders `[]` as
  `"None — no resting orders."` A transient listener timeout can therefore tell the model
  it has nothing resting when it does, inviting a duplicate placement. `None` vs `[]` are
  distinguished everywhere except this error path. Note this also interacts with the new
  carve-out: a failed fetch falls through to the skip. Not fixed by choice this session.
