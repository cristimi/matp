# BloFin hedge mode — long and short on one coin, one account

**Date:** 2026-08-02
**Services touched:** `order-executor`, `order-listener`, `dashboard-api`, DB migration 073
**Status:** shipped and unit-tested. **The live long+short run on BloFin is still outstanding**
— see "What is NOT verified" at the bottom.

---

## 1. What the exchange actually supports (checked, not remembered)

The question that started this was whether BloFin's **Multi-Position** feature can give us several
positions per coin. It cannot, through the API. The full API reference was downloaded
(`docs.blofin.com`, 795 KB, 2026-08-02) and read as raw parameter tables rather than trusted to a
summary:

| Endpoint | What it accepts |
|---|---|
| `POST /api/v1/account/set-position-mode` | `positionMode`: **only** `net_mode` \| `long_short_mode` |
| `POST /api/v1/trade/order` | `positionSide` — *"Default net for One-way Mode, long or short for Hedge Mode. It must be sent in Hedge Mode."* **No `positionId`.** |
| `POST /api/v1/trade/close-position` | `instId`, `marginMode`, `positionSide`, `clientOrderId`, `brokerId`. **No `positionId`.** |
| `POST /api/v1/trade/order-tpsl` | `positionSide` *Required* — same hedge rule |
| `POST /api/v1/account/set-leverage` | `positionSide` — *"Only required when margin mode is isolated in long/short mode"* (leverage is per leg) |
| `GET /api/v1/account/positions` | returns `positionId`, `positionSide` — **read-only**; `positionId` is accepted only as a filter on `positions-history` |

Multi-Position (several positions in the *same* direction) exists in BloFin's app and help centre,
but nothing in the public API can address an individual position. The API ceiling is **one long leg
+ one short leg per instrument**, and that is what was built.

---

## 2. What changed

### DB — migration 073
`exchange_accounts.position_mode` (`net` | `hedge`, NOT NULL, default `net`, CHECK-constrained).
The executor is the only writer, and only after the exchange has confirmed the switch.

```
$ docker compose exec -T postgres psql -U matp -d matp -v ON_ERROR_STOP=1 < db/migrations/073_account_position_mode.sql
ALTER TABLE
DO
COMMENT
NOTICE:  Migration 073 verified OK: exchange_accounts.position_mode present, default net
DO

              id              |  exchange   | mode | position_mode
------------------------------+-------------+------+---------------
 blofin-blofin-demo-v5vr      | blofin      | demo | net
 hyperliquid-hyperliquid-hqdy | hyperliquid | demo | net
```

`strategy_positions` needed nothing: its unique index is already
`(strategy_id, symbol, side) WHERE status='open'`, so one open long and one open short coexist. The
migration asserts that index still contains `side`, so narrowing it later fails loudly instead of
breaking hedge mode silently.

### order-executor
- `BlofinAdapter(credentials, mode, position_mode)`; `_position_side()` maps a position side to the
  wire value and **raises** rather than guessing on a hedge account.
- `positionSide` now correct on: entries, closes, partial closes, `set-leverage` (per leg), and
  `order-tpsl`. Net-mode payloads are byte-for-byte what they were.
- `get_open_positions()` reads the side from `positionSide`, falling back to the sign of the
  quantity. In hedge mode **both legs report a positive quantity**, so the old sign test would have
  labelled the short a long.
- `list_trigger_orders(symbol, position_side)` filters by leg. This one matters most: `modify-stops`
  *cancels everything it reads*, so unscoped on a hedge account, moving the long's stop would delete
  the short's stop and leave that leg naked.
- `get_closed_position_details(..., side)` and `_recover_close_fill(..., position_side)` pick the
  right leg — both feed realised PnL, and the wrong leg mis-books both positions.
- New `get_position_mode()` / `set_position_mode()`, plus `GET`/`POST /accounts/{id}/position-mode`.
  The switch goes: exchange first → read back → only then the DB column.

### order-listener
- `_account_position_mode()` — Redis-cached, **fails closed**: unreadable or absent reads as `net`.
- Same-symbol guard: on a hedge account the *opposite* side is no longer a conflict. The *same* side
  still is — two strategies long the same coin still merge into one exchange position with no way to
  attribute size, in hedge mode exactly as in net mode.
- Flip-close: the inference "this entry returned realised PnL, so the exchange netted away the
  opposite leg" is **false** on a hedge account and is now skipped there. Running it would have
  closed a live position in the DB while it kept running on the exchange.
- `target_position=flat` now closes **every** open leg for the symbol, and reports the failing leg
  rather than the first one. It used to `fetchrow` a single arbitrary leg.

### dashboard-api
`position_mode` on `GET /accounts`; `GET`/`POST /accounts/:id/position-mode`. Hedge is refused for
non-BloFin accounts before the executor is even called.

---

## 3. Tests

### Unit — order-executor: 86 passed (32 of them new, `tests/test_blofin_hedge.py`)

```
$ docker compose exec -T order-executor python -m pytest tests/ -q
86 passed, 1 warning in 80.14s (0:01:20)
```

The new cases assert the actual wire payload for: hedge/net entries, hedge/net closes, per-leg
leverage, full close, partial close, both-legs position parsing, trigger filtering, trigger
placement, closed-position history by leg, fill recovery by leg, mode parsing, read-back
disagreement, and the exchange's refusal being passed through.

**A test found a real bug in this change.** `modify_stops` already binds `leg` in
`for leg in placed_this_attempt:`, and the new hedge variable was also called `leg` — so after the
first placement, the verify read-back was handed a *trigger dict* as its position filter. Renamed to
`leg_side`, with a comment at the declaration explaining why.

