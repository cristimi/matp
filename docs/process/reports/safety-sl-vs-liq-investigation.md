# Investigation: safety stop-loss landing beyond liquidation price on open shorts

Date: 2026-07-02
Type: read-only investigation — no code/DB changes made.

## Summary

**Confirmed, real, currently active.** One of the two open shorts
(`tv-btc-test-hl-94e1`, BTC-USDT, Hyperliquid, 40x) has a guaranteed safety
stop-loss that sits **beyond** the exchange's real liquidation price. The
position would liquidate before the safety SL could ever trigger.

**Root cause: Hypothesis 2 — hardcoded `MMR = 0.01` in
`order-listener/app/config.py` underestimates the real maintenance-margin
rate at high leverage.** Hyperliquid's real MMR for BTC at 40x (BTC's own
exchange max leverage) is ~1.25–1.3%, not the 1% the formula assumes. The
formula term (`1/L - MMR`), not the `MIN_SAFETY_SL_DIST` floor, is what
"won" in the failing case — **Hypothesis 1 (flat floor override) is not
the cause of the current bug**, though it remains a latent risk (see below).
**Hypothesis 3 (side/rounding error) is ruled out** — the short-side
formula direction and rounding are both correct and the rounding error is
orders of magnitude too small to matter.

## A. Open shorts in `strategy_positions`

```
docker compose exec postgres psql -U matp -d matp -c "
SELECT sp.id, sp.strategy_id, sp.exchange, sp.symbol, sp.side, sp.leverage, sp.margin_mode,
       sp.entry_price, sp.liquidation_price, sp.status
FROM strategy_positions sp
WHERE lower(sp.status)='open' AND lower(sp.side)='short'
ORDER BY sp.opened_at DESC;"
```

```
                  id                  |     strategy_id     | exchange |  symbol   | side  | leverage | margin_mode | entry_price | liquidation_price | status 
--------------------------------------+---------------------+----------+-----------+-------+----------+-------------+-------------+-------------------+--------
 cb73ec38-c9ca-4ad3-8a35-a6fcb827cb09 | tv-btc-test-hl-94e1 | auto     | BTC-USDT  | short |       40 | isolated    |     61693.0 |                   | open
 9be7efbf-2af2-4356-82f2-90ad4fa3c674 | hype-test-7db4      | auto     | HYPE-USDT | short |       10 | isolated    |      65.165 |                   | open
```

Note: `strategy_positions.liquidation_price` is **not populated** for either
row (the column exists but this reconciliation path apparently doesn't
backfill it) — the exchange-reported value has to be pulled live via
order-executor.

`account_id` for each (joined from `strategies`):

```
                  id                  |     strategy_id     |          account_id          
--------------------------------------+---------------------+------------------------------
 9be7efbf-2af2-4356-82f2-90ad4fa3c674 | hype-test-7db4      | blofin-blofin-demo-v5vr
 cb73ec38-c9ca-4ad3-8a35-a6fcb827cb09 | tv-btc-test-hl-94e1 | hyperliquid-hyperliquid-hqdy
```

## B. Exchange-reported liquidation prices (order-executor `:8004`, live)

```
docker compose exec nginx wget -qO- http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions
docker compose exec nginx wget -qO- http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/positions
```

Blofin (`blofin-blofin-demo-v5vr`):
```json
[
  {"symbol":"SUI-USDT","side":"long","size":"142.0","entry_price":"0.695100000000000000","leverage":10,"mark_price":"0.7406217523333334","unrealized_pnl":"6.4640888313333428","liquidation_price":"0.630063450498539631"},
  {"symbol":"HYPE-USDT","side":"short","size":"3.10","entry_price":"65.165000000000000000","leverage":10,"mark_price":"65.52165000000001","unrealized_pnl":"-1.105615000000031","liquidation_price":"71.162017273900526159"}
]
```

