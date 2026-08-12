"""
Binance USDⓈ-M perpetual futures adapter.

Third venue alongside Blofin and Hyperliquid, implementing the same
`ExchangeAdapter` contract so nothing above the adapter layer needs to know which
exchange an account is on.

Three differences from the other two are worth knowing before reading the code,
because they shape most of what follows:

1. **Quantity is base asset, not contracts.** Blofin needs `_to_contracts` /
   `_to_base` conversions on every path; Binance takes 0.005 BTC as `0.005`. The
   only shaping needed is rounding to the symbol's LOT_SIZE step and PRICE_FILTER
   tick.

2. **TP/SL cannot ride along with the entry order.** Blofin accepts
   `tpTriggerPrice` / `slTriggerPrice` on the order itself. Binance has no such
   field on `/fapi/v1/order` — protection is a *separate* STOP_MARKET /
   TAKE_PROFIT_MARKET order. `submit_order` therefore places the entry and then
   places the triggers, and reports partial failure loudly: an entry that filled
   with no stop attached is the one outcome the caller must never mistake for
   success.

3. **Triggers use `closePosition=true`, not a fixed size.** That makes them track
   the position: after a partial close the remaining stop still covers exactly
   what is left, and the exchange cancels them when the position goes flat. It is
   a better fit for this system than the size-carrying triggers the other two
   place, which `_resize_stops_after_partial_close` has to re-issue by hand.

One-way (net) position mode is assumed throughout, matching how order-listener
models a position. `positionSide` is never sent, which is what Binance expects in
one-way mode; `_check_position_mode` fails loudly rather than silently
mis-trading if the account is in hedge mode.
"""

import asyncio
import hashlib
import hmac
import logging
import math
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.adapters.base import (
    ExchangeAdapter,
    ExchangeUnavailableError,
    CANDLE_BAR_SECONDS,
    MMR_CONSERVATISM_BUFFER,
)
from app.models import OrderRequest, OrderResult, Position

logger = logging.getLogger(__name__)

_EXCHANGE_INFO_TTL = 86400   # 24h — contract specs change rarely
_BRACKET_TTL       = 86400
_RECV_WINDOW       = 10000   # ms; homelab clock drift plus a slow hop
_KLINES_MAX_PER_CALL = 1500  # Binance's ceiling for /fapi/v1/klines

# Order types that are conditional triggers rather than resting book orders.
# get_open_orders must exclude these (the base contract gives them to
# list_trigger_orders instead); list_trigger_orders keeps only these.
_TRIGGER_TYPES = {
    "STOP", "STOP_MARKET",
    "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
    "TRAILING_STOP_MARKET",
}

# Binance answers "the account is already in that state" with an error rather than
# a no-op. Setting leverage or margin type is idempotent for us, so these are
# success, not failure.
_ALREADY_SET_CODES = {-4046, -4047}


