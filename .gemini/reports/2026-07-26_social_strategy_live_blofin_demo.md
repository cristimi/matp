# Social Listener — live execution built, 1-week trial armed on Blofin demo

**Date:** 2026-07-26
**Branch:** main
**Status:** DONE — live path built, verified with a real round-trip order, strategy armed.

---

## What was asked

Create a strategy, allocate $100, $10 per trade at 20x on Blofin, run it for a week —
driven by the social listener.

## Two constraints found first

1. **There is no live Blofin account.** Only `blofin-blofin-demo-v5vr` (`mode=demo`) exists.
   Cristi chose to run the week on demo, so this is paper money with the exact requested sizing.
2. **The social listener could not trade at all.** `execution_mode != "shadow"` only logged
   *"live emission is not built yet"*. That path is what this change builds.

A risk note I gave earlier was **wrong and is corrected here**: I said the strategy has no stop
loss. order-listener injects a *guaranteed* SL on every opening signal
(`compute_guaranteed_sl`, sized off live maintenance margin). Verified below — the test entry
came back with `sl_price` already set.

---

## Design

The listener emits into the **existing** order pipeline and touches no exchange:

```
social-listener  --POST /webhook/{strategy_id}-->  order-listener  -->  order-executor  -->  Blofin
```

That keeps the exchange-isolation rule intact and means order-listener still owns sizing, the
guaranteed stop loss, leverage, margin mode and drawdown guards. `social-listener` holds no
credentials and names no venue.

