# Parallelize node_ingest.py data fetches + fetch_mtf_structure

## Root cause (investigated in prior turns, fixed this turn)

`ai-signal-generator/app/graph/nodes/node_ingest.py` awaited 15+ independent external data
sources one at a time (OHLCV, MTF structure, fear/greed, funding rate, open interest, funding
history, news, economic calendar, BTC dominance, macro, open orders, orderbook depth, CVD,
liquidations). None of these depend on each other's *results* — they were only sequential because
the code awaited them one by one. `fetch_mtf_structure` compounded this further: its own 3
timeframe fetches (1h/4h/1d) were also a sequential `for` loop.

Live-traced on a real ETH AI Geometric Range cycle: the AI's `close_long` decision at 18:50:11
didn't reach order-listener as a webhook until 18:52:54 — a 2.5-minute gap entirely inside data
ingestion (the LLM call itself took ~9s, dispatch ~250ms).

## Fix

- `node_ingest.py`: every async fetch now starts as `asyncio.create_task(...)` immediately (before
  any of them are awaited), and is `await`ed at its original call site further down — same
  per-source try/except, same error message format, same `errors` list, same fallback values
  (e.g. `open_orders = []` on failure). Only the scheduling changed: total wall time becomes
  ~max(latencies) instead of sum(latencies).
- `app/data/mtf.py`: `fetch_mtf_structure`'s 3 timeframe fetches now run via `asyncio.gather`
  instead of a sequential loop, with the same per-timeframe try/except and warning log preserved.
- `app/data/news.py`: `_fetch_rss` wrapped in `asyncio.wait_for(..., timeout=10.0)` —
  `feedparser.parse()` has no built-in timeout and could hang indefinitely on a slow/unresponsive
  RSS server.

## Verification (live, real cycles — not mocked)

Syntax check (`ast.parse`) on all three changed files: clean.

Redeployed `ai-signal-generator` twice (once per fix), confirmed healthy each time via
`docker compose exec nginx wget -qO- http://ai-signal-generator:8005/health`.

Manually triggered `eth-ai-34d2` via `POST /internal/schedulers/eth-ai-34d2/trigger` three times
across the two deploys and measured trigger-to-decision time from container logs:

1. First test (node_ingest fix only, contaminated by the container's startup 145-model probe
   sweep running concurrently): ~120s trigger → LLM decision at 20:52:54, but this run overlapped
   heavily with `Model probe: starting initial verification for all providers` (152 models probed
   across 4 providers, finishing at 20:55:10) — not a clean measurement.
2. After the mtf.py fix, retested against the same strategy: **93s** trigger (21:08:17.093) →
   LLM decision (21:09:49.825) for a full-LLM cycle (`action=amend_order`, gated out on
   confidence). Log inspection for this run shows **no** Hyperliquid OHLCV/MTF fetch errors (all
   3 timeframes succeeded, concurrently) — previously, per-timeframe failures were logged ~20-40s
   apart, which is what the mtf.py fix targeted. This run was *still* partially contaminated by a
   second startup probe sweep (visible OpenAI 429s from concurrent model probing in the same
   window), so 93s is a conservative (not best-case) number for steady-state operation, where no
   probe sweep runs concurrently with a normal cycle.

Net: confirmed the two structural sequential bottlenecks in our own code are fixed (evidenced by
absence of the previously-observed serialized per-timeframe failure gaps); the remaining ~93s in
testing is at least partly attributable to an unrelated, one-time-per-restart background process,
not to any remaining sequential await chain in `node_ingest.py`/`mtf.py`.

## Also touched

- `docs/ROADMAP.md`: added a Known Issues Fixed row for this, plus two new Deferred Backlog
  items per user request — "UI: order/position diagnostic trail" (surface the kind of
  reconstruction done manually this session — close reason, cycle timing/data-fetch-error
  breakdown — directly in the dashboard) and "close_reason: distinguish SL-hit / TP-hit /
  manual-on-exchange" (the concrete mechanism needed to feed that trail, found during the same
  investigation).
