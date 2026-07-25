# Social Listener — close the ungated `priceless_market` path

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — deployed and verified against live Redis

---

## What the bug actually was

I had characterised `priceless_market` as "`marketdata.get_mark()` returned nothing". **That was
wrong**, and it matters because it changes the fix. Reading `statemachine.evaluate()`:

```python
if ref is None:                                    # ref = rec["reference_price"]
    if settings.entry_on_missing_price == "market":
        return act("priceless_market", tgt, sig)   # acts — mark never consulted
```

`ref` is the price *the trader cited in the post*. So `priceless_market` means: the post named
no price, therefore the staleness gate could not run, therefore **the entry was taken
unconditionally**. Worse, `main.py` only fetched a mark price when a reference existed:

```python
if phase == "live" and asset and rec["reference_price"] is not None:
    mark = await marketdata.get_mark(asset)
```

so a priceless signal had *no* market context at all. Both historical rows confirm it —
`reference_price` and `mark_price` are both NULL:

```
 channel_msg_id |       posted_at        | asset | action | reference_price | mark_price | decision |      reason
----------------+------------------------+-------+--------+-----------------+------------+----------+------------------
           9670 | 2026-07-14 13:45:12+00 | BTC   | FLIP   |                 |            | acted    | priceless_market
           9716 | 2026-07-17 06:43:20+00 | BTC   | FLIP   |                 |            | acted    | priceless_market
```

Two of the four `acted` decisions in the entire shadow history were ungated market entries.

A second exposure sat behind it: **nothing gated on signal age**. The catchup loop replays
missed messages through the live path, so a post recovered hours after a listener outage was
evaluated as if it had just landed.

---

## Fix

`market-ingestion` already keeps ~2000 closed 1m bars in
`stream:candles:blofin:BTC-USDT:1m` — a 34-hour window. That is enough to reconstruct what the
market was doing when a post was made, which is exactly the reference a priceless signal lacks.

- **`marketdata.get_close_at(asset, ts_ms)`** — close of the 1m bar covering `ts_ms`. Scans the
  stream by ID (IDs are ingest wall-clock, a beat after bar close) over a widened window and
  picks the bar by its own `t` field. Returns `None` when the bar is outside retention or an
  ingestion gap leaves only a much older bar (`implied_ref_max_gap_ms`, 5 min) — a stale bar is
  not "the price at post time".
- **`statemachine.evaluate()`** gains `implied_ref` and `now`:
  - **Age backstop first**, before any price logic: `posted_at` older than
    `max_signal_age_seconds` (900s) → `signal_too_old`. Applies to priced signals too.
  - Priceless signals now use `implied_ref` as the reference and run the **same**
    `staleness_pct` check → `ok_implied_ref` / `stale_implied_ref`.
  - Only when no reference can be established at all does it take a market entry, now called
    `priceless_recent` — and the age gate above has already proved the signal is fresh.
  - Backfill replay is untouched.
- **`main.py`** fetches a mark for every live signal with an asset (not just priced ones) and
  resolves the implied reference when the post cites no price.
- **`config.py`** — `max_signal_age_seconds`, `implied_ref_lookback_ms`, `implied_ref_max_gap_ms`.

No migration: `reason` on `social_shadow_orders` is free text (only `decision` is constrained).

---

## Verification

### `get_close_at` against the live stream

```
stream window: 34.3h  (1784866080000 .. 1784989680000)
mid-bar lookup   : got=64080.5 truth=64080.5  MATCH=True
bar-open lookup  : got=64080.5 truth=64080.5  MATCH=True
outside window   : None  (expect None)
far future       : None  (expect None — nearest bar too old)
```

### Decision matrix (`staleness_pct=0.01`, `max_signal_age=900s`)

```
--- priced signals (unchanged behaviour) ---
cited 100, mark 100.5, long                    -> acted   ok
cited 100, mark 102, long (chased 2%)          -> skipped stale_price
cited 100, no mark                             -> skipped no_mark

--- priceless: THE FIX ---
no price, implied 100, mark 100.5              -> acted   ok_implied_ref
no price, implied 100, mark 102 (2%)           -> skipped stale_implied_ref
no price, implied 100, mark 98 SHORT           -> skipped stale_implied_ref
no price, no implied, fresh                    -> acted   priceless_recent
no price, implied but no mark                  -> skipped no_mark

--- age backstop (catchup recovering an old post) ---
cited 100, mark 100.1, posted 20m ago          -> skipped signal_too_old
no price, posted 20m ago                       -> skipped signal_too_old
cited 100, mark 100.1, posted 14m ago          -> acted   ok

--- backfill replay is untouched ---
backfill, ancient, no price                    -> acted   backfill_replay
```

The second block is the whole point: `mark 102` against an implied reference of `100` is now
`stale_implied_ref`. Under the old code that identical input was `acted / priceless_market`.

### End-to-end, real signal 9716, live Redis

```
signal 9716: {'action_type': 'FLIP', 'asset': 'BTC', 'direction': 'LONG',
              'reference_price': None, 'confidence': 0.92}
posted_at: 2026-07-17 06:43:20+00:00

[real age, 8 days] mark=64123.9 implied=None            -> skipped / signal_too_old
[posted now]       mark=64123.9 implied=64124.1 drift=-0.000% -> acted / ok_implied_ref
[posted now, +2%]  mark=65406.6 implied=64124.1         -> skipped / stale_implied_ref
```

Persisted row shows `mark_price` populated on a priceless signal — previously always NULL:

```
 channel_msg_id | asset | reference_price |    mark_price    | decision |     reason
----------------+-------+-----------------+------------------+----------+----------------
      999999903 | BTC   |                 | 64123.9000000000 | skipped  | signal_too_old
```

Synthetic row deleted afterwards (`DELETE 1`); `social_position_state` unchanged
(`BTC | LONG | 9716`), service `Up`.

---

## Consequences worth knowing

- **This is strictly more conservative.** Both historical `priceless_market` acts would now be
  gated: 9716 replayed at its real age is `signal_too_old`, and either would be
  `stale_implied_ref` had the market moved >1% since the post. Expect fewer `acted` decisions.
- **`max_signal_age_seconds = 900` is a guess, not a measured value.** It is the first knob to
  tune once there's enough shadow data to see how quickly this channel's calls decay. Too tight
  and catchup-recovered signals are all dropped; too loose and the backstop does nothing.
- **Only BTC has an ingestion stream.** ETH is in `asset_whitelist` but `market-ingestion` only
  covers `BTC-USDT`, so an ETH priceless signal resolves no implied reference and falls to
  `priceless_recent` (age-gated only). Adding ETH to the ingestion symbol set would close that.
- Still shadow-only; nothing here routes signals to order-generator.
