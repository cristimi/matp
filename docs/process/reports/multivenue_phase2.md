# Multi-Venue Sourcing — Phase 2 Report (Stages D–E: the stream collector)

**Date:** 2026-07-07
**Plan:** `docs/design/ai_prompts/21_reference_exchange_sourcing.md` §3.2 / §4.2 / §4.3.
**Scope:** `ai-signal-generator` only (+ `redis` in its requirements). One migration-free
config surface change: none — Redis was already in the stack. No toggle changes, no
template edits, no other service touched.

Every claim below is backed by pasted in-container output.

---

## 1. What was built (Stage D)

`app/collector.py` — a supervisor task in the FastAPI lifespan running one watch-task per
venue × symbol × stream-kind over ccxt.pro for every enabled AI strategy symbol:

- `watchTrades` → per-minute CVD buckets in Redis (`d`elta / `g`ross / `n` trades /
  last `p`rice, 25h TTL); `watchLiquidations` → pruned 24h event zsets; last-price keys.
- Per-stream status hashes; reconnect with exponential backoff (5s→300s);
  `NotSupported` opt-out; strategy-set reconcile + dead-task watchdog every 5 min;
  `/health` gains a `collector` field.
- Readers (`read_cvd_window`, `read_liquidations_window`) never raise and enforce
  coverage: a venue contributes to a CVD window only with ≥90% of its bucket-minutes
  present; liquidation reads require a connected stream (else `None` — the section stays
  absent exactly like the old no-op).
- `fetch_cvd` rung 1 is now `stream_aggregate` — the venue set is whoever covers the
  smallest window; larger windows are claimed only if that set carries over. Falls back
  to `klines_taker` → trades snapshot → `None`. `fetch_liquidations` replaced its
  documented no-op with the Redis window read (forced SELL = long liquidated).

## 2. Two defects caught live during Stage D (both fixed before push)

1. **Thundering herd:** v1 gave each stream task its own ccxt.pro instance — 14
   concurrent `load_markets()` calls hammered the venues' catalog endpoints and most
   streams sat in reconnect loops (verified: only 3 of 14 producing). Fix: **one shared
   instance per venue** (ccxt.pro multiplexes watch loops). After: 18/18 alive, all
   venue×symbol combos producing.
2. **Failure caching:** `signal_sources` cached transient resolve failures for 1h — one
   binance REST hiccup parked binance out of every aggregate (caught when `fetch_cvd`
   fell back to bybit trades). Failures now cache 120s; successes keep 1h.

## 3. Stage E verification — the coverage rules working unattended

After >1h of accumulation (spanning two unrelated service restarts), `fetch_cvd` switched
to the stream method **on its own**:

```
fetch_cvd('hyperliquid', 'BTC/USDT') -> method=stream_aggregate source=bybit
ORDER FLOW (CVD):
CVD (1h window):      +$7,984,545
CVD (4h window):      not covered by snapshot
CVD Trend:            flat
CVD/Price Divergence: none
Coverage:             56.0 min of stream data, 165867 trades.
Source:               aggregate bybit (stream-collected)

fetch_cvd('blofin', 'HYPE/USDT') -> method=stream_aggregate source=binance+bybit+okx
CVD (1h window):      +$2,193,142
CVD (4h window):      not covered by snapshot
Coverage:             56.0 min of stream data, 141574 trades.
Source:               aggregate binance+bybit+okx (stream-collected)
```

Every honesty rule is visible in that output: the 1h window claimed at 56/60 minutes,
the 4h window refused (buckets don't span it yet — it self-claims as retention builds),
and BTC's venue set shrunk to bybit because the restarts left binance/okx under the 90%
bar for that hour — they rejoin automatically as coverage accumulates, and the label
always names the venues actually inside the number.

**Liquidations — live data in a field that was a no-op since Wave 3:**

```
LIQUIDATIONS:
Long Liqs (4h):       $36,289
Short Liqs (4h):      $511,454
Clusters near price:
  $44,986 @ 63707.3625
  $137,886 @ 63867.43125
  $364,871 @ 64027.5
Source:               aggregate binance (stream-collected, 0.1h covered; may under-report during cascades — venue stream throttling).
```

(Earlier in Stage D the very first minutes of uptime already caught $115k of BTC short
liquidations across binance+okx — first-ever data for this field.)

Honest-absence spot-checks from the Stage-D run: unknown symbol → readers return `None`;
renderer `''`; `fetch_cvd` refused the stream method below 1h coverage and fell back to
`klines_taker`/trades honestly.

## 4. Operational state

- `/health`: `"collector": {"running": true, "streams": 16, "alive": 16, ...}` (stream
  count varies with reconcile sweeps; watchdog restarts dead tasks, `unsupported` tasks
  stay retired).
- Per-cycle REST cost unchanged (readers hit Redis); steady-state network cost is the
  websocket traffic itself.
- Live effect: both strategies' CVD upgraded in place (`use_cvd` was already on);
  `use_liquidations` remains **false everywhere** — the field is now *available*, and
  only the `scalper` template consumes it (no strategy runs it). Enabling it anywhere is
  a deliberate future decision.

## 5. Known refinements (candidates, not scheduled)

- **Quiet-stream status:** a liquidation stream only writes `connected` after its first
  event, so `covered_hours` under-claims on quiet venues (bybit liq missing from the
  label while quiet; binance showed 0.1h after a reconnect while the event zset held the
  full 4h). Under-claiming is the safe direction, but a periodic heartbeat would make
  the label exact.
- ROADMAP `liquidation_data` entry can be updated to "live via Phase-2 collector
  (single-set of stream venues); Coinglass remains the paid true-aggregate option."

Plan status: **all of `21_reference_exchange_sourcing.md` v2 is now delivered** —
Phase 1 (aggregate OI, klines CVD) + Phase 2 (stream collector, true multi-venue CVD,
live liquidations).
