# order-executor: shared HTTP client per adapter (fixes health-check flap)

## Follow-up to: pending-order-live-mark-price-and-modified-time.md

That report flagged `order-executor` as `unhealthy` with what looked like a
runaway request loop. Investigation found that theory was wrong — request
volume was normal (~2-3 req/s combined from dashboard-api's 1s live-PnL
ticker × 2 accounts + the Positions page's 3s poll). The real cause:

## Root cause

Both `BlofinAdapter` and `HyperliquidAdapter` constructed a **brand-new
`httpx.AsyncClient()` on every single exchange call** (33 call sites total)
instead of reusing one pooled client. Every call paid a full TCP+TLS
handshake to blofin/hyperliquid before it could even send the request. Under
concurrent calls (multiple accounts/pollers overlapping), handshake latency
compounded until even the trivial, zero-I/O `/health` endpoint couldn't be
scheduled within its 5s Docker health-check timeout — producing the
`unhealthy` status, even though the container itself was not resource
starved (5.7% CPU, 71MB/2GB RAM at the time).

Confirmed directly: a manual call to
`/accounts/hyperliquid-hyperliquid-hqdy/mark-price/BTC-USDT` took 14-23s
(timing out) before the fix.

## Fix

- `order-executor/app/adapters/base.py`: added `ExchangeAdapter.close()`
  (no-op default, overridable).
- `order-executor/app/adapters/hyperliquid.py`: one `self._client =
  httpx.AsyncClient(timeout=10)` built in `__init__`, reused for all 15 call
  sites (write calls pass `timeout=15` explicitly, same as before). Added
  `close()` to close it.
- `order-executor/app/adapters/blofin.py`: same pattern — one
  `self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10)`
  reused across all 18 call sites. Added `close()`.
- `order-executor/app/registry.py`: `invalidate()` is now async and closes
  the evicted adapter's client (was leaking connections on credential
  rotation before this fix too). Added `close_all()` for shutdown.
- `order-executor/app/main.py`: `lifespan()` now calls
  `registry.close_all()` on shutdown; the `/accounts/{id}/invalidate` route
  awaits the now-async `invalidate()`.
- Updated 3 unit test files (`test_blofin_close.py`,
  `test_blofin_fill_size.py`, `test_hyperliquid_fill_size.py`) that
  previously patched `httpx.AsyncClient` at module level (relying on
  per-call construction) — now patch `adapter._client` directly via
  `patch.object(adapter, "_client", mock_client)`, since the client is built
  once in `__init__`.

## Verification

Unit tests (copied into the running container ahead of rebuild, to validate
before deploying):
```
10 passed in 24.40s
```

Redeployed via `./scripts/redeploy.sh order-executor`. Post-deploy:

```
$ docker compose ps order-executor
matp-order-executor-1   Up ... (healthy)

$ time docker compose exec dashboard-api wget -qO- --timeout=10 \
    "http://order-executor:8004/accounts/hyperliquid-hyperliquid-hqdy/mark-price/BTC-USDT"
{"symbol":"BTC-USDT","mark_price":63980.0}
real  0m1.727s      # was 14-23s (timing out) before the fix

$ time docker compose exec dashboard-api wget -qO- --timeout=10 \
    "http://order-executor:8004/accounts/blofin-blofin-demo-v5vr/positions"
[{"symbol":"SUI-USDT", ...}]
real  0m1.620s
```

Watched for 2+ minutes under normal polling load post-deploy:
```
$ docker inspect matp-order-executor-1 --format '{{.State.Health.Status}} failingStreak={{.State.Health.FailingStreak}}'
healthy failingStreak=0

$ docker compose logs dashboard-api --since 60s | grep -c "TimeoutError\|failed"
0
```

`[livePnl] tick:` log lines land ~1s apart with no overlap/pile-up, and zero
timeout errors over the observation window (previously every tick logged an
`executor failed for account ...: TimeoutError`).