**A FLIP is two calls** — close to flat, then open the other way. That is what the webhook
contract expresses and exactly what the backtest priced (`backtest_replay`: "a flip is two
fills").

**Sizing is not duplicated.** `emitter._size_for()` reads `margin_per_trade` and
`default_leverage` from the strategy row; order-listener re-derives the same number and clamps
anything larger, so the strategies table stays the single place capital rules are edited.

### Three deliberate safety properties

- **Backfill never emits.** Only `phase == "live"` can send an order. The `backfill` phase acts
  unconditionally by design (`backfill_replay` bypasses the gates), so emitting there would
  re-fire every historical post on each restart.
- **Fail-closed.** If any webhook step fails, the decision is rewritten to
  `skipped/emit_failed`, the state is **not** advanced, and `social_position_state` keeps
  matching the exchange. The cost is a missed trade — the safe direction to be wrong in.
- **Live mode refuses to arm on a half-configured strategy.** Missing/deleted/disabled strategy,
  no `account_id`, no `webhook_secret`, non-positive `margin_per_trade`, or
  `default_leverage > max_leverage` all degrade to shadow with an explicit error.

Proven — the first redeploy happened while the strategy was still disabled:

```
ERROR social-listener execution_mode=live rejected for social-btc-astro
      (strategy is disabled) — staying in shadow
```

After enabling:

```
WARNING social-listener LIVE execution armed: strategy=social-btc-astro
        (Social BTC (AstronomerZero)) account=blofin-blofin-demo-v5vr
        allocation=100 margin/trade=10 leverage=20x isolated
```

---

## The strategy

```
        id        |            name             |  symbol  | enabled |       account_id        | alloc | margin | lev | maxlev | margin_mode | max_dd
------------------+-----------------------------+----------+---------+-------------------------+-------+--------+-----+--------+-------------+--------
 social-btc-astro | Social BTC (AstronomerZero) | BTC-USDT | t       | blofin-blofin-demo-v5vr |   100 |     10 |  20 |     20 | isolated    |     50
```

Exactly as asked: $100 allocation, $10 margin per trade, 20x, isolated, Blofin. `max_leverage`
is pinned to 20 so nothing can size past it, and `max_drawdown_pct=50` auto-disables the
strategy if allocation halves.

---

## End-to-end verification (a real order, not a simulation)

```
strategy: social-btc-astro | account: blofin-blofin-demo-v5vr | mark: 64507.0
computed size: 0.00310044 BTC  (= $200.00 notional)

INFO httpx HTTP Request: POST http://order-listener:8001/webhook/social-btc-astro "HTTP/1.1 200 OK"
INFO app.emitter emitted open_long for BTC size=0.00310044 -> 200
OPEN  ok= True | open_long->d7b4c0db-15a6-4dfa-890f-2653eaa4a3fa
```

The order filled, with the stop loss injected by order-listener:

```
id                | d7b4c0db-15a6-4dfa-890f-2653eaa4a3fa
symbol            | BTC-USDT      side | buy      signal | open_long
size              | 0.00310044    status | filled
leverage          | 20            margin_mode | isolated
sl_price          | 61565.4                     <-- auto-injected, 4.55% below entry
actual_fill_price | 64500.3
signal_source     | social_listener
```

$10 margin × 20x = **$200 notional**, as specified. Closed again to leave the book flat:

```
INFO app.emitter emitted close_long for BTC size=0.00310044 -> 200
CLOSE ok= True | close_long->72a186ee-3e8c-4e2f-9583-f2bfcb9e5165

 status | count          capital_allocation: 99.7594...  (round-trip cost ~$0.24)
--------+-------         allocation_peak:    100
 closed |     1          open positions:     0
```

Allocation compounding works — the round trip is booked back into the strategy.

---

## A stale-state bug this exposed

`social_position_state` still said **BTC = LONG**, set by msg 9716 on 2026-07-17 during the
shadow run — while the account held **no** position:

```
 source                  | asset | state | last_msg_id | updated_at
 telegram:AstronomerZero | BTC   | LONG  |        9716 | 2026-07-17 06:43:48+00
 open_positions: 0
```

Left alone the strategy would have sat idle all week: a new "open long" post evaluates to
`no_state_change` and is skipped, and a close would find nothing to close. Reset to `FLAT` so
the recorded stance matches the exchange:

```
 telegram:AstronomerZero | BTC   | FLAT  |        9716 | 2026-07-26 09:59:04+00
```

**Generalised:** shadow-mode state is not a valid starting point for live trading. Any future
shadow→live switch must reconcile `social_position_state` against `strategy_positions` first.
Worth a startup check rather than a manual step — noted, not built.

---

## What to expect this week

Tempered by the evidence, not the backtest headline:

- **The channel trades rarely.** 3 state changes in the last 14 days; the last actionable post
  was 2026-07-22. A week may produce **zero to three** trades. A quiet week is the most likely
  outcome and is not a failure.
- **The +6.16% backtest number should not be the expectation.** n=4, one leg still open, and the
  whole edge traced to a single post. The text-only path scored +2.53% over the same window.
- **Each trade risks ~$10** (the margin) with the stop ~4.5% away at 20x. Allocation
  auto-disables the strategy at a 50% drawdown.

## Files

- `social-listener/app/emitter.py` — new; webhook emission, flip = two steps
- `social-listener/app/main.py` — live-only emission, fail-closed, startup validation
- `social-listener/app/config.py` — `execution_strategy_id`, `listener_url`, `emit_timeout_seconds`
- `social-listener/app/db.py` — `load_execution_strategy()`, honour `mode` on shadow rows
- `social-listener/requirements.txt` — declare `httpx` (was only transitive)
- `docker-compose.yml` — `EXECUTION_MODE`, `EXECUTION_STRATEGY_ID`, `EXTRACTOR_FALLBACKS`
- `.env` — `SOCIAL_EXECUTION_MODE=live`, `SOCIAL_EXECUTION_STRATEGY_ID=social-btc-astro`

## To stop the trial

```bash
docker compose exec -T postgres psql -U matp -d matp \
  -c "UPDATE strategies SET enabled=false WHERE id='social-btc-astro';"
```

That alone halts new orders (order-listener rejects webhooks for a stopped strategy). To also
silence the listener, set `SOCIAL_EXECUTION_MODE=shadow` in `.env` and redeploy.
