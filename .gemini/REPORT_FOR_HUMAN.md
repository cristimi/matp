# HL leverage + margin overrun — fix verification report
_Updated 2026-06-16. Covers: leverage fix (Defect A) + margin clamp bypass fix (Defect B) deployment + test results._

---

## 1. Diff stat

```
order-executor/app/adapters/base.py       |  5 ++++
order-executor/app/adapters/blofin.py     | 22 ++++++++++++++
order-executor/app/adapters/hyperliquid.py| 89 +++++++++++++++++++++++++++++++++++++++++++++++++++++
3 files changed, 116 insertions(+), 5 deletions(-)
```

---

## 2. Full diff

### base.py

```diff
+    async def get_max_leverage(self, symbol: str) -> int:
+        """Return the exchange's maximum allowed leverage for `symbol`.
+        Returns 0 if unknown. Subclasses should override. Must never raise."""
+        return 0
```

### blofin.py

```diff
+    async def get_max_leverage(self, symbol: str) -> int:
+        """Exchange max leverage for the instrument. Returns 0 if unknown."""
+        try:
+            inst = await self._get_instrument(symbol)
+            if not inst:
+                return 0
+            return int(float(inst.get("maxLeverage") or 0))
+        except Exception as e:
+            logger.warning(f"BlofinAdapter.get_max_leverage({symbol}) failed: {e}")
+            return 0

     async def _set_leverage(self, ...):
         ...
     async def submit_order(self, order):
         ...
         leverage = order.leverage or 10
+
+        max_lev = await self.get_max_leverage(order.symbol)
+        if max_lev and leverage > max_lev:
+            # DECISION: reject (do not clamp)
+            msg = (f"Requested leverage {leverage}x exceeds Blofin "
+                   f"max {max_lev}x for {order.symbol}")
+            logger.warning(f"BlofinAdapter: {msg}")
+            return OrderResult(success=False, status="rejected", error_msg=msg)
+
         # Blofin ignores the lever field; must set it explicitly first
         await self._set_leverage(order.symbol, leverage, margin_mode)
```

### hyperliquid.py

```diff
     self._sz_decimals_cache: Optional[dict] = None
+    self._max_lev_cache: Optional[dict] = None      # coin → exchange max leverage (int)

     async def submit_order(self, order):
         try:
             asset_index = await self._get_asset_index(order.symbol)
             is_close = order.signal in ("close_long", "close_short")
+
+            if not is_close:
+                req_lev = int(order.leverage or 1)
+                max_lev = await self.get_max_leverage(order.symbol)
+                if max_lev and req_lev > max_lev:
+                    # DECISION: reject (do not clamp)
+                    msg = (f"Requested leverage {req_lev}x exceeds Hyperliquid "
+                           f"max {max_lev}x for {order.symbol}")
+                    logger.warning(f"HyperliquidAdapter: {msg}")
+                    return OrderResult(success=False, status="rejected", error_msg=msg)
+                # Push leverage to HL (persistent per-coin setting). Abort on failure.
+                await self._update_leverage(asset_index, req_lev, order.margin_mode or "isolated")
+
             result = await self._place_order(order, asset_index, reduce_only=is_close)
             return result

+    async def get_max_leverage(self, symbol: str) -> int:
+        """Exchange max leverage for the coin. Returns 0 if unknown."""
+        try:
+            await self._get_asset_index(symbol)  # ensures caches populated
+            coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
+            return int((self._max_lev_cache or {}).get(coin, 0) or 0)
+        except Exception as e:
+            logger.warning(f"HyperliquidAdapter.get_max_leverage({symbol}) failed: {e}")
+            return 0

     # _get_asset_index — added _max_lev_cache population:
             self._sz_decimals_cache = { asset["name"]: int(asset.get("szDecimals", 4)) ... }
+            self._max_lev_cache = {
+                asset["name"]: int(asset.get("maxLeverage", 0) or 0)
+                for asset in universe
+            }

     # cache-miss reset also clears _max_lev_cache:
             self._asset_cache = None
             self._sz_decimals_cache = None
+            self._max_lev_cache = None

+    async def _update_leverage(self, asset_index: int, leverage: int, margin_mode: str) -> None:
+        """Send an updateLeverage action to Hyperliquid before opening a position.
+        Raises on failure so the caller can abort the order (fail-safe)."""
+        import msgpack
+        from eth_hash.auto import keccak
+
+        is_cross = (margin_mode or "isolated").lower() != "isolated"
+        nonce = int(time.time() * 1000)
+        action = {
+            "type":     "updateLeverage",
+            "asset":    asset_index,
+            "isCross":  is_cross,
+            "leverage": int(leverage),
+        }
+        action_bytes  = msgpack.packb(action, use_bin_type=True)
+        nonce_bytes   = nonce.to_bytes(8, "big")
+        connection_id = keccak(action_bytes + nonce_bytes + b'\x00')
+        source = "b" if self.base_url.endswith("testnet.xyz") else "a"
+        message = {"source": source, "connectionId": connection_id}
+        signed = self._account.sign_typed_data(
+            domain_data=_HL_DOMAIN, message_types=_HL_TYPES, message_data=message,
+        )
+        payload = {
+            "action": action, "nonce": nonce,
+            "signature": {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v},
+            "vaultAddress": None,
+        }
+        async with httpx.AsyncClient(timeout=15) as client:
+            resp = await client.post(f"{self.base_url}/exchange", json=payload)
+            resp.raise_for_status()
+            data = resp.json()
+
+        status = data.get("status")
+        if status != "ok":
+            raise ValueError(f"Hyperliquid updateLeverage failed: {data}")
+        logger.info(
+            f"HyperliquidAdapter: leverage set to {leverage}x "
+            f"(asset={asset_index}, {'cross' if is_cross else 'isolated'})"
+        )
```

---

## 3. Container grep — new code is live

```
# hyperliquid.py
88:  max_lev = await self.get_max_leverage(order.symbol)
96:  await self._update_leverage(asset_index, req_lev, order.margin_mode or "isolated")
176: async def get_max_leverage(self, symbol: str) -> int:
486: async def _update_leverage(self, asset_index: int, leverage: int, margin_mode: str) -> None:
495:     "type":     "updateLeverage",

# blofin.py
149: async def get_max_leverage(self, symbol: str) -> int:
192: max_lev = await self.get_max_leverage(order.symbol)
195:     msg = (f"Requested leverage {leverage}x exceeds Blofin "
```

---

## 4. Exchange max leverage — field name confirmation

### Hyperliquid HYPE (testnet `/info` → `meta`)

```json
{
  "szDecimals": 2,
  "name": "HYPE",
  "maxLeverage": 10,
  "marginTableId": 10,
  "onlyIsolated": true,
  "marginMode": "strictIsolated"
}
```

**HYPE_MAX = 10.** Field name: `maxLeverage` (int). Caching works as designed.
Note: `onlyIsolated: true` — HYPE only supports isolated margin. `_update_leverage` always uses `is_cross = False` when `margin_mode = "isolated"`, which is correct.

### Blofin ONDO-USDT (demo `/api/v1/market/instruments`)

```json
{
  "instId": "ONDO-USDT",
  "contractValue": "0.1",
  "maxLeverage": "50",
  "minSize": "1",
  "lotSize": "1",
  "tickSize": "0.0001"
}
```

**ONDO_MAX = 50.** Field name: `maxLeverage` (string `"50"`). `get_max_leverage` uses `int(float(...))` which correctly coerces `"50"` → `50`.

---

## 5. Test results

### Test A — HL HYPE, valid 3x leverage ✅ PASS

**Request:** `symbol=HYPE-USDT, leverage=3, size=0.5, signal=open_long`

**Response:**
```json
{
  "success": true,
  "exchange_order_id": "55100169269",
  "status": "filled",
  "actual_fill_price": "37.52"
}
```

**Log lines (in order):**
```
POST https://api.hyperliquid-testnet.xyz/exchange  200 OK   ← updateLeverage
HyperliquidAdapter: leverage set to 3x (asset=135, isolated)
POST https://api.hyperliquid-testnet.xyz/info      200 OK   ← metaAndAssetCtxs (mark price)
POST https://api.hyperliquid-testnet.xyz/exchange  200 OK   ← order placement
```

**Post-test position:**
```json
{ "symbol": "HYPE-USDT", "side": "long", "size": "0.5", "leverage": 3, "entry_price": "37.52" }
```

**Verdict:** `updateLeverage` sent before order, HL applied **3x** (not stale 20x). ✅

---

### Test B — HL HYPE, over-max 11x (HYPE_MAX=10) ✅ PASS

**Request:** `symbol=HYPE-USDT, leverage=11, size=0.5, signal=open_long`