Hyperliquid (`hyperliquid-hyperliquid-hqdy`):
```json
[
  {"symbol":"BTC-USDT","side":"short","size":"0.02","entry_price":"61693.0","leverage":40,"mark_price":"61809.0","unrealized_pnl":"-2.31992","liquidation_price":"62435.7063209877"}
]
```

Both adapters (`hyperliquid.py:160`, `blofin.py:355`) pass the exchange's
own `liquidationPx` / `liquidationPrice` straight through with no
client-side recomputation — these are the exchange's authoritative
numbers, not something our code derives.

(SUI-USDT long is a third open position on the Blofin account, not created
by `webhook_handler.py`'s guaranteed-SL path in this test set — included
below as a supplementary long-side data point, not part of the requested
short-side set.)

## C. Per-position comparison

Formula from `webhook_handler.py:66`: `sl_distance = max(1/L - MMR, MIN_SAFETY_SL_DIST)`,
with `MMR = 0.01`, `MIN_SAFETY_SL_DIST = 0.005` (`config.py:27-29`).

| Position | Side | Lev | Entry | Formula dist (1/L−MMR) | Term that won | SL distance used | Computed SL price | Real liq price | Real liq distance | Implied real MMR | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USDT (hyperliquid, `tv-btc-test-hl-94e1`) | short | 40x | 61693.0 | 1.5000% | **formula** | 1.5000% | 62618.395 | 62435.7063 | 1.2039% | **1.2961%** | **BUG — SL beyond liquidation** |
| HYPE-USDT (blofin, `hype-test-7db4`) | short | 10x | 65.165 | 9.0000% | formula | 9.0000% | 71.02985 | 71.16202 | 9.2028% | 0.7972% | safe (SL before liq) |
| SUI-USDT (blofin, supplementary, long) | long | 10x | 0.6951 | 9.0000% | formula | 9.0000% | 0.632541 | 0.630063 | 9.3564% | 0.6436% | safe (SL before liq) |

For BTC-USDT: computed SL (62618.395) is **past** the real liquidation
price (62435.706) — the position liquidates first, at a smaller move
(1.20%) than the SL was set to require (1.50%). The other two positions
are currently safe because their implied real MMR (0.64–0.80%) happens to
sit *under* the 1% assumption, so the "conservative" comment in
`config.py:26` holds for them but not for BTC at 40x.

## D. Root-cause confirmation: real MMR, not the floor

The floor (`MIN_SAFETY_SL_DIST`) never engaged in either failing or passing
case — `1/L - MMR` exceeded `0.005` in all three rows above (floor only
overrides once `L > 1/(MMR+0.005) ≈ 66.7x`). Checked configured strategy
`max_leverage` caps — the highest configured anywhere is **50x**
(`matp-test-harness-fe19`, `tv_test_harness`), still under the 66.7x
crossover, so **Hypothesis 1 is not reachable today** given current
strategy configs. It remains a real latent bug if any strategy's
`max_leverage` is ever raised above ~67x — flagging for the backlog, not
fixing here per instructions.

Hypothesis 2 (hardcoded MMR wrong) is the confirmed cause for BTC:

```
docker compose exec nginx wget -qO- --post-data='{"type":"meta"}' \
  --header='Content-Type: application/json' https://api.hyperliquid.xyz/info
  → BTC: {'szDecimals': 5, 'name': 'BTC', 'maxLeverage': 40, 'marginTableId': 56}

docker compose exec nginx wget -qO- --post-data='{"type":"marginTable","id":56}' \
  --header='Content-Type: application/json' https://api.hyperliquid.xyz/info
  → {"description":"tiered 40x","marginTiers":[{"lowerBound":"0.0","maxLeverage":40},
     {"lowerBound":"150000000.0","maxLeverage":20}]}
```

