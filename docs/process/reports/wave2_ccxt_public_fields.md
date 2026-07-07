# Wave 2 Report — Three ccxt-Public AI Data Fields

**Date:** 2026-07-07
**Scope:** `ai-signal-generator` only, per the wave scope guard.
**Design authority:** `docs/design/ai_prompts/20_plumbing_specs.md` §2 (orderbook_depth),
§7 (funding_history), §1 (mtf_structure), §11 (cross-cutting rules);
`docs/design/ai_prompts/00_audit.md` §2 (delivered-field pattern).

Every claim below is backed by pasted command output from the live stack. All functional
verification was done by one-off invocations **inside the running container** against real
exchange data — **no strategy toggle was enabled at any point** (unlike Wave 1's live test,
which was reverted).

---

## 1. No migration needed

All three toggle columns (`use_orderbook`, `use_funding_history`, `use_mtf_structure`)
already exist from migration 045. No migration was added or edited this wave — the
diff-stat in §3 shows zero files under `db/migrations/`.

## 2. Three fields delivered, with render slots

| Field | Fetcher | State location | Renderer | Slot |
|---|---|---|---|---|
| `mtf_structure` | `app/data/mtf.py::fetch_mtf_structure(exchange_id, symbol, timeframes=['1h','4h','1d'])` — one `fetch_ohlcv` per fixed TF, local EMA-posture / slope / `_find_swings` classification | `mtf_structure: Optional[list]` | `_render_mtf_structure` → `MULTI-TIMEFRAME STRUCTURE:` | **2.1** (after Technical) |
| `orderbook_depth` | `app/data/orderbook.py::fetch_orderbook_depth(exchange_id, symbol, depth_pct=(1.0, 2.0))` — ccxt `fetch_order_book`, ±1%/±2% notional, imbalance, walls | `orderbook_data: Optional[dict]` | `_render_orderbook` → `ORDER BOOK:` | **2.7** (after Open Orders, before Sentiment) |
| `funding_history` | `app/data/funding.py::fetch_funding_history(exchange_id, symbol, days=30)` — ccxt `fetch_funding_rate_history`, percentile + same-sign streak, degrades to the exchange's default window | `sentiment_data['funding_history']` (no new top-level key) | `_render_funding_history` invoked **inside** `_render_sentiment` | **nested in section 3** (the deliberate exception per spec §7) |