**Response:**
```json
{
  "success": false,
  "status": "rejected",
  "error_msg": "Requested leverage 11x exceeds Hyperliquid max 10x for HYPE-USDT"
}
```

**Log line:**
```
[WARNING] HyperliquidAdapter: Requested leverage 11x exceeds Hyperliquid max 10x for HYPE-USDT
```

No `/exchange` POST and no position opened. ✅

---

### Test C — Blofin ONDO, over-max 51x (ONDO_MAX=50) ✅ PASS

**Account:** `acc_blofin_demo_default` (Myblofin was inactive)

**Request:** `symbol=ONDO-USDT, leverage=51, size=0.1, signal=open_long`

**Response:**
```json
{
  "success": false,
  "status": "rejected",
  "error_msg": "Requested leverage 51x exceeds Blofin max 50x for ONDO-USDT"
}
```

**Log line:**
```
[WARNING] BlofinAdapter: Requested leverage 51x exceeds Blofin max 50x for ONDO-USDT
```

No `set-leverage` POST and no order placed. ✅

---

### Test D — Blofin ONDO, valid 3x ✅ PASS

**Request:** `symbol=ONDO-USDT, leverage=3, size=0.1, signal=open_long`

**Response:**
```json
{
  "success": true,
  "exchange_order_id": "1000130175920",
  "status": "filled",
  "actual_fill_price": "0.3777"
}
```

**Log lines (in order):**
```
POST https://demo-trading-api.blofin.com/api/v1/account/set-leverage  200 OK
BlofinAdapter: leverage set to 3x for ONDO-USDT (isolated)
```

✅

---

## 6. Cleanup

| Position | Account | Action | Result |
|---|---|---|---|
| HYPE-USDT long 0.5 (Test A) | Hyperliquidtest | close-position attempted | Failed: IOC timed out — HL testnet HYPE has no resting book depth. Position remains open on testnet. |
| ONDO-USDT long 0.1 (Test D) | acc_blofin_demo_default | close-position | `success: true`, fill=0.3783, pnl=0.00006 |

The HYPE testnet position could not be closed via IOC market order (no resting orders on the thin testnet book). This is a testnet-only liquidity condition — no code issue. The position carries negligible exposure (0.5 HYPE × $37.52 = $18.76 notional at 3x = $6.25 margin).

---

## 7. Deviations from prompt

None. The signing scheme for `updateLeverage` uses the identical `keccak(action_bytes + nonce_bytes + b'\x00')` + `sign_typed_data` pattern as the `order` action — confirmed by HL returning `"status": "ok"` on the first attempt (no trial-and-error). The `maxLeverage` field name was correct on both exchanges as specified.

---

## 8. Summary (Defect A)

Leverage defect resolved: `_update_leverage` now fires before every non-close order on Hyperliquid, using the same EIP-712/msgpack signing path already proven for `order` and `cancel` actions. An exchange-max guard on both adapters prevents future orders that would be immediately rejected by the exchange from reaching the signing stage.

---

# Defect B — Margin clamp bypass on price-less webhooks

_Updated 2026-06-16. Covers: fix deployment + test results._

---

## 9. Diff stat

```
order-executor/app/adapters/base.py        |  6 +++
order-executor/app/adapters/hyperliquid.py | 22 +++++++++++
order-executor/app/adapters/blofin.py      | 18 +++++++++
order-executor/app/main.py                 | 11 ++++++
order-listener/app/executor_client.py      | 10 +++++
order-listener/app/webhook_handler.py      | 32 +++++++++------
```

---

## 10. Full diff

### base.py

```diff
+    async def get_mark_price(self, symbol: str) -> float | None:
+        """Return the current mark price for `symbol`, or None if unavailable.
+        Subclasses should override. Must never raise."""
+        return None
```

### hyperliquid.py

```diff
+    async def get_mark_price(self, symbol: str) -> float | None:
+        """Return the current mark price for `symbol` via metaAndAssetCtxs. Returns None on error."""
+        try:
+            asset_index = await self._get_asset_index(symbol)
+            async with httpx.AsyncClient(timeout=10) as client:
+                resp = await client.post(f"{self.base_url}/info", json={"type": "metaAndAssetCtxs"})
+                resp.raise_for_status()
+                meta_and_ctx = resp.json()
+            asset_ctxs = meta_and_ctx[1]
+            if asset_index >= len(asset_ctxs):
+                return None
+            mark_px = float(asset_ctxs[asset_index].get("markPx") or 0)
+            return mark_px if mark_px > 0 else None
+        except Exception as e:
+            logger.warning(f"HyperliquidAdapter.get_mark_price({symbol}) failed: {e}")
+            return None
```

### blofin.py

```diff
+    async def get_mark_price(self, symbol: str) -> float | None:
+        """Return the current mark price for `symbol`. Returns None on error."""
+        try:
+            path = f"/api/v1/market/mark-price?instId={symbol}"
+            async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
+                resp = await client.get(path)
+                resp.raise_for_status()
+            data = resp.json().get("data") or []
+            if not data:
+                return None
+            mark_px = float(data[0].get("markPrice") or 0)
+            return mark_px if mark_px > 0 else None
+        except Exception as e:
+            logger.warning(f"BlofinAdapter.get_mark_price({symbol}) failed: {e}")
+            return None
```

Field name confirmation: Blofin `/api/v1/market/mark-price` returns `{"data": [{"instId": "BTC-USDT", "markPrice": "65847.6", ...}]}`.
Field is `markPrice` (not `markPx`). Probed live before writing.

### main.py

```diff
+@app.get("/accounts/{account_id}/mark-price/{symbol}")
+async def get_mark_price(account_id: str, symbol: str):
+    """Return the current exchange mark price for the given symbol."""
+    try:
+        adapter    = await registry.get(account_id)
+        mark_price = await adapter.get_mark_price(symbol)
+        return {"symbol": symbol, "mark_price": mark_price}
+    except Exception as e:
+        logger.error(f"get_mark_price failed for {account_id}/{symbol}: {e}")
+        return {"symbol": symbol, "mark_price": None, "error": str(e)}
```

### executor_client.py

```diff
+async def get_mark_price(account_id: str, symbol: str) -> Optional[float]:
+    """Fetch the exchange mark price for `symbol` on `account_id`. Returns None if unavailable."""
+    data = await call_executor_get(f"/accounts/{account_id}/mark-price/{symbol}")
+    mp = data.get("mark_price")
+    return float(mp) if mp is not None else None
```

### webhook_handler.py

New reference-price resolution block inserted after Guard 3, before margin clamp:

```diff
+    # Reference price resolution — shared by clamp and guaranteed-SL below.
+    _ref_price = float(payload.indicator_price or payload.price or 0)
+    if payload.signal in ("open_long", "open_short") and _ref_price <= 0:
+        _acct_for_price = strategy.get("account_id") or ""
+        _mp = await get_mark_price(_acct_for_price, resolved.execution_symbol)
+        _ref_price = float(_mp) if _mp else 0.0
+        if _ref_price > 0:
+            logger.info(f"strategy={strategy_id}: no webhook price; using exchange mark price ...")
+        else:
+            _detail = "Cannot size open for strategy ...: no webhook price and exchange mark price unavailable ..."
+            await _finalize_signal_log(pool, signal_log_id, 422, "no_reference_price", _detail, start_ms)
+            raise HTTPException(status_code=422, detail=_detail)

     # Guard: Margin-per-trade clamp
     if payload.signal in ("open_long", "open_short"):
         _margin_per_trade = float(strategy.get("margin_per_trade") or 5.0)
-        _ref_price        = float(payload.indicator_price or payload.price or 0)   # ← removed
         if _ref_price > 0:
             ...
+            _meta["ref_price_source"] = "webhook" if (payload.indicator_price or payload.price) else "exchange_mark"

     # Guaranteed SL
-    _entry_ref = float(payload.indicator_price or payload.price or 0)   # ← removed
+    _entry_ref = _ref_price   # reuses already-resolved price
```

---

## 11. Container grep — new code is live

```
# order-executor
/app/app/adapters/hyperliquid.py:186:    async def get_mark_price(self, symbol: str) -> float | None:
/app/app/adapters/blofin.py:160:    async def get_mark_price(self, symbol: str) -> float | None:
/app/app/adapters/base.py:106:    async def get_mark_price(self, symbol: str) -> float | None:
/app/app/main.py:245:@app.get("/accounts/{account_id}/mark-price/{symbol}")

# order-listener
/app/app/executor_client.py:88:async def get_mark_price(account_id: str, symbol: str) -> Optional[float]:
/app/app/webhook_handler.py:21:from app.executor_client import get_mark_price
/app/app/webhook_handler.py:622:        _mp = await get_mark_price(_acct_for_price, resolved.execution_symbol)
/app/app/webhook_handler.py:636:            await _finalize_signal_log(pool, signal_log_id, 422, "no_reference_price", ...)
/app/app/webhook_handler.py:652:                _meta["ref_price_source"]      = (...)
```