```
last_position_side = {'tpsl': 'tp', 'oid': 'new-tp'}      # before
last_position_side = 'short'                              # after
```

### Unit — order-listener: 14 passed (`tests/test_hedge_mode.py`)

```
$ docker compose exec -T order-listener python -m pytest tests/test_hedge_mode.py -q
14 passed, 2 warnings in 11.96s
```

Covers: net still rejects the opposite side; hedge allows it; hedge still rejects the same side (both
directions, and with both legs already open); net still books a flip-close; hedge never does; flat
closes both legs; flat reports the failing leg; single-leg flat unchanged; unknown/absent mode reads
as `net`; hedge is read and cached.

**Pre-existing failures, not caused by this work.** Five tests in `test_fill_size_open_path.py` and
`test_webhook_handler.py` fail. Verified by running them against `git show HEAD:` of
`webhook_handler.py` inside the container — identical five failures:

```
FAILED tests/test_fill_size_open_path.py::test_create_position_uses_actual_fill_size_when_provided
FAILED tests/test_fill_size_open_path.py::test_create_position_falls_back_to_payload_size_when_fill_size_none
FAILED tests/test_webhook_handler.py::test_valid_token_passes_auth
FAILED tests/test_webhook_handler.py::test_quote_variant_accepted_when_flag_on
FAILED tests/test_webhook_handler.py::test_daily_signal_cap_returns_429
5 failed, 8 passed, 2 warnings in 12.53s      # ← on the ORIGINAL file
```

### Live — against the real BloFin demo account

Mode read, stored vs. exchange:
```
$ wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/position-mode
{"account_id":"blofin-blofin-demo-v5vr","position_mode":"net","live":"net","agrees":true}
```

Net-mode position reads unregressed through the new side-resolution code — the live BTC short is
still read as a short with a positive size:
```
$ wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions
[{"symbol":"BTC-USDT","side":"short","size":"0.0032","entry_price":"63454.9...","leverage":20,...},
 {"symbol":"BNB-USDT","side":"short","size":"0.150","entry_price":"588.37...","leverage":10,...}]
```

The exchange's refusal to switch while positions are open — passed through verbatim, nothing
persisted:
```
$ wget -qO- --post-data='{"position_mode":"hedge"}' \
    http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/position-mode
{"success":false,"position_mode":null,
 "error":"Unable to adjust position mode. Please cancel any open orders, close positions, and stop trading bots first."}

           id            | position_mode
-------------------------+---------------
 blofin-blofin-demo-v5vr | net            ← unchanged
```

dashboard-api:
```
$ curl -s http://localhost/api/dashboard/accounts
[{"id":"blofin-blofin-demo-v5vr","exchange":"blofin","mode":"demo","label":"Blofin Demo",
  "is_active":true,"position_mode":"net", ...}, ...]

$ curl -s http://localhost/api/dashboard/accounts/blofin-blofin-demo-v5vr/position-mode
{"account_id":"blofin-blofin-demo-v5vr","position_mode":"net","live":"net","agrees":true}

$ curl -s -X POST -d '{"position_mode":"hedge"}' .../accounts/hyperliquid-hyperliquid-hqdy/position-mode
{"error":"Hedge mode is BloFin-only; hyperliquid accounts stay in net mode"}

$ curl -s -X POST -d '{"position_mode":"multi"}' .../accounts/blofin-blofin-demo-v5vr/position-mode
{"error":"position_mode must be 'net' or 'hedge'"}
```

---

## 4. What is NOT verified

**The live long+short run has not happened.** BloFin refuses the mode switch while anything is open,
and the demo account is carrying two strategy positions (BTC-USDT short, BNB-USDT short). Closing
them is the operator's call, and the operator chose to wait until the account is flat naturally.

Still to run, once the account has no open positions and no running bots on it:

1. flip to hedge, confirm `agrees: true`;
2. open a long and a short on the same coin, confirm both legs live with the right sides and sizes;
3. attach stops to each leg, confirm moving one does not cancel the other's;
4. partially close one leg, confirm only that leg shrinks and the other's stop survives;
5. close each leg fully, confirm PnL is booked to the right position;
6. confirm the reconciler agrees with the exchange throughout;
7. flip back to net.

**Nothing is live yet:** both accounts remain `position_mode = 'net'`, so every code path in
production is the one it was before. Hedge behaviour only starts when an account is deliberately
switched.

---

## 5. Known limitation worth stating plainly

The AstronomerZero social strategy still cannot hold two of its own positions. Hedge mode is an
execution-layer capability; `social-listener/app/statemachine.py` is a three-state machine per
(source, asset) — `FLAT` / `LONG` / `SHORT`, with `LONG→SHORT` expressed as `flip_to_short`. It has
no vocabulary for "long and short at once", so it will keep flipping rather than stacking.

What hedge mode *does* unlock today: two **different** strategies can now hold opposite sides of the
same coin on one account — an AI strategy long BTC while the social strategy is short it — which the
listener used to reject with `opp_pos_conflict`. Making the social strategy itself multi-position is
a separate change to its state machine and its stance table, not started here.

---

## 6. Housekeeping note

`db/init.sql` was not regenerated. It already lags migrations 070–072 (`mark_price_at_decision` is
absent from it), so regenerating now would bundle several unrelated schema changes into this commit.
Flagging rather than fixing: a fresh deploy currently needs migrations 070+ applied by hand.
