# Binance USDⓈ-M futures as a third trading venue

Date: 2026-07-28

Binance now sits alongside Blofin and Hyperliquid with the same functional surface.
Nothing above the adapter layer needed to change to trade on it.

## Parity, proven mechanically

The contract is 10 abstract methods, 6 optional overrides, and 3 methods that are
not in the base class but which `order-executor/app/main.py` calls on every
adapter (`list_trigger_orders`, `cancel_order`, `place_trigger_orders`). Rather
than assert parity by eye, it is checked by reflection against the running
container:

```
$ docker compose exec -T order-executor python -c "...reflection over the ABC..."
abstract methods: 10
unimplemented on Binance: none
methods on blofin+HL but NOT on binance: none
binance instantiates: True
```

## Three places Binance genuinely differs

These are not stylistic; each one changes how the adapter has to behave.

**1. Quantity is base asset, not contracts.** Blofin needs `_to_contracts` /
`_to_base` on every path. Binance takes `0.005` for 0.005 BTC, so all that is
needed is rounding to the symbol's LOT_SIZE step. That deleted a whole class of
conversion bug — and introduced a different one (below).

**2. TP/SL cannot ride on the entry order.** Blofin accepts `tpTriggerPrice` /
`slTriggerPrice` on `/api/v1/trade/order`. Binance has no such field: protection
is a *separate* STOP_MARKET / TAKE_PROFIT_MARKET order. `submit_order` places the
entry, then the triggers, and if the triggers fail it logs
`FILLED BUT UNPROTECTED` and sets `error_msg` on an otherwise successful result —
an entry that filled with no stop is the one outcome that must never read as a
clean success.

**3. Triggers use `closePosition=true` instead of a fixed size.** This is a
deliberate improvement, not a shortcut: the trigger tracks the position, so after
a partial close the remaining stop still covers exactly what is left, and the
exchange cancels both legs when the position goes flat. The other two venues
carry a fixed size, which is why order-listener has
`_resize_stops_after_partial_close` to re-issue them by hand. `list_trigger_orders`
reports `sz: "position"` for these rather than inventing a number.

One-way (net) position mode is required and enforced. In hedge mode Binance
rejects `reduceOnly` and an "entry" can open a second opposite leg, so
`_check_position_mode` refuses — at account-validation time, not at the first
trade.

## A real bug the live check caught

The unit tests passed against a fixture with a 0.001 step. Run against the actual
exchange, BTCUSDT's step is **0.0001**, and:

```
qty  0.0059 -> 0.0058       # wrong: 1.7% under
```

`math.floor(0.0059 / 0.0001)` is **58**, not 59 — the float division lands on
58.99999999999999. Every quantity on this venue went through that line, so the
error was on every order. Fixed with Decimal quantisation (and the same fix
applied to tick rounding, which has the same failure mode), then re-verified
against the real spec:

```
qty 0.0059    -> 0.0059
qty 0.00295   -> 0.0029
qty 0.0001    -> 0.0001
qty 5e-05     -> 0
px  64000.04        -> 64000
px  64000.06        -> 64000.1
px  63378.69028986  -> 63378.7
```

A regression test pins it with the real step size. This is the argument for live
verification: the fixture was self-consistent and wrong.

## Verification

**46 tests pass** in the running container (29 new for Binance, 17 pre-existing,
no regressions):

```
$ docker compose exec -T order-executor python -m pytest /app/tests -q
..............................................                           [100%]
46 passed in 38.10s
```

The new tests pin the things that would silently corrupt a trade: the signature
covers the query string verbatim; quantities round *down* and prices round to
*nearest*; trigger types are excluded from `get_open_orders` and are the only
thing in `list_trigger_orders`; `list_trigger_orders` returns `None` (not `[]`) on
a failed read; `realizedPnl` sums to GROSS and commission is positive-means-paid;
an expired post-only entry is `rejected`, never `filled`; leverage over the max is
rejected rather than clamped; hedge mode blocks before anything reaches the
exchange; and a failed positions read raises rather than reporting flat.

**Live round trip to Binance**, through the full chain (dashboard-api →
executor → adapter), with a deliberately bad key:

```
$ ... POST /credentials/validate {"exchange":"binance","mode":"demo",...}
{"valid":false,"error":"Binance auth failed: binance /fapi/v2/account HTTP 401: -2014 API-key format invalid."}
```

That is Binance's own error text coming back — so the testnet host, the signing,
the transport and the error surfacing all work end to end.

**Live public data** (needs no credentials), against `demo-fapi.binance.com`:

```
instruments: 528  e.g. ['0GUSDT', '1000000BOBUSDT', '1000000MOGUSDT', ...]
BTCUSDT present: True
min order size BTCUSDT: 0.0001
mark price BTCUSDT   : 63378.69028986
specs BTCUSDT        : {'price': {'mode': 'tick', 'tick': 0.1}, 'size': {'dp': 4}}
```

**Symbol formatting** in the running listener:

```
binance  -> BTCUSDT
blofin   -> BTC-USDT
hyperliq -> BTC
```

All four services redeployed and healthy; `dashboard-api` and `dashboard-ui`
type-check clean.

## What else changed

- `registry.py` and the executor's `/credentials/validate` know the venue.
- `symbol_factory.py` gains the `BTCUSDT` format.
- `accounts.ts` accepts `binance`, and the long-standing
  `TODO(blofin-dedup)` is now closed: a shared api-key duplicate check covers
  both Blofin and Binance. Two accounts on one key are indistinguishable
  downstream and would double-count PnL.
- Dashboard UI: venue in the picker and the add-account form (API key + secret),
  plus a `badge-amber` class, which did not exist and would have rendered an
  unstyled badge.

## Not done, deliberately

**Spread trading is untouched.** `spread_trade._resolve_accounts` requires *both*
a Hyperliquid and a Blofin account and places opposing legs on that specific pair.
Adding `binance` to that tuple would make an active Binance account mandatory and
break funding-harvest for anyone without one. Making that feature venue-agnostic
is its own piece of work, not adapter parity.

## What still needs your testnet keys

Everything above is verified except the authenticated trading paths, which cannot
be exercised without a real key. Specifically unproven against the exchange:
order placement and fills, close and partial close, trigger placement, amend,
balance, positions, and closed-position PnL/fee attribution.

The code is written against the current documented API and the response parsing
is defensive, but exchange adapters are exactly where undocumented behaviour
bites — the rounding bug above is the proof.

To finish the job: create a Binance **USDⓈ-M futures testnet** key, add the
account in the dashboard (Accounts → Add → Binance → demo), and I will run the
same live checks the other two venues got. Two things to set on the testnet
account first: **One-way position mode** (the adapter refuses hedge mode), and
some testnet USDT.