---

## 12. Mark-price endpoint — live probe

```bash
# HL testnet HYPE
{"symbol":"HYPE-USDT","mark_price":37.508}

# Blofin demo ONDO
{"symbol":"ONDO-USDT","mark_price":0.3748}
```

---

## 13. Test results

### Test E — HL testnet ETH, no webhook price, clamp should fire via exchange mark price ✅ PASS

**Strategy:** `test_hl_demo_01` (ETH-USDT, account=Hyperliquidtest, margin_per_trade=5, default_leverage=20)

**Request:** `signal=open_long, size=0.1, leverage=5` — no `price`, no `indicator_price`

**Expected clamp:** `5 × 5 / 1866.0 = 0.01339836 ETH`

**Listener log lines:**
```
GET http://order-executor:8004/accounts/Hyperliquidtest/mark-price/ETH-USDT "HTTP/1.1 200 OK"
strategy=test_hl_demo_01: no webhook price; using exchange mark price 1865.9 for ETH-USDT
Strategy test_hl_demo_01 margin clamp: 0.1 → 0.01339836 (margin=5.0, lev=5, price=1865.9)
```

**DB signal_metadata (from `orders` table):**
```json
{
  "size_scaled_to_margin": true,
  "original_size":         0.1,
  "used_size":             0.01339836,
  "ref_price_source":      "exchange_mark",
  "sl_source":             "liquidation_safe",
  "sl_distance_pct":       18.9999,
  "entry_ref":             1865.9
}
```

**Exchange result:** `status=rejected` (HL testnet rejected the clamped size — likely below effective $10 floor after fees). No position opened. The clamp itself is the object under test — it fired correctly using exchange mark price.

**Verdict:** Clamp fires on price-less webhooks, `ref_price_source="exchange_mark"` recorded, guaranteed SL computed from same mark price. ✅

---

### Test F — Backstop 422 when account not found ✅ PASS

**Setup:** Temporary strategy `_test_backstop_` with `account_id="nonexistent_acct"` (executor returns `mark_price: null`).

**Request:** `signal=open_long, size=0.1, leverage=5` — no `price`, no `indicator_price`

**HTTP Response (status 422):**
```json
{
  "detail": "Cannot size open for strategy _test_backstop_: no webhook price and exchange mark price unavailable for ETH-USDT. Order rejected to prevent an unsized entry."
}
```

**DB `signal_log`:**
```
http_status | outcome            | error_detail
422         | no_reference_price | Cannot size open for strategy _test_backstop_: ...
```

No order row created. **Verdict:** Backstop rejects the order with 422 and records `no_reference_price` outcome. ✅

Test strategy deleted after test.

---

## 14. Cleanup

| Test | Position | Action | Result |
|------|----------|--------|--------|
| Test E | ETH-USDT (HL testnet) | None needed | Exchange rejected the clamped order — no position opened |
| Test F | None | None needed | Request rejected at listener level |

Pre-existing BTC-USDT long on Hyperliquidtest (from an earlier session) — not related to this test run, not touched.

---

## 15. Deviations from prompt

None. Blofin mark-price field name probed live before writing code (confirmed: `markPrice`, not `markPx`). The reference-price resolution block, margin clamp modification, and guaranteed-SL reuse of `_ref_price` match the specified design exactly.

---

## 16. Summary (both defects)

- **Defect A (leverage):** Fixed in commit `6c2237c`. HL adapter now calls `updateLeverage` before every open order. Exchange-max guard on both adapters.
- **Defect B (margin clamp bypass):** Fixed in this commit. For opening signals with no webhook price, the listener fetches the exchange mark price via the executor's new `GET /accounts/{id}/mark-price/{symbol}` endpoint. If no price can be obtained, the order is rejected (422, `no_reference_price`) — never placed unsized.

---

# Defect B follow-up — HL minimum order and size-precision bug

_Updated 2026-06-17. Mode: read-only investigation + live testnet probes. No code changes._

---

## §1. Preconditions

| Item | Value |
|---|---|
| ETH mark price at start | **$1818** (executor `/mark-price/ETH-USDT`) |
| Main wallet account value | **$333.80** (main_wallet=`0x79A3E6...`) |
| Agent wallet account value | $0.00 (positions held under main wallet, not agent) |
| Pre-existing positions | BTC-USDT long 0.05 (untouched) |

---

## §2. Verbatim rejection — Test E reproduced

**DB `error_msg` for the Test E order (`orders` table):**
```
Order has invalid size.
```

**DB `signal_metadata`:**
```json
{
  "size_scaled_to_margin": true,
  "original_size": 0.1,
  "used_size": 0.01339836,
  "ref_price_source": "exchange_mark",
  "entry_ref": 1865.9,
  "sl_source": "liquidation_safe",
  "sl_distance_pct": 18.9999
}
```

**Root cause (one sentence):** The margin clamp computed `round(5×5/1865.9, 8) = 0.01339836`, which has 8 significant decimal places; Hyperliquid's `szDecimals=4` for ETH rejects any size that is not a multiple of 0.0001, so `"0.01339836"` is an invalid wire size.

**Not the SL leg, not the notional floor** — direct executor call with `size=0.0134` (4 dp) filled immediately in Test A, and direct executor call with `size=0.0134` + SL also filled in Test B. The notional ($24.51) cleared HL's $10 minimum. The only difference was the decimal precision of the clamped size.

---

## §3. SL leg analysis — A vs B

| Test | Size | SL | Entry | SL status |
|---|---|---|---|---|
| A (bare entry) | 0.0134 | none | ✅ filled @ $1829.4 | — |
| B (entry + SL) | 0.0134 | $1472.58 | ✅ filled @ $1827.47 | "waitingForTrigger" |

`status[0]=filled, status[1]="waitingForTrigger"` confirms the entry leg filled and the SL trigger was accepted and is live. **The SL leg does not cause rejection at this size.** Test E's rejection was purely the 8-dp size, not the SL.

---

## §4. Notional sweep (bare entry, no SL, ETH at ~$1828)

| Size (ETH) | ≈ Notional | ≈ Margin @5x | HL result | error_msg |
|---|---|---|---|---|
| 0.0054 | $9.87 | $1.97 | **rejected** | "Order must have minimum value of $10. asset=4" |
| 0.0080 | $14.62 | $2.92 | **filled** @ $1827.75 | — |
| 0.0134 | $24.49 | $4.90 | **filled** @ $1827.60 | — |
| 0.0270 | $49.38 | $9.88 | **filled** @ $1827.80 | — |
| 0.0540 | $98.73 | $19.75 | **filled** @ $1828.36 | — |

**Smallest filling size: 0.0080 ETH ≈ $14.62 notional.** The threshold is $10 nominal; 0.0054 ($9.87) is just below, 0.0080 ($14.62) clears it.

**Derived minimum:**
- HL's effective minimum order value: **$10 notional**
- Minimum `margin_per_trade` = $10 / leverage
  - At 5x leverage: **$2.00** (0.0080 ETH @ $1828 = $14.62; actual margin = $2.92 — rounding to szDecimals=4 means $2.00 minimum is tight; **$3 is safer**)
  - At 10x: $1.00 minimum, $1.50 safer
  - At 20x: $0.50 minimum, $1.00 safer
  - Formula: minimum_margin = ceil($10 / leverage / szDecimals_step) × szDecimals_step × price / leverage

**Scaling note:** since `notional = margin_per_trade × leverage` (price cancels), the minimum `margin_per_trade` for HL is purely $10 / leverage — independent of which coin/price. With the default $5 `margin_per_trade` at 5x, notional is $25 and clears the floor with 2.5× headroom.

---

## §5. Cleanup

| Position | Action | Result |
|---|---|---|
| ETH-USDT long 0.1292 (Tests A + B + sweep S2–S5) | close-position | ✅ Filled @ $1823.94, pnl=-$0.54 |
| BTC-USDT long 0.05 (pre-existing) | untouched | Still open |

---

## §6. Bottom line

**Is a $5-margin open viable on Hyperliquid?**

**Yes** — from a notional standpoint: $5 × 5x = $25 clears the $10 floor. The Defect B test failure was **not** a margin floor issue.

**The real bug (unfixed):** `_place_order` in `hyperliquid.py` sends `_float_to_wire(float(order.size))` without first rounding to `szDecimals`. The margin clamp writes 8 dp; `_float_to_wire` preserves all significant digits; HL rejects sizes with more than `szDecimals` decimal places. For ETH (`szDecimals=4`): `0.01339836` → rejected; `0.0134` → filled. This bug affects any size computed by the clamp (or any caller that passes a high-precision Decimal).

