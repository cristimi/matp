# Social Listener — 14-day backtest (v1 text-only vs v2 vision)

**Date:** 2026-07-25
**Branch:** main
**Window:** 2026-07-11 → 2026-07-25 (131 channel messages)
**Status:** DONE — read-only replay, no live tables mutated

---

## Method

Two new modules, both read-only with respect to `social_signal_log` /
`social_shadow_orders` / `social_position_state`:

- **`app/backtest_extract.py`** — re-runs a window of channel history through the **currently
  deployed** extractor and dumps JSON. Needed because every stored extraction in the window is
  `v1` text-only (`has_image = 0` for all 131) — replaying the DB would have backtested the
  extractor I replaced this morning, not the one in production.
- **`app/backtest_replay.py`** — replays signals through the **current** `statemachine.evaluate()`
  (age gate + implied reference, as shipped in `bf7fa5d`) and prices the resulting position
  timeline.

Data:

| Input | Source | Note |
|---|---|---|
| Signals | Telegram + `social_signal_log` | 131 messages, 18 carrying a chart image |
| Prices | Binance `BTC/USDT:USDT` 1m, 21 600 bars | Blofin's API capped at 1000 bars (~3.5d), too short |
| Funding | Binance funding history, 45 points | mean 0.00535% / 8h (≈5.86%/yr, longs pay) |
| Decision time | real `ingested_at` per row | p50 18.8s, p90 48.4s, max 57.5s after posting — **no** backfill contamination, the listener was live all window |

Execution assumptions: fill at the close of the 1m bar containing the decision, taker 6bps +
2bps adverse slippage per fill, a flip counted as two fills, $10 000 fixed notional at 1x, no
compounding, **no stop loss**.

State was seeded from the real position at window open (`BTC = LONG`, set by msg 9542 back on
2026-06-18). Replaying from FLAT instead invents an entry the live system never took — that
error alone changed the result by +1.5pp, so it is worth being explicit about.

---

## Result

```
window: 14d   BTC 64339.3 -> 64155.4 (-0.29% buy & hold)
seeded state at window open: {'BTC': 'LONG'}

==========================================================================
v1 — stored text-only extractions
==========================================================================
messages=131  actionable=11
gate outcomes: no_state_change=5, not_whitelisted=3, ok=1, ok_implied_ref=2
    msg side       entry      exit     held   gross $  fund $    MAE $    MFE $  img reason
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  n  ok_implied_ref
   9716 LONG     62831.9   64155.4   200.0h    210.64  -11.57   -52.01   651.29  n  ok_implied_ref (OPEN)

legs=2  wins=2  fills=4  worst intra-leg drawdown $-279.39 (-2.79%)
gross $257.06   fees $32.00   funding $-6.19   NET $218.87 (+2.19% on $10,000 notional)

==========================================================================
v2 — re-extracted WITH chart images
==========================================================================
messages=131  actionable=14
gate outcomes: low_confidence=1, no_state_change=7, not_whitelisted=2, ok=3, stale_price=1
    msg side       entry      exit     held   gross $  fund $    MAE $    MFE $  img reason
   9670 SHORT    63807.0   63510.8    60.2h     46.42    5.38  -279.39    58.21  y  ok
   9716 LONG     62831.9   64155.4   200.0h    210.64  -11.57   -52.01   651.29  y  ok (OPEN)

legs=2  wins=2  fills=4  worst intra-leg drawdown $-279.39 (-2.79%)
gross $257.06   fees $32.00   funding $-6.19   NET $218.87 (+2.19% on $10,000 notional)
```

**+2.19% net vs −0.29% buy & hold**, on two positions, over two weeks.

The replay reproduces the live shadow record exactly — the same three transitions the running
system actually made (9670 LONG→SHORT, 9702 SHORT→FLAT, 9716 FLAT→LONG), which is the best
available validation that the harness is faithful.

---

## What the vision upgrade actually changed

**Nothing in P&L — identical trades.** But the mechanism differs, and the difference is the
point:

- **v1** had to *reconstruct* the reference price for both entries (`ok_implied_ref` ×2),
  because the posts cited no price in text.
- **v2** read the price off the chart, so the gate compared the market against **the trader's
  own stated level** (`ok` ×3) rather than a proxy.
- **v2 found 3 more actionable signals** (14 vs 11) — and the gates filtered all three: one
  `stale_price` (market already >1% past the charted level — correctly refused to chase), plus
  two more `no_state_change`. Vision raised recall without adding trades, and the one genuinely
  new signal it surfaced was one you would not have wanted.
- **v2 also rejected one on `low_confidence`** that v1 never flagged as actionable at all.

So for this window the vision work bought precision and better-grounded gating, not more P&L.
That is a real finding, not a null one — but it is one window.

---

## Why I would not act on this number

- **n = 2.** Two positions is not a sample. Any statistic computed from it (win rate 100%,
  +2.19%) is noise.
- **82% of the P&L is unrealised.** The LONG (9716) is still open after 200 hours and accounts
  for $210.64 of the $257.06 gross. Marked to the last bar; it has not been exited.
- **The strategy takes serious heat with no stops.** The SHORT ended +$46.42 but drew
  **−$279.39** against itself first — 6× the eventual profit. On a 2-week window that survived;
  a stop-less always-in strategy is one bad leg from giving back everything.
- **Wrong venue's prices.** Binance perp, because Blofin only serves ~3.5 days of history. Basis
  between the two is small but this is not the venue that would fill.
- **The v2 extractions are a re-run today**, not what the system produced at the time. Temperature
  is 0, but that is not a determinism guarantee.
- Only 3 state changes in 14 days — the channel barely traded. The window mostly measures one
  BTC swing.

---

## Suggested next step

The bottleneck is sample size, not tooling. `backtest_extract.py` + `backtest_replay.py` will
run over any window; the binding constraint is that `social_signal_log` only goes back to
2026-06-11, and Telegram history goes back further than that. Re-extracting the full available
channel history (several hundred messages) would roughly triple the sample at a cost of a few
dollars in tokens — still small, but enough to say something about hit rate rather than
anecdote. Worth doing before anyone considers `execution_mode=live`.

Token cost of this run: 249 159 tokens for 131 re-extractions (~$0.75).