Both async fetchers reuse the `sentiment.py` ccxt lifecycle exactly (class lookup →
`enableRateLimit` → `load_markets()` → `resolve_ccxt_symbol` → work → `close()` in
`finally`, `None` on error). All ingest wiring is try/except appending to
`data_fetch_errors` — never fatal. Ingest insertion points per the wave prompt:
`mtf_structure` after the closed-candles block and before sentiment; `funding_history` as
a fourth fetch inside the sentiment section after `open_interest`; `orderbook` after the
open-orders block. The sentiment-section gate in `build_prompt()` was extended to include
`use_funding_history` so the toggle isn't dead on a strategy with the fear-greed/funding/OI
trio off (same dead-switch reasoning as Wave 1's OHLCV-gate extension).

### 2.1 Code present in the built image (grep inside the running container)

Phase 1 (orderbook):

```
$ docker compose exec ai-signal-generator grep -n "_render_orderbook\|fetch_orderbook_depth" /app/app/prompt/builder.py /app/app/data/orderbook.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:160:def _render_orderbook(state: dict) -> str:
/app/app/prompt/builder.py:509:        ob = _render_orderbook(state)
/app/app/data/orderbook.py:25:async def fetch_orderbook_depth(
/app/app/data/orderbook.py:102:        logger.warning("fetch_orderbook_depth error [%s %s]: %s", exchange_id, symbol, exc)
/app/app/data/orderbook.py:119:        result = await fetch_orderbook_depth("binance", "BTC/USDT")
/app/app/graph/nodes/node_ingest.py:12:from app.data.orderbook import fetch_orderbook_depth
/app/app/graph/nodes/node_ingest.py:178:            orderbook_data = await fetch_orderbook_depth(exchange_id, ccxt_symbol)
```

Phase 2 (funding + mtf, after the second rebuild — builder line numbers shifted):

```
$ docker compose exec ai-signal-generator grep -n "_render_funding_history\|fetch_funding_history\|_render_mtf_structure\|fetch_mtf_structure" /app/app/prompt/builder.py /app/app/data/funding.py /app/app/data/mtf.py /app/app/graph/nodes/node_ingest.py
/app/app/prompt/builder.py:198:def _render_funding_history(sd: dict) -> list[str]:
/app/app/prompt/builder.py:233:        body += _render_funding_history(sd)
/app/app/prompt/builder.py:301:def _render_mtf_structure(state: dict) -> str:
/app/app/prompt/builder.py:517:        mtf = _render_mtf_structure(state)
/app/app/data/funding.py:30:async def fetch_funding_history(
/app/app/data/funding.py:85:        logger.warning("fetch_funding_history error [%s %s]: %s", exchange_id, symbol, exc)
/app/app/data/funding.py:102:        result = await fetch_funding_history("binance", "BTC/USDT")
/app/app/data/mtf.py:101:async def fetch_mtf_structure(
/app/app/data/mtf.py:138:        result = await fetch_mtf_structure("binance", "BTC/USDT")
/app/app/graph/nodes/node_ingest.py:8:from app.data.funding import fetch_funding_history
/app/app/graph/nodes/node_ingest.py:11:from app.data.mtf import fetch_mtf_structure
/app/app/graph/nodes/node_ingest.py:115:            mtf_structure = await fetch_mtf_structure(exchange_id, ccxt_symbol)
/app/app/graph/nodes/node_ingest.py:148:            funding_history = await fetch_funding_history(exchange_id, ccxt_symbol)
```

### 2.2 Functional proof on real exchange data (hyperliquid BTC/USDT, in-container)

Order book (Phase 1):

```
fetch_orderbook_depth('hyperliquid', 'BTC/USDT') -> {'bid_depth_1pct_usd': 10366251.94, 'ask_depth_1pct_usd': 790677.38, 'bid_depth_2pct_usd': 10366251.94, 'ask_depth_2pct_usd': 790677.38, 'depth_imbalance_ratio': 13.111, 'largest_bid_wall': {'price': 64339.0, 'size_usd': 3265926.78}, 'largest_ask_wall': {'price': 64357.0, 'size_usd': 240893.4}}

ORDER BOOK:
Bid Depth (±1% / ±2%):  $10,366,252 / $10,366,252
Ask Depth (±1% / ±2%):  $790,677 / $790,677
Depth Imbalance (1% bid/ask): 13.111 (bids heavier)
Largest Bid Wall:       $3,265,927 @ 64339.0
Largest Ask Wall:       $240,893 @ 64357.0
Note: snapshot at analysis time — resting walls can be pulled; treat as corroboration only.
```

(Reviewed observation: on hyperliquid the ±1% and ±2% figures coincide because its ccxt
book snapshot only returns near-mid levels — a per-exchange data-depth property, not a
computation bug.)

Funding history — the extra lines appear **within** the `SENTIMENT:` section, under the
existing `Funding Rate:` line:

```
fetch_funding_history('hyperliquid', 'BTC/USDT') -> {'funding_percentile': 15.4, 'funding_streak': 1, 'streak_direction': 'negative'}

SENTIMENT:
Funding Rate:         1.25e-05% (neutral)
Funding Percentile:   15.4 (vs trailing 30d window)
Funding Streak:       1 consecutive negative settlements
```

Multi-timeframe structure:

```
fetch_mtf_structure('hyperliquid', 'BTC/USDT') -> [{'tf': '1h', 'trend_direction': 'sideways', 'ema_posture': 'price above EMA50, EMA50 above EMA200', 'swing_structure': 'mixed'}, {'tf': '4h', 'trend_direction': 'uptrend', 'ema_posture': 'price above EMA50, EMA50 below EMA200', 'swing_structure': 'HH/HL'}, {'tf': '1d', 'trend_direction': 'downtrend', 'ema_posture': 'price below EMA50, EMA50 below EMA200', 'swing_structure': 'LH/LL'}]

MULTI-TIMEFRAME STRUCTURE:
  1h: sideways  — price above EMA50, EMA50 above EMA200; swings mixed
  4h: uptrend   — price above EMA50, EMA50 below EMA200; swings HH/HL
  1d: downtrend — price below EMA50, EMA50 below EMA200; swings LH/LL
```

### 2.3 Honest-absence proof

```
honest absence (orderbook_data=None): repr=''  empty=True
honest absence (mtf_structure=None): repr=''  empty=True
honest absence (funding_history=None, sole source): repr=''  empty=True
honest absence (funding_history=None, funding_rate on): extra lines added=False
SENTIMENT:
Funding Rate:         1.25e-05% (neutral)
```

(The last check proves the nested renderer adds nothing when its data is missing while the
rest of the SENTIMENT section still renders normally.)

### 2.4 Deploy verification

Rebuilt + force-recreated via `./scripts/redeploy.sh ai-signal-generator` after each phase
(never `docker compose restart`). Post-deploy health from inside the docker network, both
phases:

```
$ docker compose exec nginx wget -qO- http://ai-signal-generator:8005/health
{"status":"ok","service":"ai-signal-generator"}
```

---

## 3. Untouched surfaces confirmed

Diff-stat of the wave's two commits — only `ai-signal-generator/` was modified; no file in
`db/migrations/`, `order-executor`, `order-listener`, `dashboard-api`, `dashboard-ui`, or
`strategy-tester` appears:

```
$ git diff --stat HEAD~2..HEAD
 ai-signal-generator/app/data/funding.py            | 105 +++++++++++++++
 ai-signal-generator/app/data/mtf.py                | 141 +++++++++++++++++++++
 ai-signal-generator/app/data/orderbook.py          | 122 ++++++++++++++++++
 ai-signal-generator/app/graph/nodes/node_ingest.py |  39 +++++-
 ai-signal-generator/app/graph/state.py             |   2 +
 ai-signal-generator/app/prompt/builder.py          |  93 +++++++++++++-
 6 files changed, 498 insertions(+), 4 deletions(-)
```

`ai_prompt_templates` (prompt template text) was not edited — template cutover remains
Wave 4. No strategy toggle was enabled at any point this wave; all 8 toggles from
migration 045 remain false on every strategy:

```
$ docker compose exec postgres psql -U matp -d matp -c "SELECT count(*) AS strategies_with_new_toggles_on FROM ai_strategy_config WHERE use_mtf_structure OR use_orderbook OR use_volume_profile OR use_cvd OR use_momentum_divergence OR use_volatility_regime OR use_funding_history OR use_liquidations;"
 strategies_with_new_toggles_on
--------------------------------
                              0
(1 row)
```

Dashboard toggle exposure and tester schema parity remain deferred to their existing
ROADMAP items.

---

## 4. Final section ordering in `build_prompt()`

From the deployed image:

```
$ docker compose exec ai-signal-generator grep -n "^    # [0-9]" /app/app/prompt/builder.py
508:    # 1. Header — always included; contains position warning if position_open
511:    # 2. Technical — only if toggled on and OHLCV data is available
515:    # 2.1. Multi-timeframe structure — immediately after Technical
521:    # 2.2. Volatility regime — right after Technical
527:    # 2.3. Momentum divergence
533:    # 2.4. Volume profile — just before Geometry, so boundary/HVN confluence reads adjacently
539:    # 2.5. Geometry — only if toggled on and geometry data is available
545:    # 2.6. Open orders — only if toggled on (geometry gates the range-working actions)
551:    # 2.7. Order book — only if toggled on and snapshot is available
557:    # 3. Sentiment — only if at least one sentiment source is toggled on
566:    # 4. News — only if toggled on and digest is available
570:    # 5. Macro — only if at least one macro source is toggled on
576:    # 6. Portfolio — always included
579:    # 7. Data warnings — inserted between portfolio and instructions if errors occurred
585:    # 8. Strategy instructions — from DB template, always included
593:    # 9. Task section — always included
```

Remaining open slots for later waves: 2.8 (cvd), 2.9 (liquidations), 4.5 (economic
calendar). Funding history has no slot number — it renders inside section 3 by design.

---

## 5. Standing constraints — how they were met

- **ccxt lifecycle:** both async fetchers copy `sentiment.py` exactly, `close()` in `finally`.
- **Non-fatal fetchers:** all three wrapped in try/except appending to `data_fetch_errors`.
- **Honest absence:** renderers return `''` / add no lines on missing data (§2.3).
- **No confidence scale / task text restated:** `_render_task` remains the sole source.
- **Toggles inert:** verified false on every strategy (§3); functional checks ran in-container only.
- **Image verification:** grep inside the running container after each `--no-cache`-equivalent
  redeploy (`./scripts/redeploy.sh` = build + `up -d --force-recreate`), never `restart`.
- **Fixed MTF timeframes:** `['1h','4h','1d']` per spec §1; the config-column idea stays
  deferred to ROADMAP Open Question #4.