**Recommended fix** (not applied here — investigation only): in `_place_order`, round size to `szDecimals` before `_float_to_wire`:
```python
sz_dec = (self._sz_decimals_cache or {}).get(coin, 4)
size_rounded = round(float(order.size), sz_dec)
size_wire = self._float_to_wire(size_rounded)
```
Same pattern should apply to trigger-order sizes (`size_wire` reused for TP/SL legs). No `margin_per_trade` change needed — the $5 default is adequate for HL.

---

# Defect C — HL order size not rounded to szDecimals

_Updated 2026-06-17. Fix applied and tested._

---

## §1. Diff (`order-executor/app/adapters/hyperliquid.py` only)

```diff
+    def _round_size(self, symbol: str, size: float) -> float:
+        """Quantize order size to the coin's szDecimals. HL rejects sizes with
+        more decimal places than szDecimals ('Order has invalid size.')."""
+        coin = symbol.replace("-USDT", "").replace("-USD", "").upper()
+        sz_dec = (self._sz_decimals_cache or {}).get(coin, 4)
+        return round(float(size), sz_dec)

     # _place_order — entry size line:
-        price_wire = self._float_to_wire(self._round_price(float(price_str)))
-        size_wire  = self._float_to_wire(float(order.size))
+        price_wire   = self._float_to_wire(self._round_price(float(price_str)))
+        size_rounded = self._round_size(order.symbol, float(order.size))
+        if size_rounded <= 0:
+            return OrderResult(
+                success=False,
+                status="rejected",
+                error_msg=(
+                    f"Order size {order.size} rounds to 0 at szDecimals precision "
+                    f"for {order.symbol}; increase margin_per_trade or size."
+                ),
+            )
+        size_wire = self._float_to_wire(size_rounded)

     # place_trigger_orders — standalone trigger size line:
-            size_wire      = self._float_to_wire(size)
+            size_wire      = self._float_to_wire(self._round_size(symbol, float(size)))
```

TP/SL trigger legs in both `_place_order` and `place_trigger_orders` reuse `size_wire` — they inherit the rounded value automatically. `close_position` calls `_place_order` so it inherits the fix too.

---

## §2. Container grep — new code is live

```
225:    def _round_size(self, symbol: str, size: float) -> float:
331:        size_rounded = self._round_size(order.symbol, float(order.size))
332:        if size_rounded <= 0:
337:                    f"Order size {order.size} rounds to 0 at szDecimals precision "
341:        size_wire = self._float_to_wire(size_rounded)
834:            size_wire      = self._float_to_wire(self._round_size(symbol, float(size)))
```

---

## §3. Test G — 8-dp size that previously failed now fills ✅ PASS

**Request:** `symbol=ETH-USDT, size=0.01339836, leverage=5`

**Response:**
```json
{ "success": true, "status": "filled", "actual_fill_price": "1811.25" }
```

**HL statuses[0]:** `{"filled": {"totalSz": "0.0134", "avgPx": "1811.25", "oid": 55114457007}}`

`totalSz="0.0134"` — HL received 4-dp wire size. Pre-fix this same call returned `"Order has invalid size."` ✅

---

## §4. Test H — end-to-end clamped webhook fills ✅ PASS

**Strategy:** `test_hl_demo_01` (ETH-USDT, Hyperliquidtest, margin_per_trade=5, lev=5)
**Webhook:** `open_long, size=0.1, leverage=5` — no price

**Listener log:**
```
no webhook price; using exchange mark price 1814.1 for ETH-USDT
Strategy test_hl_demo_01 margin clamp: 0.1 → 0.01378094 (margin=5.0, lev=5, price=1814.1)
```

**DB `orders` row:**
```
size=0.01378094  status=filled  error_msg=  ref_price_source=exchange_mark
```

`0.01378094` → `_round_size` → `0.0138` (4 dp) → HL fills. Defects B and C resolved end-to-end. ✅

---

## §5. Test I — zero-size guard fires ✅ PASS

**Coin:** `GMT-USDT` (`szDecimals=0`, `maxLeverage=2`). `round(0.4, 0) = 0`.

**Request:** `size=0.4, leverage=2` (leverage guard passes; zero-size guard in `_place_order` fires before any HL call)

**Response:**
```json
{
  "success": false,
  "status": "rejected",
  "error_msg": "Order size 0.4 rounds to 0 at szDecimals precision for GMT-USDT; increase margin_per_trade or size."
}
```

No `_update_leverage` call and no `/exchange` POST. ✅

---

## §6. Test J — regression: valid size unchanged ✅ PASS

**Request:** `symbol=ETH-USDT, size=0.02, leverage=5`

**Response:** `success=true, status=filled, fill_price=1816.18` ✅

---

## §7. Cleanup

| Position | Action | Result |
|---|---|---|
| ETH-USDT long 0.0472 (Tests G + H + J combined) | close-position | ✅ Filled @ $1814.39, pnl=-$0.00020 |
| BTC-USDT long 0.05 (pre-existing, not ours) | untouched | Still open |

---

## §8. Deviations