BTC's exchange max leverage is 40x — exactly the leverage this position
used. Hyperliquid's standard maintenance-margin formula for the base tier
is `MMR ≈ 1 / (2 × maxLeverage)`, i.e. `1/(2×40) = 1.25%` for BTC at this
tier — versus the code's hardcoded `MMR = 0.01` (1%). That lines up with
the 1.2961% implied real MMR backed out from the live liquidation price in
section C (the small residual above 1.25% is consistent with Hyperliquid
folding a fee/funding buffer into the live `liquidationPx`, which the
static formula doesn't and can't model).

**In short: the hardcoded MMR is a single global constant, but real MMR is
leverage-tier-dependent — it rises as leverage approaches (or is at) a
symbol's exchange max leverage. The code's "MMR=0.01 is conservative"
assumption in `config.py:26` is only true when leverage is well below the
symbol's max; it silently inverts (becomes non-conservative) once leverage
gets close to max leverage, exactly the 40x-of-40x-max case here.**

Hypothesis 3 (side/rounding) — ruled out:
- Short-side formula (`webhook_handler.py:69`, `entry_ref * (1 + sl_distance)`)
  is the correct direction; SL is placed above entry for shorts as expected.
- `_infer_price_decimals(61693.0)` → price ≥ 10,000 → 1 decimal place
  (`webhook_handler.py:42-52`). Rounding error is at most ±0.05 on a price
  gap of ~183 (computed SL 62618.395 vs real liq 62435.706) — three orders
  of magnitude too small to be the cause or to fix it.

## Does this affect longs too?

Yes, symmetrically — the formula and the underlying MMR assumption are
side-agnostic (`webhook_handler.py:66-70` applies the same `sl_distance` to
both branches). The SUI-USDT long in this dataset happens to be safe only
because its leverage (10x) is far below its exchange max leverage, keeping
its implied real MMR (0.64%) under the hardcoded 1%. Any long at leverage
close to its symbol's exchange max, on an exchange/symbol whose real tiered
MMR exceeds 1% at that tier, would show the identical failure mode. This is
a leverage-relative-to-symbol-max problem, not a short-specific one — it is
a coincidence of this test data that the one instance found is a short.

## Proposed fix direction (not implemented)

1. **Source real MMR instead of assuming it.** Hyperliquid exposes
   `maxLeverage` and `marginTiers` per symbol via the `meta` /
   `marginTable` info endpoints (confirmed reachable above); MMR can be
   derived from `1/(2×tier_maxLeverage)` per symbol/notional tier rather
   than a single global constant. Blofin likely has an equivalent
   instruments/leverage-tier endpoint that isn't currently queried by
   `blofin.py` — needs a lookup to confirm what it exposes.
2. **If real per-symbol MMR isn't reliably obtainable at order time,**
   scale a safety buffer by how close `effective_leverage` is to the
   symbol's exchange max leverage (already fetchable via
   `HyperliquidAdapter.get_max_leverage()`, `hyperliquid.py:178`), e.g.
   widen the assumed MMR as leverage approaches max instead of holding it
   flat at 1%.
3. **`MIN_SAFETY_SL_DIST` floor should scale with leverage**, not be a flat
   0.5%, to close the latent Hypothesis-1 gap for any future strategy
   configured above ~67x leverage.
4. Regardless of formula fix, consider a **belt-and-suspenders check at
   order time**: after computing the guaranteed SL, compare against the
   exchange's own live `liquidationPx` (already available via
   `get_open_positions()`/adapter) and force liquidation_safe if the
   computed SL would sit beyond it — this makes the safety net correct
   even if the analytic MMR estimate is ever wrong again.
5. Populate `strategy_positions.liquidation_price` from the exchange
   (currently NULL for both positions in section A) so this class of
   problem is visible in the DB/dashboard without an ad hoc live query.

## Constraints honored

Read-only: no code edits, no migrations, no redeploys were performed in
this session. Table/column/route names were confirmed live before use
(`\d strategy_positions`, `\d orders`, `order-executor/app/main.py:181-202`)
rather than trusted from the investigation prompt.