class BinanceAdapter(ExchangeAdapter):
    # Class-level caches shared across instances, keyed by base_url so testnet and
    # live never contaminate each other.
    _exchange_info: Dict[str, Dict[str, dict]] = {}   # base_url -> symbol -> spec
    _exchange_info_ts: Dict[str, float] = {}
    _brackets: Dict[str, Dict[str, list]] = {}        # base_url -> symbol -> bracket list
    _brackets_ts: Dict[str, Dict[str, float]] = {}

    def __init__(self, credentials: dict, mode: str):
        super().__init__(credentials, mode)
        self.api_key    = credentials["api_key"]
        self.api_secret = credentials["api_secret"]
        # Testnet moved to demo-fapi.binance.com; testnet.binancefuture.com is the
        # old host and is not what the current docs point at.
        self.base_url = (
            "https://demo-fapi.binance.com"
            if mode == "demo"
            else "https://fapi.binance.com"
        )
        # One pooled client for the adapter's lifetime — a fresh AsyncClient per
        # call pays a TCP+TLS handshake every time, which compounds badly under
        # this host's load.
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10)
        self._position_mode_checked = False

    async def close(self) -> None:
        await self._client.aclose()

    # ── signing / transport ────────────────────────────────────────────────────

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _signed_query(self, params: dict) -> str:
        """Binance signs the exact query string it receives, so the signature must
        be computed over the same serialisation that is sent — build it once here
        and never re-encode it afterwards."""
        p = {k: v for k, v in params.items() if v is not None}
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = _RECV_WINDOW
        query = urlencode(p)
        return f"{query}&signature={self._sign(query)}"

    @property
    def _auth_headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    async def _private(self, method: str, path: str, params: dict | None = None) -> dict | list:
        """Signed request. Raises on transport failure; returns the parsed body
        (which may itself carry a Binance error code) otherwise."""
        query = self._signed_query(params or {})
        url = f"{path}?{query}"
        resp = await self._client.request(method, url, headers=self._auth_headers)
        try:
            body = resp.json()
        except Exception:
            raise ExchangeUnavailableError(
                f"binance {path} HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if resp.status_code != 200:
            # Binance puts a negative `code` and human `msg` in the body on error.
            raise ExchangeUnavailableError(
                f"binance {path} HTTP {resp.status_code}: "
                f"{body.get('code') if isinstance(body, dict) else ''} "
                f"{body.get('msg') if isinstance(body, dict) else str(body)[:200]}"
            )
        return body

    async def _public(self, path: str, params: dict | None = None) -> dict | list:
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _err(body) -> Optional[str]:
        """Binance's error message if this body is an error, else None."""
        if isinstance(body, dict) and body.get("code") is not None and int(body["code"]) < 0:
            return f"{body.get('code')}: {body.get('msg')}"
        return None

    # ── instrument specs ───────────────────────────────────────────────────────

    async def _refresh_exchange_info(self) -> None:
        try:
            data = await self._public("/fapi/v1/exchangeInfo")
            symbols = data.get("symbols", []) if isinstance(data, dict) else []
            specs = {}
            for s in symbols:
                # USDT-quoted perpetuals only: that is what this platform trades,
                # and including delivery futures would put expiring contracts in
                # the strategy symbol picker.
                if s.get("contractType") != "PERPETUAL" or s.get("status") != "TRADING":
                    continue
                if s.get("quoteAsset") != "USDT":
                    continue
                filters = {f.get("filterType"): f for f in s.get("filters", [])}
                specs[s["symbol"]] = {
                    "symbol":        s["symbol"],
                    "baseAsset":     s.get("baseAsset"),
                    "quoteAsset":    s.get("quoteAsset"),
                    "tickSize":      float(filters.get("PRICE_FILTER", {}).get("tickSize") or 0) or None,
                    "stepSize":      float(filters.get("LOT_SIZE", {}).get("stepSize") or 0) or None,
                    "minQty":        float(filters.get("LOT_SIZE", {}).get("minQty") or 0),
                    "minNotional":   float(filters.get("MIN_NOTIONAL", {}).get("notional") or 0),
                    "pricePrecision": int(s.get("pricePrecision") or 8),
                    "quantityPrecision": int(s.get("quantityPrecision") or 8),
                }
            if specs:
                BinanceAdapter._exchange_info[self.base_url] = specs
                BinanceAdapter._exchange_info_ts[self.base_url] = time.time()
                logger.info(f"BinanceAdapter: cached {len(specs)} perpetual specs from {self.base_url}")
        except Exception as e:
            logger.warning(f"BinanceAdapter: failed to refresh exchangeInfo: {e}")

    async def _get_spec(self, symbol: str) -> Optional[dict]:
        age = time.time() - BinanceAdapter._exchange_info_ts.get(self.base_url, 0)
        if age > _EXCHANGE_INFO_TTL or self.base_url not in BinanceAdapter._exchange_info:
            await self._refresh_exchange_info()
        return BinanceAdapter._exchange_info.get(self.base_url, {}).get(symbol)

    @staticmethod
    def _round_step(value: float, step: Optional[float], precision: int) -> float:
        """Round DOWN to the exchange's step. Down, not nearest: rounding a
        quantity up can exceed available margin or turn a reduce-only close into a
        rejected oversized order, and both failures are worse than trading a hair
        less than asked.

        Decimal, not float. `math.floor(0.0059 / 0.0001)` is 58, not 59, because
        the division lands on 58.99999999999999 — which silently shipped 0.0058
        against BTCUSDT's real 0.0001 step, 1.7% less than asked. Every quantity on
        this venue goes through here, so the error was on every order.
        """
        if step and step > 0:
            d = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(
                rounding=ROUND_DOWN)
            return float(d * Decimal(str(step)))
        return round(value, precision)

    async def _fmt_qty(self, symbol: str, qty) -> str:
        spec = await self._get_spec(symbol)
        if not spec:
            logger.warning(f"BinanceAdapter: no spec for {symbol}, sending quantity unrounded")
            return f"{float(qty):f}".rstrip("0").rstrip(".")
        v = self._round_step(float(qty), spec["stepSize"], spec["quantityPrecision"])
        return f"{v:.{spec['quantityPrecision']}f}".rstrip("0").rstrip(".") or "0"

    async def _fmt_price(self, symbol: str, price) -> str:
        spec = await self._get_spec(symbol)
        if not spec:
            return f"{float(price)}"
        tick = spec["tickSize"]
        v = float(price)
        if tick and tick > 0:
            # Prices round to NEAREST tick — unlike quantity there is no safe
            # direction, and Binance rejects a non-multiple outright. Decimal for
            # the same reason as _round_step: float division misses the boundary.
            t = Decimal(str(tick))
            v = float((Decimal(str(v)) / t).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * t)
        return f"{v:.{spec['pricePrecision']}f}".rstrip("0").rstrip(".") or "0"

    # ── account preconditions ──────────────────────────────────────────────────

    async def _check_position_mode(self) -> Optional[str]:
        """Return an error string if the account is in hedge mode, else None.

        Everything above this adapter models one net position per symbol. In hedge
        mode Binance rejects `reduceOnly` and requires `positionSide`, so a close
        would fail and — worse — an entry could open a second, opposite leg. Only
        checked once per adapter instance; it is an account-level setting.
        """
        if self._position_mode_checked:
            return None
        try:
            data = await self._private("GET", "/fapi/v1/positionSide/dual")
            if isinstance(data, dict) and data.get("dualSidePosition") is True:
                return ("account is in Hedge Mode; this platform requires One-way Mode "
                        "(set it in Binance Futures > Preferences > Position Mode)")
            self._position_mode_checked = True
        except Exception as e:
            # Do not block trading on a failed preflight read.
            logger.warning(f"BinanceAdapter: position-mode check failed (continuing): {e}")
        return None

    async def _set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            body = await self._private("POST", "/fapi/v1/leverage",
                                       {"symbol": symbol, "leverage": leverage})
            err = self._err(body)
            if err:
                logger.warning(f"BinanceAdapter: set leverage failed for {symbol}: {err}")
            else:
                logger.info(f"BinanceAdapter: leverage set to {leverage}x for {symbol}")
        except Exception as e:
            logger.warning(f"BinanceAdapter: set-leverage error for {symbol}: {e}")

    async def _set_margin_type(self, symbol: str, margin_mode: str) -> None:
        """ISOLATED / CROSSED. Binance errors instead of no-opping when the mode is
        already what you asked for; that is not a failure here."""
        wire = "ISOLATED" if (margin_mode or "isolated").lower() == "isolated" else "CROSSED"
        try:
            body = await self._private("POST", "/fapi/v1/marginType",
                                       {"symbol": symbol, "marginType": wire})
            code = body.get("code") if isinstance(body, dict) else None
            if code is not None and int(code) < 0 and int(code) not in _ALREADY_SET_CODES:
                logger.warning(f"BinanceAdapter: set marginType failed for {symbol}: {body.get('msg')}")
        except ExchangeUnavailableError as e:
            # -4046 "No need to change margin type" arrives as HTTP 400.
            if any(str(c) in str(e) for c in _ALREADY_SET_CODES):
                return
            logger.warning(f"BinanceAdapter: set-marginType error for {symbol}: {e}")
        except Exception as e:
            logger.warning(f"BinanceAdapter: set-marginType error for {symbol}: {e}")

    # ── reads used for fills / fees ────────────────────────────────────────────

    async def _get_order(self, symbol: str, order_id: str) -> dict:
        try:
            body = await self._private("GET", "/fapi/v1/order",
                                       {"symbol": symbol, "orderId": order_id})
            return body if isinstance(body, dict) and not self._err(body) else {}
        except Exception as e:
            logger.warning(f"BinanceAdapter._get_order({symbol},{order_id}) failed: {e}")
            return {}

    async def _get_trades(self, symbol: str, order_id: str | None = None,
                          since_ms: int | None = None) -> list[dict]:
        params: dict = {"symbol": symbol, "limit": 1000}
        if order_id is not None:
            params["orderId"] = order_id
        if since_ms is not None:
            params["startTime"] = since_ms
        try:
            body = await self._private("GET", "/fapi/v1/userTrades", params)
            return body if isinstance(body, list) else []
        except Exception as e:
            logger.warning(f"BinanceAdapter._get_trades({symbol}) failed: {e}")
            return []

    @staticmethod
    def _aggregate_trades(trades: list[dict]) -> dict:
        """Volume-weighted fill price plus summed size, gross pnl and fee.

        Binance's per-trade `realizedPnl` excludes commission, so summing it gives
        the GROSS figure the base contract asks for, and `commission` is reported
        positive-means-paid — the same convention the DB uses.
        """
        qty = sum(float(t.get("qty") or 0) for t in trades)
        if qty <= 0:
            return {}
        notional = sum(float(t.get("price") or 0) * float(t.get("qty") or 0) for t in trades)
        return {
            "price": Decimal(str(notional / qty)),
            "size":  Decimal(str(qty)),
            "pnl":   sum(Decimal(str(t.get("realizedPnl") or 0)) for t in trades),
            "fee":   sum(Decimal(str(t.get("commission") or 0)) for t in trades),
        }

    async def get_order_fill_fee(self, symbol: str, order_id: str) -> Optional[Decimal]:
        """Fee for a fill detected asynchronously (the reconciler picking up a
        resting limit), where no synchronous fee was ever returned."""
        try:
            trades = await self._get_trades(symbol, order_id=order_id)
            if not trades:
                return None
            return sum(Decimal(str(t.get("commission") or 0)) for t in trades)
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_order_fill_fee failed for {order_id}: {e}")
            return None

    # ── market data / risk ─────────────────────────────────────────────────────

    async def _get_brackets(self, symbol: str) -> list:
        cache = BinanceAdapter._brackets.setdefault(self.base_url, {})
        ts    = BinanceAdapter._brackets_ts.setdefault(self.base_url, {})
        if symbol not in cache or (time.time() - ts.get(symbol, 0)) > _BRACKET_TTL:
            try:
                body = await self._private("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
                entries = body if isinstance(body, list) else []
                for e in entries:
                    if e.get("symbol") == symbol and e.get("brackets"):
                        cache[symbol] = e["brackets"]
                        ts[symbol] = time.time()
                        break
            except Exception as e:
                logger.warning(f"BinanceAdapter: leverageBracket refresh failed for {symbol}: {e}")
        return cache.get(symbol, [])

    async def get_max_leverage(self, symbol: str) -> int:
        try:
            brackets = await self._get_brackets(symbol)
            if not brackets:
                return 0
            # Bracket 1 (smallest notional) carries the headline max leverage.
            return int(max(float(b.get("initialLeverage") or 0) for b in brackets))
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_max_leverage({symbol}) failed: {e}")
            return 0

    async def get_maintenance_margin_rate(
        self, symbol: str, notional: float, margin_mode: str = "isolated"
    ) -> Optional[float]:
        """Tier-aware MMR from Binance's own leverage brackets, plus the shared
        conservatism buffer. Returns None when unknown — callers must fall back to
        a conservative static value, never treat None as zero."""
        try:
            brackets = await self._get_brackets(symbol)
            if not brackets:
                return None
            applicable = [
                b for b in brackets
                if float(b.get("notionalFloor") or 0) <= notional
            ]
            # Brackets ascend by notionalFloor; the last one at or below the
            # position's notional is the tier it sits in.
            applicable.sort(key=lambda b: float(b.get("notionalFloor") or 0))
            tier = applicable[-1] if applicable else brackets[0]
            mmr = float(tier.get("maintMarginRatio") or 0)
            if mmr <= 0:
                return None
            return mmr + MMR_CONSERVATISM_BUFFER
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_maintenance_margin_rate({symbol}) failed: {e}")
            return None

    async def get_mark_price(self, symbol: str) -> float | None:
        try:
            data = await self._public("/fapi/v1/premiumIndex", {"symbol": symbol})
            if isinstance(data, list):
                data = data[0] if data else {}
            mark = float(data.get("markPrice") or 0)
            return mark if mark > 0 else None
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_mark_price({symbol}) failed: {e}")
            return None

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 300, end_ms: int | None = None
    ) -> list[dict]:
        """See ExchangeAdapter.get_candles. Klines come back oldest-first as
        `[openTime, o, h, l, c, volume, closeTime, ...]`, and `endTime` windows the
        series on a past moment. The dash is stripped so a canonical BTC-USDT and a
        Binance-native BTCUSDT both work."""
        if timeframe not in CANDLE_BAR_SECONDS:
            logger.warning(f"BinanceAdapter.get_candles: unsupported timeframe {timeframe!r}")
            return []
        try:
            params: dict = {
                "symbol":   symbol.replace("-", ""),
                "interval": timeframe,
                "limit":    min(max(1, limit), _KLINES_MAX_PER_CALL),
            }
            if end_ms:
                params["endTime"] = int(end_ms)
            rows = await self._public("/fapi/v1/klines", params)
            if not isinstance(rows, list):
                return []
            return [
                {
                    "time":   int(r[0]),
                    "open":   float(r[1]),
                    "high":   float(r[2]),
                    "low":    float(r[3]),
                    "close":  float(r[4]),
                    "volume": float(r[5]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_candles({symbol},{timeframe}) failed: {e}")
            return []

    # ── orders ─────────────────────────────────────────────────────────────────

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        try:
            hedge_err = await self._check_position_mode()
            if hedge_err:
                return OrderResult(success=False, status="rejected", error_msg=hedge_err)

            margin_mode = order.margin_mode or "isolated"
            leverage    = order.leverage or 10

            max_lev = await self.get_max_leverage(order.symbol)
            if max_lev and leverage > max_lev:
                # Reject rather than clamp, matching Blofin: silently trading at a
                # different leverage than the strategy sized for is worse than not
                # trading.
                msg = (f"Requested leverage {leverage}x exceeds Binance max "
                       f"{max_lev}x for {order.symbol}")
                logger.warning(f"BinanceAdapter: {msg}")
                return OrderResult(success=False, status="rejected", error_msg=msg)

            await self._set_margin_type(order.symbol, margin_mode)
            await self._set_leverage(order.symbol, leverage)

            is_close = order.signal in ("close_long", "close_short")
            qty = await self._fmt_qty(order.symbol, order.size)
            if float(qty) <= 0:
                return OrderResult(success=False, status="rejected",
                                   error_msg=f"quantity rounds to zero at {order.symbol} step size")

            params: dict = {"symbol": order.symbol, "side": order.side.upper(), "quantity": qty,
                            "newOrderRespType": "RESULT"}

            if order.order_type == "market":
                params["type"] = "MARKET"
            else:
                params["type"] = "LIMIT"
                params["price"] = await self._fmt_price(order.symbol, order.price)
                # Entry limits go out post-only (GTX): a taker fill would land at
                # market, at or beyond the stop computed from the intended limit
                # price. Closes keep GTC — a taker fill is fine when exiting.
                params["timeInForce"] = "GTC" if is_close else "GTX"

            if is_close:
                params["reduceOnly"] = "true"

            body = await self._private("POST", "/fapi/v1/order", params)
            err = self._err(body)
            if err:
                return OrderResult(success=False, status="rejected",
                                   error_msg=f"Binance rejected order: {err}",
                                   raw_response=body if isinstance(body, dict) else None)

            exchange_order_id = str(body.get("orderId"))
            status_raw = body.get("status")

            fill_price = fill_size = pnl = fee = None
            order_status = "filled"

            if status_raw in ("NEW", "PARTIALLY_FILLED"):
                order_status = "pending"
                if status_raw == "PARTIALLY_FILLED":
                    agg = self._aggregate_trades(await self._get_trades(order.symbol, exchange_order_id))
                    fill_price, fill_size = agg.get("price"), agg.get("size")
            elif status_raw == "EXPIRED":
                # A GTX entry the book had already moved through. Binance accepts
                # then expires it, so this must surface as rejected: no position
                # exists, and reporting it filled would invent one.
                return OrderResult(
                    success=False, status="rejected", exchange_order_id=exchange_order_id,
                    error_msg=("post-only limit expired by exchange: price already through "
                               "the limit (would have filled as taker)"),
                    raw_response=body if isinstance(body, dict) else None,
                )
            else:
                # FILLED (or gone from the book). Trades carry the authoritative
                # price, pnl and fee; the order body's avgPrice is a fallback.
                await asyncio.sleep(1.0)
                agg = self._aggregate_trades(await self._get_trades(order.symbol, exchange_order_id))
                if agg:
                    fill_price, fill_size = agg["price"], agg["size"]
                    pnl, fee = agg["pnl"], agg["fee"]
                else:
                    avg = float(body.get("avgPrice") or 0)
                    fill_price = Decimal(str(avg)) if avg > 0 else None
                    ex = float(body.get("executedQty") or 0)
                    fill_size = Decimal(str(ex)) if ex > 0 else None

            result = OrderResult(
                success=True,
                status=order_status,
                exchange_order_id=exchange_order_id,
                raw_response=body if isinstance(body, dict) else None,
                actual_fill_price=fill_price,
                actual_fill_size=fill_size,
                realized_pnl=pnl,
                fee=fee,
            )

            # Protection is a separate order on Binance. Attach it after the entry
            # exists, and say so loudly if it does not land — an unprotected filled
            # entry must never read as a clean success.
            if not is_close and (order.tp_price or order.sl_price):
                trigger_side = "sell" if order.side.lower() == "buy" else "buy"
                trig = await self.place_trigger_orders(
                    order.symbol, trigger_side, float(order.size),
                    tp_price=float(order.tp_price) if order.tp_price else None,
                    sl_price=float(order.sl_price) if order.sl_price else None,
                )
                if not trig.get("success"):
                    logger.error(
                        f"BinanceAdapter: entry {exchange_order_id} for {order.symbol} is "
                        f"FILLED BUT UNPROTECTED — trigger placement failed: {trig}"
                    )
                    result.error_msg = (
                        f"entry placed but stop/target did NOT attach: {trig.get('error') or trig.get('placed')}"
                    )
            return result

        except Exception as e:
            logger.error(f"BinanceAdapter.submit_order failed: {e}")
            return OrderResult(success=False, status="route_failed", error_msg=str(e))

    async def get_open_positions(self) -> List[Position]:
        """Raises ExchangeUnavailableError on a failed read — callers must never
        read a transport failure as 'no positions'."""
        last_err: Exception | None = None
        # v3 is current; v2 is kept as a fallback so a version retirement on either
        # side degrades to the other rather than to "flat".
        for path in ("/fapi/v3/positionRisk", "/fapi/v2/positionRisk"):
            try:
                body = await self._private("GET", path)
                entries = body if isinstance(body, list) else []
                out: List[Position] = []
                for p in entries:
                    amt = float(p.get("positionAmt") or 0)
                    if amt == 0:
                        continue
                    entry = p.get("entryPrice") or p.get("avgPrice") or "0"
                    liq = p.get("liquidationPrice")
                    mark = p.get("markPrice") or p.get("marketPrice")
                    upnl = p.get("unRealizedProfit") or p.get("unrealizedProfit") or "0"
                    out.append(Position(
                        symbol=p.get("symbol"),
                        side="long" if amt > 0 else "short",
                        size=Decimal(str(abs(amt))),
                        entry_price=Decimal(str(entry)),
                        leverage=int(float(p.get("leverage") or 10)),
                        mark_price=Decimal(str(mark)) if mark else None,
                        unrealized_pnl=Decimal(str(upnl)),
                        liquidation_price=(
                            Decimal(str(liq)) if liq and float(liq) > 0 else None
                        ),
                    ))
                return out
            except Exception as e:
                last_err = e
                logger.warning(f"BinanceAdapter: {path} failed: {e}")
        raise ExchangeUnavailableError(f"binance positionRisk unavailable: {last_err}")

    async def close_position(self, symbol: str, side: str, size=None,
                             margin_mode: str = "isolated") -> OrderResult:
        """Full close (size=None) or partial reduce. Both are reduce-only market
        orders — there is no separate close endpoint to go wrong, which is why this
        needs none of the fill-recovery machinery the Blofin adapter carries."""
        try:
            reduce_side = "SELL" if side == "long" else "BUY"

            if size is None:
                positions = await self.get_open_positions()
                match = next((p for p in positions if p.symbol == symbol and p.side == side), None)
                if match is None:
                    return OrderResult(success=False, status="rejected",
                                       error_msg=f"no open {side} position for {symbol}")
                close_qty = match.size
            else:
                close_qty = Decimal(str(size))

            qty = await self._fmt_qty(symbol, close_qty)
            if float(qty) <= 0:
                return OrderResult(success=False, status="rejected",
                                   error_msg=f"close quantity rounds to zero at {symbol} step size")

            body = await self._private("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": reduce_side, "type": "MARKET",
                "quantity": qty, "reduceOnly": "true", "newOrderRespType": "RESULT",
            })
            err = self._err(body)
            if err:
                return OrderResult(success=False, status="rejected",
                                   error_msg=f"Binance rejected close: {err}",
                                   raw_response=body if isinstance(body, dict) else None)

            exchange_order_id = str(body.get("orderId"))
            await asyncio.sleep(1.0)
            agg = self._aggregate_trades(await self._get_trades(symbol, exchange_order_id))

            return OrderResult(
                success=True,
                status="filled",
                exchange_order_id=exchange_order_id,
                raw_response=body if isinstance(body, dict) else None,
                actual_fill_price=agg.get("price"),
                actual_fill_size=agg.get("size"),
                realized_pnl=agg.get("pnl"),
                fee=agg.get("fee"),
            )
        except Exception as e:
            logger.error(f"BinanceAdapter.close_position failed: {e}")
            return OrderResult(success=False, status="route_failed", error_msg=str(e))

    # ── account ────────────────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        """Never raises — an empty/zeroed dict with `error` is the failure shape."""
        try:
            body = await self._private("GET", "/fapi/v2/account")
            err = self._err(body)
            if err:
                return {"total_balance": 0.0, "available_balance": 0.0, "used_margin": 0.0,
                        "currency": "USDT", "error": f"Binance API error {err}"}
            total     = float(body.get("totalMarginBalance") or body.get("totalWalletBalance") or 0)
            available = float(body.get("availableBalance") or 0)
            used      = float(body.get("totalInitialMargin") or max(total - available, 0))
            return {
                "total_balance":     total,
                "available_balance": available,
                "used_margin":       max(used, 0),
                "currency":          "USDT",
            }
        except Exception as e:
            logger.error(f"BinanceAdapter.get_balance failed: {e}")
            return {"total_balance": 0.0, "available_balance": 0.0, "used_margin": 0.0,
                    "currency": "USDT", "error": str(e)}

    async def list_instruments(self) -> list[str]:
        try:
            await self._get_spec("")   # populates the cache
            return sorted(BinanceAdapter._exchange_info.get(self.base_url, {}).keys())
        except Exception as e:
            logger.error(f"BinanceAdapter.list_instruments failed: {e}")
            return []

    async def get_min_order_size(self, symbol: str) -> float:
        try:
            spec = await self._get_spec(symbol)
            return float(spec.get("minQty") or 0.0) if spec else 0.0
        except Exception as e:
            logger.warning(f"BinanceAdapter.get_min_order_size({symbol}) failed: {e}")
            return 0.0

    async def get_instrument_specs(self) -> dict:
        try:
            if not BinanceAdapter._exchange_info.get(self.base_url):
                await self._refresh_exchange_info()
            out = {}
            for sym, spec in BinanceAdapter._exchange_info.get(self.base_url, {}).items():
                step = spec.get("stepSize") or 0
                if step and step < 1:
                    size_dp = max(0, -int(math.floor(math.log10(step + 1e-15))))
                else:
                    size_dp = 0
                out[sym] = {
                    "price": {"mode": "tick", "tick": spec.get("tickSize") or 0.01},
                    "size":  {"dp": size_dp},
                }
            return out
        except Exception as e:
            logger.error(f"BinanceAdapter.get_instrument_specs failed: {e}")
            return {}

    async def get_account_meta(self) -> dict:
        """Public metadata only — never the secret."""
        try:
            return {
                "api_key":      self.api_key,
                "account_type": "futures",
                "exchange":     "binance",
            }
        except Exception as e:
            logger.error(f"BinanceAdapter.get_account_meta failed: {e}")
            return {}

    async def get_closed_position_details(
        self, symbol: str, since_ms: int | None = None, side: str | None = None
    ) -> dict | None:
        # `side` is interface parity only: Binance accounts are refused at
        # credential validation unless they are in one-way mode, so a symbol
        # never has two legs to choose between here.
        """Most recent close for `symbol`, reconstructed from user trades.

        `realizedPnl` on a Binance trade excludes commission, so summing it gives
        the GROSS figure the contract asks for, and the commissions summed here
        cover the CLOSING legs only — hence fee_scope 'close_only', the same as
        Hyperliquid. A trade is a closing leg when it moved realized PnL.
        """
        try:
            trades = await self._get_trades(symbol, since_ms=since_ms)
            closing = [t for t in trades if float(t.get("realizedPnl") or 0) != 0]
            if not closing:
                return None
            closing.sort(key=lambda t: int(t.get("time") or 0))
            agg = self._aggregate_trades(closing)
            if not agg:
                return None

            latest_ms = int(closing[-1].get("time") or 0)
            from datetime import datetime, timezone as tz
            closed_at = datetime.fromtimestamp(latest_ms / 1000, tz=tz.utc) if latest_ms else None

            is_liquidation = await self._was_liquidated(symbol, since_ms or latest_ms)

            return {
                "close_reason":  "Liquidated" if is_liquidation else "Closed on exchange",
                "closing_price": agg["price"],
                "pnl_realized":  agg["pnl"],
                "fee":           abs(agg["fee"]),
                "fee_scope":     "close_only",
                "closed_at":     closed_at,
                "raw":           closing,
            }
        except Exception as e:
            logger.error(f"BinanceAdapter.get_closed_position_details failed: {e}")
            return None

    async def _was_liquidated(self, symbol: str, since_ms: int) -> bool:
        """Binance records a forced close as an order whose type is a liquidation
        or ADL variant. Best-effort: a wrong answer only mislabels close_reason."""
        try:
            body = await self._private("GET", "/fapi/v1/allOrders",
                                       {"symbol": symbol, "startTime": max(since_ms - 1000, 0),
                                        "limit": 50})
            for o in (body if isinstance(body, list) else []):
                blob = f"{o.get('type')} {o.get('origType')} {o.get('clientOrderId')}".upper()
                if "LIQUIDATION" in blob or "ADL" in blob or "AUTOCLOSE" in blob:
                    return True
        except Exception as e:
            logger.warning(f"BinanceAdapter._was_liquidated({symbol}) failed: {e}")
        return False

    # ── resting orders ─────────────────────────────────────────────────────────

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Resting non-trigger limit orders only. Binance returns triggers from the
        same endpoint, so they are filtered out here — they belong to
        list_trigger_orders by the base contract."""
        try:
            body = await self._private("GET", "/fapi/v1/openOrders",
                                       {"symbol": symbol} if symbol else {})
            out = []
            for o in (body if isinstance(body, list) else []):
                if (o.get("type") or "").upper() in _TRIGGER_TYPES:
                    continue
                executed = float(o.get("executedQty") or 0)
                out.append({
                    "order_id":      str(o.get("orderId")),
                    "symbol":        o.get("symbol"),
                    "side":          (o.get("side") or "").lower(),
                    "price":         float(o.get("price") or 0),
                    "size":          float(o.get("origQty") or 0),
                    "filled_size":   executed,
                    "status":        "partially_filled" if executed > 0 else "resting",
                    "created_at_ms": int(o.get("time") or o.get("updateTime") or 0),
                })
            return out
        except Exception as e:
            logger.error(f"BinanceAdapter.get_open_orders failed: {e}")
            return []

    async def amend_order(self, symbol: str, order_id: str,
                          new_price: float | None = None,
                          new_size: float | None = None) -> dict:
        """Native amend via PUT /fapi/v1/order.

        Unlike Blofin — where amend is cancel-then-replace and can destroy the
        order if the replacement fails — this is atomic: a rejected amend leaves
        the original resting untouched. Binance supports it for LIMIT orders only,
        and requires side, quantity and price together, so the unchanged one is
        read back from the live order.
        """
        try:
            if new_price is None and new_size is None:
                return {"success": False, "error": "amend_order requires new_price or new_size"}

            existing = await self._get_order(symbol, order_id)
            if not existing:
                return {"success": False, "error": f"order {order_id} not found"}
            if (existing.get("type") or "").upper() != "LIMIT":
                return {"success": False,
                        "error": f"Binance can only amend LIMIT orders, this is {existing.get('type')}"}

            price = new_price if new_price is not None else float(existing.get("price") or 0)
            size  = new_size  if new_size  is not None else float(existing.get("origQty") or 0)

            body = await self._private("PUT", "/fapi/v1/order", {
                "symbol":   symbol,
                "orderId":  order_id,
                "side":     (existing.get("side") or "").upper(),
                "quantity": await self._fmt_qty(symbol, size),
                "price":    await self._fmt_price(symbol, price),
            })
            err = self._err(body)
            if err:
                return {"success": False, "error": err, "raw_response": body}
            return {
                "success": True,
                "order_id": str(body.get("orderId")),
                "raw_response": body,
            }
        except Exception as e:
            logger.error(f"BinanceAdapter.amend_order failed: {e}")
            return {"success": False, "error": str(e)}

    # ── trigger orders (TP/SL) ─────────────────────────────────────────────────

    async def list_trigger_orders(
        self, symbol: str, position_side: Optional[str] = None
    ) -> Optional[list[dict]]:
        # position_side: interface parity only — one-way accounts only (see
        # _check_position_mode), so there is never a second leg to filter out.
        """Pending TP/SL orders as {oid, tpsl, triggerPx, sz}.

        Returns None — never [] — on a failed read: callers treat [] as "confirmed
        no protection" and would cancel-then-replace on a lie.
        """
        try:
            body = await self._private("GET", "/fapi/v1/openOrders", {"symbol": symbol})
            if not isinstance(body, list):
                return None
            out = []
            for o in body:
                otype = (o.get("type") or "").upper()
                if otype not in _TRIGGER_TYPES:
                    continue
                tpsl = "tp" if "TAKE_PROFIT" in otype else "sl"
                # closePosition triggers carry origQty 0 — they cover whatever the
                # position is. Report the position-tracking marker honestly rather
                # than a fake size.
                sz = o.get("origQty")
                if o.get("closePosition") is True:
                    sz = "position"
                out.append({
                    "oid":       str(o.get("orderId")),
                    "tpsl":      tpsl,
                    "triggerPx": o.get("stopPrice"),
                    "sz":        sz,
                })
            return out
        except Exception as e:
            logger.error(f"BinanceAdapter.list_trigger_orders failed: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        try:
            body = await self._private("DELETE", "/fapi/v1/order",
                                       {"symbol": symbol, "orderId": order_id})
            err = self._err(body)
            if err:
                return {"success": False, "error": err}
            return {"success": True, "oid": str(order_id)}
        except Exception as e:
            # -2011 "Unknown order sent" means it is already gone, which is the
            # state the caller wanted.
            if "-2011" in str(e):
                return {"success": True, "oid": str(order_id), "note": "already gone"}
            logger.error(f"BinanceAdapter.cancel_order failed: {e}")
            return {"success": False, "error": str(e)}

    async def place_trigger_orders(
        self,
        symbol: str,
        trigger_side: str,
        size: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        position_side: Optional[str] = None,
    ) -> dict:
        """Standalone TP/SL for an existing position.

        `position_side` is interface parity only — one-way accounts only.

        `size` is accepted for interface parity but deliberately not sent: these go
        out with `closePosition=true`, so each trigger covers the whole position
        however it changes. That means a partial close leaves the remaining stop
        correctly sized with no re-issue, and the exchange cancels both legs when
        the position goes flat — neither of which the size-carrying triggers on the
        other two venues give for free.
        """
        try:
            placed = []
            for tpsl, price, otype in (
                ("tp", tp_price, "TAKE_PROFIT_MARKET"),
                ("sl", sl_price, "STOP_MARKET"),
            ):
                if price is None:
                    continue
                params = {
                    "symbol":        symbol,
                    "side":          trigger_side.upper(),
                    "type":          otype,
                    "stopPrice":     await self._fmt_price(symbol, price),
                    "closePosition": "true",
                    "workingType":   "MARK_PRICE",   # match the price the liquidation engine uses
                    "priceProtect":  "true",
                }
                try:
                    body = await self._private("POST", "/fapi/v1/order", params)
                    err = self._err(body)
                except Exception as e:
                    err, body = str(e), None
                if err:
                    placed.append({"tpsl": tpsl, "error": err})
                    logger.warning(f"Binance trigger ({tpsl}) failed for {symbol}: {err}")
                else:
                    oid = str(body.get("orderId"))
                    placed.append({"tpsl": tpsl, "oid": oid, "status": "placed"})
                    logger.info(f"Binance trigger ({tpsl}) placed at {params['stopPrice']} "
                                f"for {symbol}, orderId={oid}")

            success = bool(placed) and not any("error" in p for p in placed)
            if not success:
                logger.warning(f"Binance place_trigger_orders({symbol}) PARTIAL/FAILED: {placed}")
            return {"success": success, "placed": placed}
        except Exception as e:
            logger.error(f"BinanceAdapter.place_trigger_orders failed: {e}")
            return {"success": False, "error": str(e)}
