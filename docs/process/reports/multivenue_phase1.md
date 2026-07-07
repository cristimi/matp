# Multi-Venue Sourcing — Phase 1 Report (Stages A–C)

**Date:** 2026-07-07
**Plan:** `docs/design/ai_prompts/21_reference_exchange_sourcing.md` (v2, validated;
§4.3 amended mid-build per probe outcome, deferral approved).
**Scope:** `ai-signal-generator` only + one compose env line (`SIGNAL_VENUES`). No
migration, no toggle changes, no template edits, no other service touched.

Every claim below is backed by pasted in-container output.

---

## 1. Probe battery (Stage A — drove three plan corrections)

**Binance klines taker-volume confirmed** (the Phase-1 CVD method):

```
binance fapiPublicGetKlines BTCUSDT 5m — last row (12 fields):
[1783407600000, "63309.90", "63424.20", "63275.00", "63356.30", "707.268", 1783407899999, "44820495.92750", 12585, "344.608", "21836321.97220", "0"]
  parsed: total_quote=44,820,496  taker_buy_quote=21,836,322  delta=-1,147,852
```

**Perp-vs-spot correction:** first probe run resolved BTC/USDT to *spot* on all three
venues and OI failed with `"supports contract markets only"` (binance returned OI in BTC
amount, not USD). Consequences built into Stage A: perp-first resolution + USD
normalization. Second run, perp-resolved:

```
binance  BTC/USDT   -> BTC/USDT:USDT      OI_usd=6,408,758,128 (from amount 101173.878 x px 63344.0)
binance  HYPE/USDT  -> HYPE/USDT:USDT     OI_usd=359,676,632 (from amount 5063657.16 x px 71.031)
bybit    BTC/USDT   -> BTC/USDT:USDT      OI_usd=3,628,683,194 (from amount 57291.5 x px 63337.2)
bybit    HYPE/USDT  -> HYPE/USDT:USDT     OI_usd=245,722,768 (from amount 3461079.05 x px 70.996)
okx      BTC/USDT   -> BTC/USDT:USDT      OI_usd=1,924,770,347 (openInterestValue)
okx      HYPE/USDT  -> HYPE/USDT:USDT     OI_usd=108,732,872 (openInterestValue)
```

**REST liquidations falsified** (plan §4.3 amended, deferral approved): okx
`fetch_liquidations` raised `NotSupported` (ccxt 4.5.59 — the plan's claim was wrong);
ccxt-wide survey + live 4h BTC window:

```
ccxt exchanges with fetchLiquidations=True: ['bitfinex', 'bitmex', 'deribit', 'gate', 'htx']
bitfinex: 0 entries   bitmex: 0 entries   deribit: 0 entries   gate: 0 entries
htx: 6 entries in 4h (sample notional ~$6,290)
```

Liquidations skip Phase 1; the Phase-2 collector path is confirmed viable:

```
ccxt.pro available: True, version 4.5.59
  binance  watchTrades=True watchLiquidations=True watchLiquidationsForSymbols=True
  bybit    watchTrades=True watchLiquidations=True watchLiquidationsForSymbols=False
  okx      watchTrades=True watchLiquidations=emulated watchLiquidationsForSymbols=True
```

## 2. Stage A — `signal_sources.py` + aggregate open interest

Venue resolver (config `SIGNAL_VENUES=binance,bybit,okx`, env verified in-container),
perp-first, capability-filtered, ~1h cache:

```
resolve_signal_venues('BTC/USDT', 'fetchOpenInterest') -> [('binance', 'BTC/USDT:USDT'), ('bybit', 'BTC/USDT:USDT'), ('okx', 'BTC/USDT:USDT')]
resolve_signal_venues('HYPE/USDT', 'fetchOpenInterest') -> [('binance', 'HYPE/USDT:USDT'), ('bybit', 'HYPE/USDT:USDT'), ('okx', 'HYPE/USDT:USDT')]
cached re-resolve took 0.0 ms
```

Aggregate OI, called exactly as `node_ingest` calls it, rendered inside SENTIMENT:

```
Open Interest (binance+bybit+okx): $11.92B (-6.95% 24h)     <- ai-btc (hyperliquid; previously rendered $0.00B)
Open Interest (binance+bybit+okx): $0.71B (1.38% 24h)       <- hype-breakout (blofin; previously silently absent — fetchOpenInterest unsupported)
```

Honest absence held: `open_interest=None` as sole source → section `''`; unknown symbol
(`NOSUCHCOIN/USDT`) → `None`. The venue label prints the venues that actually responded;
the own-venue suffix suppresses itself when the execution venue reports nothing (both
current cases). Long/short ratio keeps its pre-existing execution-venue behavior.

## 3. Stage B — CVD via binance klines taker-volume

`fetch_cvd` orchestration (plan §4.2): binance `klines_taker` → trades snapshot on the
first resolving signal venue → execution venue (Wave-3 behavior) → `None`. Live, for
both strategies:

```
fetch_cvd('hyperliquid', 'BTC/USDT') -> method=klines_taker source=binance
ORDER FLOW (CVD):
CVD (1h window):      -$2,953,084
CVD (4h window):      +$11,517,604
CVD (full window):    +$11,517,604
CVD Trend:            flat
CVD/Price Divergence: none
Coverage:             full 4h window, 467862 trades (per-candle taker volume).
Source:               binance (klines taker-volume — largest single venue)

fetch_cvd('blofin', 'HYPE/USDT') -> method=klines_taker source=binance
CVD (1h window):      +$814,771
CVD (4h window):      +$785,340
Coverage:             full 4h window, 234130 trades (per-candle taker volume).
```

ai-btc previously had **no CVD at all** (hyperliquid's ccxt trades are user-fills-only);
it now reads a 467k-trade 4h window. The Wave-3 fallback survives intact and labeled:

```
_fetch_cvd_trades('blofin', 'BTC/USDT') -> method=trades_snapshot
Coverage:             100 trades spanning 5.3 min (single snapshot, one API call — short coverage is a data limit, not low activity).
Source:               blofin (trades snapshot — coverage-limited)
```

Honest absence: `fetch_cvd('hyperliquid', 'NOSUCHCOIN/USDT') -> None; render repr='' empty=True`.

## 4. Confirmations

- **No migration** — config surface is the single `SIGNAL_VENUES` env (plan §6).
- **No toggle changes** — `use_open_interest`/`use_cvd` were already enabled on the two
  live strategies (Wave 4); this phase changed input *quality* only. `use_liquidations`
  remains false everywhere; the field stays a documented no-op pending Phase 2.
- **No template edits** — source/coverage lines are renderer-additive.
- Deploys via `./scripts/redeploy.sh ai-signal-generator` after each stage; health
  `{"status":"ok","service":"ai-signal-generator"}` from inside the docker network.
- Per-cycle REST cost added: ~4 calls (3 venue OI + 1 binance klines; resolution cached
  ~1h) — well inside public limits.

## 5. Phase-2 checkpoint (decision pending)

Phase 2 (plan §3.2) is the websocket collector: `watchTrades` + `watchLiquidations`
accumulation into Redis for true multi-venue CVD and the only viable free liquidations
source. The checkpoint question the plan poses: **is Phase-1 CVD (binance-only,
majority-of-market, full windows) already sufficient, or is the collector worth its
complexity now?** Evidence to weigh after a few live cycles: whether CVD
trend/divergence readings materially drive gate decisions, and whether liquidations are
missed in practice (only `scalper` consumes them, and no scalper strategy exists yet).