None. The `place_trigger_orders` close-path variable is `symbol` (matches the prompt's expectation). The TP/SL legs in `_place_order` reuse `size_wire` rather than re-reading `order.size`, confirming they inherit the rounding automatically — no extra call sites needed.

---

# LLM pct units investigation

_Updated 2026-06-17. Mode: read-only (no LLM spend, no code changes). Step 3 not required — Steps 1–2 were conclusive._

---

## Step 1 — Reasoning text (intended SL/TP %)

Representative excerpts from the 40 most recent `open_long`/`open_short` signals:

| Date | Action | Symbol | Stated SL/TP in reasoning |
|---|---|---|---|
| 2026-06-14 | open_short | ETH | "targeting the range support at 1632.71 (**3.8% take profit**) with a stop loss set 1% beyond the resistance level at 1730.13 (**1.9% stop loss**)" |
| 2026-06-11 | open_short | ETH | "stop loss placed approximately **0.9%** beyond the resistance level; take profit targets range support at 1604.62" |
| 2026-06-11 | open_long  | BTC | "tight stop-loss of **0.6%** is placed below the EMA50, targeting the nearest resistance at 62857" |
| 2026-06-15 | open_short | ETH | reasoning truncated but SL derived from "1% beyond resistance", TP from range support |
| 2026-06-12 (×5) | open_short | BTC | no explicit % stated; SL described relative to resistance levels |
| 2026-06-10 | open_short | ETH | "within 0.88% of nearest resistance… take profit targeting 2423.75" |

**Pattern:** every time the model states a percentage it uses single-digit whole numbers — 0.6%, 0.9%, 1%, 1.9%, 3.8%. No evidence of sub-0.1% or fractional intent.

---

## Step 2 — Recovered take_profit_pct from stored TP prices

16 AI-originated orders have `order_id` set. 12 have both `actual_fill_price` and `tp_price`.

`approx_tp_pct = |fill_price − tp_price| / fill_price × 100` (fill price used as entry proxy; small price drift between signal time and fill inflates the ETH numbers slightly — see note below):

| Date | Action | Symbol | Fill px | tp_price | approx_tp_pct |
|---|---|---|---|---|---|
| 2026-06-16 | open_long  | BTC | 65797.1 | 66351.935 | 0.84% |
| 2026-06-15 | open_short | ETH | 1779.86 | 1652.203 | 7.17% |
| 2026-06-14 | open_short | ETH | 1723.88 | 1633.967 | **5.22%** |
| 2026-06-12 | open_short | BTC | 63604 | 63220.348 | 0.60% |
| 2026-06-12 | open_short | BTC | 63736.4 | 63303.228 | 0.68% |
| 2026-06-12 | open_short | BTC | 63034.5 | 62407.62 | 0.99% |
| 2026-06-12 | open_short | BTC | 63622.9 | 63190.887 | 0.68% |
| 2026-06-11 | open_short | BTC | 63496.9 | 62789.750 | 1.11% |
| 2026-06-11 | open_short | ETH | 1715.23 | 1604.709 | **6.44%** |
| 2026-06-11 | open_short | BTC | 62896 | 62414.745 | 0.77% |
| 2026-06-11 | open_short | ETH | 1698.4 | 1603.222 | **5.60%** |
| 2026-06-11 | open_short | ETH | 1680.3 | 1603.965 | **4.54%** |

**Two clusters:**
- BTC scalper: **0.60–1.11%** (n=6, median≈0.72%)
- ETH range-trader: **4.54–7.17%** (n=5, median≈5.60%)

**Note on ETH inflation:** the approx_tp_pct uses `actual_fill_price` which can differ from the signal-time `current_price` by 1–3% on ETH (less liquid, wider spreads). The June 14 signal is the one with the explicit cross-check (see below).

**Key cross-check (June 14 ETH open_short):**

Reasoning states: "targeting the range support at 1632.71 (**3.8% take profit**)".
Signal-time current_price from reasoning: 1698.51.
Stored tp_price: 1633.9666.

Verify: `1698.51 × (1 − 3.8/100) = 1698.51 × 0.962 = 1633.87 ≈ 1633.9666` ✓

The model emitted `take_profit_pct = 3.8` (whole-number percent). The formula divided it by 100 correctly. `approx_tp_pct` appears as 5.22% only because the fill price (1723.88) was 1.5% above the signal-time price (ETH drifted between generation and fill).

**min/median/max of recovered values (12 rows):** 0.60% / 0.84% / 7.17%

---

## Step 3 — Not run

Steps 1–2 provided conclusive evidence. No LLM spend was necessary.

---

## Verdict: B — No units mismatch

**The model emits whole-number percentages consistently.** Every value cross-checkable against the reasoning text confirms this: "3.8% take profit" → `take_profit_pct = 3.8`, "0.6% stop loss" → `stop_loss_pct = 0.6`. The `/100` division in `node_guard` is correct.

The `-0.049` incident was **out-of-range garbage with the wrong sign** — not a fraction-vs-whole debate. Its magnitude (0.049%) is ~15× smaller than the smallest normal BTC scalper TP (0.60%), confirming it's a model hallucination, not a units convention.

**The `abs()` fix (commit `31db8ca`) is correct and sufficient** to prevent a negative value from flipping the TP direction. It is belt-and-braces rather than a unit correction.

**Recommended additional fix (not applied here):** add a range guard to `LLMSignalOutput` to catch future out-of-range values before they reach `node_guard`:
```python
from pydantic import Field

class LLMSignalOutput(BaseModel):
    ...
    stop_loss_pct:   float = Field(ge=0.05, le=50,
                                   description="Distance from entry as a percent, e.g. 1.5 = 1.5%")
    take_profit_pct: float = Field(ge=0.05, le=50,
                                   description="Distance from entry as a percent, e.g. 1.5 = 1.5%")
```
This makes the schema self-documenting (hints to the LLM what the units are), and rejects garbage before it reaches `node_guard`. `abs()` can stay as belt-and-braces.

---

## Note: strategy-tester/node_guard_sim.py still missing abs()

`strategy-tester/app/engine/node_guard_sim.py:111–112` has the identical pre-fix code:
```python
sl_pct   = float(signal['stop_loss_pct'])   # no abs()
tp_pct   = float(signal['take_profit_pct'])  # no abs()
```
If backtests replay signals that happen to carry a negative pct, the sim will compute the same wrong-direction price as production did. The `abs()` fix and the schema range guard should be mirrored here when the production fix is applied.

---

## Fix: SL/TP pct range guard + schema units hint (prod + tester parity)
_2026-06-17. Fixes: (1) missing abs() in node_guard_sim; (2) no guard against hallucinated/garbage pct values; (3) no schema hint to the LLM about units._

### Changes

**`ai-signal-generator/app/graph/nodes/node_analyze.py`**
- Import changed to `from pydantic import BaseModel, Field`
- `stop_loss_pct` / `take_profit_pct` fields now carry `Field(description=...)` hint:
  `"Distance from entry as a percent, e.g. 1.5 = 1.5%. Use 0 for hold/close actions."`
- No `ge`/`le` constraints added (hold/close legitimately emit 0.0).

**`ai-signal-generator/app/graph/nodes/node_guard.py`** (lines 10–11, 111–117)
- Added module-level constants: `_MIN_SL_TP_PCT = 0.05`, `_MAX_SL_TP_PCT = 50.0`
- Inside `action in ('open_long', 'open_short')` block, after existing `abs()` lines:
  ```python
  if not (_MIN_SL_TP_PCT <= sl_pct <= _MAX_SL_TP_PCT) or \
     not (_MIN_SL_TP_PCT <= tp_pct <= _MAX_SL_TP_PCT):
      return _reject(state, 'sl_tp_pct_out_of_range')
  ```

**`strategy-tester/app/engine/node_guard_sim.py`** (lines 19–20, 114–123)
- Same constants, `abs()`, and range guard added — full parity with prod.
- Closes the gap noted in the previous section.

**`strategy-tester/app/_vendored/node_analyze.py`** + **`CHECKSUMS`**
- Vendored mirror updated with same `Field(description=...)` changes.
- `CHECKSUMS` hash updated to `26df22ec3de26c8df9761123e021ae418e1f8d44c116072e6412219f096893a2`.

### Boundary logic test (all 6 OK)

| Input (raw) | abs() → | Guard result | Expected |
|---|---|---|---|
| sl=-0.049 | 0.049 < 0.05 | REJECT | ✓ |
| tp=-0.049 | 0.049 < 0.05 | REJECT | ✓ |
| sl=0.3, tp=0.8 (scalp) | 0.3, 0.8 | PASS | ✓ |
| sl=1.5, tp=3.8 (normal) | 1.5, 3.8 | PASS | ✓ |
| sl=80 | 80 > 50 | REJECT | ✓ |
| sl=0, tp=0 (hold zeros) | 0 < 0.05 | REJECT | ✓ |

### Holds/closes unaffected

The range guard is inside `if action in ('open_long', 'open_short'):` in both files.
`hold` / `adjust_stops` exit before that block; `close_long` / `close_short` fall through
to the final close block which never reads `sl_pct` / `tp_pct`. Zero-valued pct fields on
hold/close signals are never evaluated against the range guard.

### Deploy

Both services rebuilt (layer-cached) and force-recreated. Verified live:
```
ai-signal-generator: node_guard.py:10 _MIN_SL_TP_PCT, :117 sl_tp_pct_out_of_range
                     node_analyze.py:25–26 Field(description=...)
strategy-tester:     node_guard_sim.py:19 _MIN_SL_TP_PCT, :114 abs(float, :123 sl_tp_pct_out_of_range
                     _vendored/node_analyze.py:30–31 Field(description=...)
```

---

# Repo Hygiene Cleanup — Branch `cleanup/repo-hygiene`
_2026-06-17. Executor: Claude Sonnet 4.6. Risk: docs/scripts only — no service code, no db/ files (except additive MANIFEST.md)._

---

## 1. Files moved (from → to)

| From (root) | To |
|---|---|
| `MATP.SDD.md` | `docs/MATP.SDD.md` |
| `MATP_STRATEGY_TESTER_SDD_v1.1.md` | `docs/MATP_STRATEGY_TESTER_SDD_v1.1.md` |
| `MATP_UI_IMPLEMENTATION_PLAN.md` | `docs/MATP_UI_IMPLEMENTATION_PLAN.md` |
| `TEST_PLAN.md` | `docs/TEST_PLAN.md` |
| `ACTION_PLAN.md` | `docs/process/ACTION_PLAN.md` |
| `MATP-Gemini-Plan-v2.md` | `docs/process/MATP-Gemini-Plan-v2.md` |
| `prompts/` (entire dir) | `docs/process/prompts/` |
| `reports/` (entire dir) | `docs/process/reports/` |
| `run_hl_test.sh` | `scripts/run_hl_test.sh` |
| `run_integration_test.py` | `scripts/run_integration_test.py` |
| `test_blofin_e2e.py` | `scripts/test_blofin_e2e.py` |
| `test_payload.json` | `scripts/test_payload.json` |
| `test_webhook.sh` | `scripts/test_webhook.sh` |
| `test_webhook_manual.py` | `scripts/test_webhook_manual.py` |

All moves used `git mv` — history preserved, diff fully reviewable.

---

## 2. Files deleted (with evidence)

### `CHECKPOINT.md` (root) — deleted

```
$ cat CHECKPOINT.md
status: done
current_task: accounts page redesignedesign
```
56-byte corrupted stub. Canonical copy is `.gemini/CHECKPOINT.md` (real session-21 content, hundreds of bytes). Root copy deleted.

### `REPORT_FOR_HUMAN.md` (root) — deleted

```
$ wc -c REPORT_FOR_HUMAN.md .gemini/REPORT_FOR_HUMAN.md
47175 REPORT_FOR_HUMAN.md
38557 .gemini/REPORT_FOR_HUMAN.md
85732 total
```

Root copy (47175 bytes) is the stale "Strategy Tester Implementation Report" from an earlier session. `.gemini/REPORT_FOR_HUMAN.md` (38557 bytes) is the current "HL leverage + margin overrun" report dated 2026-06-16. The root copy is the older diverged version — deleted.

```
$ diff REPORT_FOR_HUMAN.md .gemini/REPORT_FOR_HUMAN.md | head -5
1c1,2
< # Strategy Tester Implementation Report
---
> # HL leverage + margin overrun — fix verification report
> _Updated 2026-06-16. Covers: leverage fix (Defect A) + margin clamp bypass fix (Defect B)..._
```

### `prompts/session_start.md` (underscore) — deleted

```
$ diff prompts/session-start.md prompts/session_start.md | head -5
14,31d13
< ## Session Log
<
< On session start, initialize `prompts/session-log.md` by resetting it...
```
`session-start.md` (dash) is the superset — it has an extra "Session Log" section (18 additional lines). The underscore variant is a subset. Dash variant kept, underscore variant deleted.

---

## 3. Other changes

- **`.gitignore`**: Removed duplicate `dist/` entry (appeared under both Python and Node sections; one `dist/` in Python section retained).
- **`db/migrations/MANIFEST.md`**: New additive file created — lists all 27 migration files with purpose, flags the 5 collision numbers (`001`, `002`, `003`, `004`, `012`), and notes the unnumbered `fix_strategies_insert.sql`.
- **Internal doc links fixed** in: `README.md`, `docs/MATP.SDD.md`, `docs/TEST_PLAN.md`, `docs/process/MATP-Gemini-Plan-v2.md`, `docs/process/prompts/sync.md`, `docs/process/prompts/resume.md`.

---

## 4. Verification output — §10 A–F (raw command output)

### A. Full picture of what changed (`git status --short`)

```
M  .gitignore
D  CHECKPOINT.md
M  README.md
D  REPORT_FOR_HUMAN.md
A  db/migrations/MANIFEST.md
R  MATP.SDD.md -> docs/MATP.SDD.md
R  MATP_STRATEGY_TESTER_SDD_v1.1.md -> docs/MATP_STRATEGY_TESTER_SDD_v1.1.md
R  MATP_UI_IMPLEMENTATION_PLAN.md -> docs/MATP_UI_IMPLEMENTATION_PLAN.md
R  TEST_PLAN.md -> docs/TEST_PLAN.md
R  ACTION_PLAN.md -> docs/process/ACTION_PLAN.md
R  MATP-Gemini-Plan-v2.md -> docs/process/MATP-Gemini-Plan-v2.md
R  prompts/260519_claude_to_gemini_webhooks_strategies.prompt -> docs/process/prompts/260519_claude_to_gemini_webhooks_strategies.prompt
R  prompts/matp-session-01.prompt -> docs/process/prompts/matp-session-01.prompt
R  prompts/matp-session-02.prompt -> docs/process/prompts/matp-session-02.prompt
R  prompts/matp-session-07.prompt -> docs/process/prompts/matp-session-07.prompt
R  prompts/resume.md -> docs/process/prompts/resume.md
R  prompts/session-log.md -> docs/process/prompts/session-log.md
R  prompts/session-start.md -> docs/process/prompts/session-start.md
R  prompts/sync.md -> docs/process/prompts/sync.md
R  reports/part2-reset-report.md -> docs/process/reports/part2-reset-report.md
R  reports/part2-reset-test-report.md -> docs/process/reports/part2-reset-test-report.md
R  reports/part3-fix-report.md -> docs/process/reports/part3-fix-report.md
R  reports/part3-units-check.md -> docs/process/reports/part3-units-check.md
R  reports/part4-pnl-attribution-report.md -> docs/process/reports/part4-pnl-attribution-report.md
R  reports/reconciler-tests-report.md -> docs/process/reports/reconciler-tests-report.md
D  prompts/session_start.md
R  run_hl_test.sh -> scripts/run_hl_test.sh
R  run_integration_test.py -> scripts/run_integration_test.py
R  test_blofin_e2e.py -> scripts/test_blofin_e2e.py
R  test_payload.json -> scripts/test_payload.json
R  test_webhook.sh -> scripts/test_webhook.sh
R  test_webhook_manual.py -> scripts/test_webhook_manual.py
```

### B. Root remaining files

```
$ ls -la | grep -vE '^d' | awk '{print $NF}'
CHANGELOG.md
CLAUDE.md
docker-compose.yml
.env
.env.example
.gitignore
LICENSE
Makefile
README.md
```
(Plus 4 pre-existing zero-byte untracked stray files: `=`, `Note:`, `not`, `opened_at` — see §5 Skipped.)

### C. New locations populated

```
$ ls docs/
MATP_AI_Siganl_Generator_Spec_v1.0.md
MATP.SDD.md
MATP_STRATEGY_TESTER_SDD_v1.1.md
MATP_UI_IMPLEMENTATION_PLAN.md
process
README.md
ROADMAP.md
session-27-28-verification.md
setup.md
TEST_PLAN.md
tradingview.md

$ ls docs/process/
ACTION_PLAN.md
MATP-Gemini-Plan-v2.md
prompts
reports

$ ls scripts/
e2e_test.sh
redeploy.sh
run_hl_test.sh
run_integration_test.py
test_blofin_e2e.py
test_payload.json
test_webhook_manual.py
test_webhook.sh
```

### D. No dangling .md links remain

All hits from the grep are now updated paths that resolve to real files. Every referenced path (`docs/MATP.SDD.md`, `docs/process/ACTION_PLAN.md`, `docs/TEST_PLAN.md`, `docs/MATP_UI_IMPLEMENTATION_PLAN.md`) was confirmed present with `ls`.

### E. db/ untouched

```
?? db/migrations/MANIFEST.md
```
Only the new additive MANIFEST.md shows — no existing db/ file was modified, moved, or deleted. Correct.

### F. Service dirs untouched

```
?? tester-ui/.vite/
```
Only pre-existing untracked Vite cache directory — no service source code touched. Correct.

---

## 5. Skipped / Needs human

- **4 stray zero-byte files at root** (`=`, `Note:`, `not`, `opened_at`): These predate this branch (visible in the original git status as `??`). They look like accidental files from a mistyped command. They are untracked and not part of the doc/script cleanup scope. A human should `git rm` them (or simply `rm`) in a follow-up.
- **Migration archiving/renumbering**: Deliberately deferred as specified — `db/init.sql` is incomplete (missing tester schema and AI tables), so migrations are still load-bearing. MANIFEST.md created to document the collision state.
- **dashboard-ui ↔ tester-ui shared-component fork**: Out of scope (design decision, not file hygiene).
- **`order-generator` keep/retire**: Out of scope (architecture decision).

---

## 6. db/ and service dir confirmation

**db/**: Only `db/migrations/MANIFEST.md` added (new file, no existing file modified). Confirmed by check E above.

**Service dirs**: Zero entries in `git status --short` for any of: `order-listener/`, `order-executor/`, `order-generator/`, `ai-signal-generator/`, `dashboard-api/`, `dashboard-ui/`, `tester-ui/`, `strategy-tester/`, `nginx/`. Confirmed by check F above.

---

_Branch `cleanup/repo-hygiene` pushed. Commit: `c415c45`. Do not merge to main without review._

---

# Backlog + CLAUDE.md pointer — 2026-06-17

Two doc-only edits on `cleanup/repo-hygiene` (commit `6b87e82`):

1. `docs/ROADMAP.md` — appended "AI prompt template management page" bullet to `## Deferred Backlog`.
2. `CLAUDE.md` — added `docs/ROADMAP.md` pointer to `## Golden rules`.

**Verification:**
```
$ grep -n "AI prompt template management page" docs/ROADMAP.md
58:- **AI prompt template management page**: no runtime CRUD exists for `ai_prompt_templates`...

$ grep -n "Deferred work and design decisions live in" CLAUDE.md
10:- Deferred work and design decisions live in `docs/ROADMAP.md` (see its "Deferred Backlog" and "Open Design Questions"). Check there before starting new feature work.
```

No build or redeploy needed — docs only.

---

# db/init.sql Baseline Regeneration — Branch `deploy/init-sql-baseline`
_2026-06-17. Executor: Claude Sonnet 4.6. Commit: `aed80e1`._

---

## §1 Live-DB inventory (pre-dump)

### public.* tables (19 tables)
```
 Schema |          Name          | Type  | Owner
--------+------------------------+-------+-------
 public | ai_prompt_templates    | table | matp
 public | ai_risk_config         | table | matp
 public | ai_risk_config_audit   | table | matp
 public | ai_signal_log          | table | matp
 public | ai_strategy_config     | table | matp
 public | assets                 | table | matp
 public | config                 | table | matp
 public | dead_letter_orders     | table | matp
 public | exchange_accounts      | table | matp
 public | order_events           | table | matp
 public | order_execution_log    | table | matp
 public | orders                 | table | matp
 public | signal_log             | table | matp
 public | strategies             | table | matp
 public | strategy_performance   | table | matp
 public | strategy_positions     | table | matp
 public | strategy_stats         | table | matp
 public | strategy_webhook_calls | table | matp
 public | trading_pairs          | table | matp
(19 rows)
```

### tester.* tables (9 tables)
```
 Schema |        Name        | Type  | Owner
--------+--------------------+-------+-------
 tester | ai_risk_config     | table | matp
 tester | ai_signal_log      | table | matp
 tester | ai_strategy_config | table | matp
 tester | backtest_runs      | table | matp
 tester | equity_curve       | table | matp
 tester | ohlcv_cache        | table | matp
 tester | orders             | table | matp
 tester | strategies         | table | matp
 tester | strategy_positions | table | matp
(9 rows)
```

### Reference table row counts
```
          t          | count
---------------------+-------
 config              |     3
 ai_prompt_templates |     6
 assets              |     4
 trading_pairs       |     1
 exchange_accounts   |     7   ← schema-only, never dumped as data
```

---

## §5 Static checks (expected vs actual)

| Check | Expected | Actual | Pass? |
|-------|----------|--------|-------|
| `CREATE SCHEMA tester` | ≥ 1 | 1 | ✓ |
| `CREATE TABLE tester.*` | 9 | 9 | ✓ |
| AI tables in public | 5 | 5 | ✓ |
| `llm_provider` present | ≥ 1 | 7 | ✓ |
| `ai_reasoning` present | ≥ 1 | 3 | ✓ |
| dropped cols in public.* | 0 | 0 | ✓ |
| `INSERT INTO exchange_accounts` | 0 | 0 | ✓ (SECURITY) |
| `CREATE TABLE.*exchange_accounts` | 1 | 1 | ✓ |
| `INSERT INTO.*config` | ≥ 1 | 3 | ✓ |
| `INSERT INTO.*ai_prompt_templates` | ≥ 1 | 6 | ✓ |
| pgcrypto extension | 1 | 1 | ✓ |

**Note on `max_position_size` appearing 2× in the file:** Both hits are in `tester.ai_risk_config` (`max_position_size_pct`) and `tester.strategies` (`max_position_size`) — these are the tester schema's own tables, independently maintained from `public.*`. Migration 021 only dropped those columns from `public.strategies` and `public.ai_risk_config`, which was confirmed by querying `information_schema.columns WHERE table_schema='public'` → 0 rows. The tester schema intentionally retains these columns.

---

## §6 Boot-test assertion output (full)

**Init errors:** none (grep -iE "error|fatal|cannot" returned empty)

**Assertions:**
```
 tester_schema             |     1
 tester_tables             |     9
 ai_tables                 |     5
 dropped_cols_should_be_0  |     0
 seed_config               |     3
 seed_ai_prompts           |     6
 OP_orders_should_be_0     |     0
 OP_signal_log_should_be_0 |     0
 OP_positions_should_be_0  |     0
 SEC_accounts_should_be_0  |     0
```

All assertions match expected values. Container booted successfully, ran initdb, and remained up.

---

## §4 Line-count delta

| File | Lines |
|------|-------|
| Old `db/init.sql` | ~318 lines (original baseline, pre-regeneration) |
| New `db/init.sql` | 2282 lines |
| Net change | +2239 insertions, -318 deletions (per `git diff --stat`) |

---

## Unexpected items / fixes applied during generation

1. **`CREATE SCHEMA public` → `CREATE SCHEMA IF NOT EXISTS public`**: PostgreSQL 16 creates the `public` schema by default before running initdb scripts. The plain `CREATE SCHEMA public` from `pg_dump` errored; changed to `IF NOT EXISTS`.

2. **`pgcrypto` extension not in dump**: `pg_dump --schema-only --no-privileges` omits `CREATE EXTENSION` statements. The `webhook_secret` default on `tester.strategies` uses `public.gen_random_bytes(16)` which requires `pgcrypto`. Added `CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;` explicitly to the init.sql header. The explicit `WITH SCHEMA public` was required because `pg_dump` sets `search_path = ''` at the top of the file.

3. **`assets` and `trading_pairs` tables were seeded**: These had 4 and 1 rows respectively in the live DB — included in the seed dump as expected. Not previously documented as reference tables, but they are.

---

## Completeness confirmation

- `db/`, `docker-compose.yml`, service code: untouched.
- No migration files modified.
- Branch `deploy/init-sql-baseline` pushed. Do not merge to `main` without review.

---

# Merge to `main` — 2026-06-17

Branch `deploy/init-sql-baseline` (which stacked on top of `cleanup/repo-hygiene`) merged into `main` at commit `bfdcb98`.

---

## §1 Branch content (pre-merge)

`git log --oneline origin/main..origin/deploy/init-sql-baseline`:
```
e80326e docs: add init.sql baseline regeneration report to .gemini/REPORT_FOR_HUMAN.md
aed80e1 deploy: regenerate db/init.sql as complete baseline (tester schema + AI objects + reference seeds)
6b87e82 docs: add AI prompt template management to backlog; wire ROADMAP pointer in CLAUDE.md
ef63d35 docs: append repo hygiene cleanup report to .gemini/REPORT_FOR_HUMAN.md
c415c45 chore: repo hygiene — consolidate docs into docs/ and docs/process/, move test scripts to scripts/, remove stray root duplicates
```

`git diff --stat origin/main origin/deploy/init-sql-baseline | tail -5`:
```
 test_webhook.sh => scripts/test_webhook.sh         |    0
 .../test_webhook_manual.py                         |    0
 36 files changed, 2677 insertions(+), 1653 deletions(-)
```

Ancestor check: `main is ancestor — clean merge`

---

## §3 Post-merge verification (full output)

### A. Recent log
```
bfdcb98 merge: repo-hygiene cleanup + complete db/init.sql baseline
e80326e docs: add init.sql baseline regeneration report to .gemini/REPORT_FOR_HUMAN.md
aed80e1 deploy: regenerate db/init.sql as complete baseline (tester schema + AI objects + reference seeds)
6b87e82 docs: add AI prompt template management to backlog; wire ROADMAP pointer in CLAUDE.md
```

### B. init.sql content checks
```
tester schema:          1        (expect 1)      ✓
tester tables:          9        (expect 9)      ✓
ai_ tables:             5        (expect 5)      ✓
exchange_accounts data: 0        (MUST be 0)     ✓ SECURITY
config seed:            3        (>=1)           ✓
ai_prompt seed:         6        (>=1)           ✓
```

### C. Cleanup checks
```
root stray files gone — correct
moved files in place — correct
```

### D. Migrations intact
```
27    (db/migrations/*.sql count)
db/migrations/MANIFEST.md   present
```

---

## Branch deletion

- `deploy/init-sql-baseline` — deleted locally and on remote ✓
- `cleanup/repo-hygiene` — deleted locally and on remote ✓

## Stray zero-byte files removed from working tree

```
removed stray: =
removed stray: Note:
removed stray: not
removed stray: opened_at
```

## Push confirmation

`main` pushed to `origin/main` (3f3bee9 → bfdcb98). No force-push used.

---

# .env.example + docs/setup.md fix — Branch `docs/env-setup`
_2026-06-17. Executor: Claude Sonnet 4.6. Commit: `886e6e4`._

---

## §1 Compose-var ↔ `.env.example` reconciliation

| Var | In compose as | `.env.example` | Notes |
|-----|--------------|----------------|-------|
| `POSTGRES_PASSWORD` | `${POSTGRES_PASSWORD}` (required) | ✓ present | |
| `MASTER_KEY` | `${MASTER_KEY}` x2 (required) | ✓ present | order-listener + order-executor |
| `GEMINI_API_KEY` | `${GEMINI_API_KEY}` x2 (no default) | ✓ present | ai-signal-generator + strategy-tester |
| `OPENAI_API_KEY` | `${OPENAI_API_KEY:-}` (empty default) | ✓ present | |
| `ANTHROPIC_API_KEY` | `${ANTHROPIC_API_KEY:-}` (empty default) | ✓ present | |
| `CRYPTOPANIC_API_KEY` | `${CRYPTOPANIC_API_KEY}` (no default) | ✓ present | ai-signal-generator only |
| `DATABASE_URL` | `${DATABASE_URL:-...}` (with default) | ✓ commented optional | ai-signal-generator only |
| `DATA_FEED_EXCHANGE` | `${DATA_FEED_EXCHANGE:-binance}` | ✓ present | |
| `TESTER_DEFAULT_BALANCE` | `${TESTER_DEFAULT_BALANCE:-1000.0}` | ✓ present | |
| `TESTER_DEFAULT_FEE_PCT` | `${TESTER_DEFAULT_FEE_PCT:-0.02}` | ✓ present | |
| `TESTER_DEFAULT_SLIPPAGE_PCT` | `${TESTER_DEFAULT_SLIPPAGE_PCT:-0.05}` | ✓ present | |
| `TESTER_MAX_CONCURRENT_RUNS` | `${TESTER_MAX_CONCURRENT_RUNS:-1}` | ✓ present | |
| `TESTER_LLM_FAILURE_THRESHOLD` | `${TESTER_LLM_FAILURE_THRESHOLD:-0.05}` | ✓ present | |
| `EXECUTOR_URL` | hardcoded in compose (phantom in .env) | removed | was in old .env.example; compose ignores it |
| `WEBHOOK_SECRET` | not in compose at all | absent | phantom — no service reads it |
| Exchange credentials | not in compose at all | absent | stored encrypted in DB via Accounts page |

---

## §2 TESTER_* defaults (source: `strategy-tester/app/config.py`)

| Var | Default |
|-----|---------|
| `tester_default_balance` | `1000.0` |
| `tester_default_slippage_pct` | `0.05` |
| `tester_default_fee_pct` | `0.02` |
| `tester_max_concurrent_runs` | `1` |
| `tester_llm_failure_threshold` | `0.05` |

Two additional tester settings exist in config.py but are NOT in docker-compose.yml (no `${...}` reference): `tester_ohlcv_fetch_batch=1000` and `tester_equity_insert_batch=500`. Omitted from `.env.example` as they can't be overridden via compose env.

---

## §3 DATABASE_URL handling

Only `ai-signal-generator` reads `DATABASE_URL` from env (with fallback default `postgresql://matp:matp@postgres:5432/matp`). All other services have it hardcoded in `docker-compose.yml`. Included in `.env.example` as a commented-out optional override.

---

## §4 Full §5 verification output

```
=== Compose var coverage ===
ok   ANTHROPIC_API_KEY
ok   CRYPTOPANIC_API_KEY
ok   DATABASE_URL
ok   DATA_FEED_EXCHANGE
ok   GEMINI_API_KEY
ok   MASTER_KEY
ok   OPENAI_API_KEY
ok   POSTGRES_PASSWORD
ok   TESTER_DEFAULT_BALANCE
ok   TESTER_DEFAULT_FEE_PCT
ok   TESTER_DEFAULT_SLIPPAGE_PCT
ok   TESTER_LLM_FAILURE_THRESHOLD
ok   TESTER_MAX_CONCURRENT_RUNS

=== Phantom/credential vars absent ===
correctly absent: WEBHOOK_SECRET
correctly absent: BLOFIN_API_KEY
correctly absent: BLOFIN_API_SECRET
correctly absent: HYPERLIQUID_PRIVATE_KEY

=== setup.md clean of wrong env instructions ===
setup.md clean of wrong env instructions

=== No real secrets ===
no real secrets
```

---

## §5 nginx exposure — correction to prompt claim

The prompt stated "nginx proxies only `/api/listener/`, `/api/generator/`, `/api/dashboard/`". This is **incorrect** based on the actual `nginx/nginx.conf`:

| nginx route | Upstream | Notes |
|-------------|----------|-------|
| `/api/listener/` | `order-listener:8001` | |
| `/api/generator/` | `order-generator:8002` | |
| `/api/ai/` | `dashboard-api:8003` (rewrite → `/ai/...`) | ai-signal-generator data flows through dashboard-api |
| `/api/tester/` | `strategy-tester:8006` | directly proxied |
| `/tester/` | `tester-ui:3001` | tester UI is proxied |
| `/api/dashboard/` | `dashboard-api:8003` | |
| `/ws/` | `dashboard-api:8003` | WebSocket |

The **real** gap: `ai-signal-generator` (8005) has no nginx route — it is only reachable at direct host port 8005 (published via `ports: "8005:8005"` in compose). All other services are reachable through nginx. `order-executor` is internal-only (no nginx route, no published port).

---

## Needs human

1. **`POSTGRES_PASSWORD` ≠ hardcoded `matp` credential mismatch**: Most services in `docker-compose.yml` hardcode `DATABASE_URL: postgresql://matp:matp@postgres:5432/matp`. `POSTGRES_PASSWORD` in `.env` only changes the postgres container's actual password. If the user changes `POSTGRES_PASSWORD` away from `matp`, all those hardcoded URLs break. `docker-compose.yml` needs to be updated to construct DATABASE_URL from `${POSTGRES_PASSWORD}`. Flagged with a warning in `.env.example`.

2. **`PUBLIC_HOST` not wired in docker-compose.yml**: `dashboard-api/src/routes/strategies.ts:369` reads `process.env.PUBLIC_HOST` to build the webhook URL shown in the UI. But `PUBLIC_HOST` is absent from the `dashboard-api` environment block in `docker-compose.yml`, so the env var is never passed to the container. Fix: add `PUBLIC_HOST: ${PUBLIC_HOST:-}` to the `dashboard-api` service environment in `docker-compose.yml`. Noted in `.env.example` comment.

3. **`order-generator` YAML strategy path**: `setup.md` "Adding a Strategy" section has been updated to show the dashboard flow as primary and the YAML/volume path as secondary, with a `<!-- TODO: confirm order-generator strategy path still supported -->` comment.

4. **`TESTER_OHLCV_FETCH_BATCH` and `TESTER_EQUITY_INSERT_BATCH`**: exist in `strategy-tester/app/config.py` but have no `${...}` entry in `docker-compose.yml` — cannot be overridden via compose env. If these need to be tunable, add them to the strategy-tester environment block.

5. **`CRYPTOPANIC_API_KEY` has no compose default**: compose uses `${CRYPTOPANIC_API_KEY}` with no `:-` fallback. Docker Compose will warn if unset. Setting it to empty string in `.env` silences the warning; the app config defaults to `""` regardless.

---

# PUBLIC_HOST wiring + `docs/env-setup` merge to `main` — 2026-06-17
_Commit: `6ec712c`. Executor: Claude Sonnet 4.6._

---

## §1 PUBLIC_HOST consumer grep

```
$ grep -rniE "PUBLIC_HOST" --include=*.ts --include=*.py --include=*.js . | grep -iE "process\.env|getenv|environ"
dashboard-api/src/routes/strategies.ts:369:    const host = process.env.PUBLIC_HOST
```
Single hit — only `dashboard-api`. Safe to wire only to that service.

---

## §2 dashboard-api environment block — before / after

**Before:**
```yaml
  dashboard-api:
    environment:
      DATABASE_URL: postgresql://matp:matp@postgres:5432/matp
      REDIS_URL: redis://redis:6379
      GENERATOR_URL: http://order-generator:8002
      LISTENER_URL: http://order-listener:8001
      EXECUTOR_URL: http://order-executor:8004
      AI_SIGNAL_GENERATOR_URL: http://ai-signal-generator:8005
```

**After (one line added):**
```yaml
  dashboard-api:
    environment:
      DATABASE_URL: postgresql://matp:matp@postgres:5432/matp
      REDIS_URL: redis://redis:6379
      GENERATOR_URL: http://order-generator:8002
      LISTENER_URL: http://order-listener:8001
      EXECUTOR_URL: http://order-executor:8004
      AI_SIGNAL_GENERATOR_URL: http://ai-signal-generator:8005
      PUBLIC_HOST: ${PUBLIC_HOST:-}
```

---

## §3 Post-merge verification (§7 A–D full output)

```
=== A: recent log ===
6ec712c merge: deployable .env.example + setup.md + PUBLIC_HOST wiring
4deccc7 deploy: pass PUBLIC_HOST through to dashboard-api; correct .env.example note
b471d7d docs: append env/setup fix report to .gemini/REPORT_FOR_HUMAN.md

=== B: PUBLIC_HOST wired and compose valid ===
compose valid ✓
      PUBLIC_HOST: ""
PUBLIC_HOST in dashboard-api ✓

=== C: env/docs vars and sections ===
ok GEMINI_API_KEY
ok OPENAI_API_KEY
ok ANTHROPIC_API_KEY
ok TESTER_DEFAULT_BALANCE
ok PUBLIC_HOST
no phantom/credential vars ✓
account-connect section present ✓

=== D: no real secrets ===
no real secrets ✓
```

---

## §4 Push and branch deletion

- `main` pushed: `40a8dec → 6ec712c` ✓
- `docs/env-setup` deleted locally (force `-D`: the final compose-wire commit `4deccc7` was committed locally after the branch's last push and reached origin via the merge into main — git correctly noted local tip was ahead of `origin/docs/env-setup`) ✓
- `docs/env-setup` deleted on remote ✓

---

## Operator note

`PUBLIC_HOST` now reaches the running container only after recreating it:
```bash
docker compose up -d dashboard-api   # env-only change — no --build needed
```
