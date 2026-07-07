"""
Liquidation data — DOCUMENTED NO-OP on the currently configured exchanges.

Wave-3 feasibility probe (2026-07-07, in-container, per spec §9's "verify
ccxt has['fetchLiquidations'] on the exchange(s) actually configured before
building"):

    hyperliquid: has['fetchLiquidations'] = False
    blofin:      has['fetchLiquidations'] = None

Neither configured exchange exposes public liquidation data via ccxt, and the
executor's decision (approved) is NOT to wire a paid/fragile aggregator
(Coinglass et al). So this fetcher checks the exchange's static capability
flag and returns None when unsupported — the LIQUIDATIONS section is honestly
absent. The ccxt aggregation path is deliberately NOT implemented: it would be
untestable dead code against the current exchanges. See docs/ROADMAP.md
"Deferred Backlog" for the revisit entry.

The capability check is static (no network call — ccxt `has` is a class-level
map), so a mistakenly enabled toggle costs nothing per cycle.
"""

import logging

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)


async def fetch_liquidations(
    exchange_id: str,
    symbol: str,
    window_hours: int = 4,
) -> dict | None:
    """
    Return liquidation clusters near current price — currently always None:
    no configured exchange supports ccxt fetch_liquidations (see module
    docstring). Kept async with the specced signature so a future supporting
    exchange only needs the aggregation body filled in.
    """
    try:
        cls = getattr(ccxt_async, exchange_id, None)
        if cls is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        exchange = cls()
        supported = bool(exchange.has.get('fetchLiquidations'))
        try:
            await exchange.close()
        except Exception:
            pass

        if not supported:
            logger.info(
                "fetch_liquidations: %s has no public liquidation endpoint "
                "(ccxt has['fetchLiquidations'] falsy) — returning None", exchange_id,
            )
            return None

        # A supporting exchange is configured — the aggregation is not built
        # yet (deliberately, see module docstring / ROADMAP). Stay honest.
        logger.warning(
            "fetch_liquidations: %s reports fetchLiquidations support but the "
            "aggregation path is not implemented — returning None (see ROADMAP)",
            exchange_id,
        )
        return None

    except Exception as exc:
        logger.warning("fetch_liquidations error [%s %s]: %s", exchange_id, symbol, exc)
        return None


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        for eid in ("hyperliquid", "blofin", "binance"):
            result = await fetch_liquidations(eid, "BTC/USDT")
            print(f"{eid}: {result}")

    asyncio.run(_test())
