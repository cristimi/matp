import asyncio
import logging
import sys

import ccxt
import ccxt.pro as ccxtpro
import redis.asyncio as aioredis

from app.config import settings
from app.exchange import make_pro_exchange, make_rest_exchange, resolve_symbol
from app.ingestor import Ingestor
from app.redis_store import RedisStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def startup_check(exchange_id: str) -> None:
    """Assert ccxt.pro supports watchOHLCV for the configured exchange."""
    logger.info("ccxt version: %s", ccxt.__version__)
    ex = make_pro_exchange(exchange_id)
    try:
        has_watch = ex.has.get("watchOHLCV")
        if not has_watch:
            logger.critical(
                "FATAL: ccxt.pro.%s does not support watchOHLCV "
                "(has['watchOHLCV']=%s). Upgrade ccxt or choose a different exchange.",
                exchange_id, has_watch,
            )
            sys.exit(1)
        logger.info(
            "Startup check passed: ccxt=%s exchange=%s watchOHLCV=%s",
            ccxt.__version__, exchange_id, has_watch,
        )
    finally:
        await ex.close()


async def main() -> None:
    await startup_check(settings.ingestion_exchange)

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    store = RedisStore(redis_client, settings.ingestion_exchange)

    # Single ccxt.pro instance — it multiplexes subscriptions over one WS connection
    pro_exchange = make_pro_exchange(settings.ingestion_exchange)
    rest_exchange = make_rest_exchange(settings.ingestion_exchange)

    try:
        await rest_exchange.load_markets()

        ingestors: list[Ingestor] = []
        for canonical_symbol, timeframe in settings.subscriptions:
            ccxt_sym = await resolve_symbol(rest_exchange, canonical_symbol)
            ingestors.append(
                Ingestor(
                    exchange_id=settings.ingestion_exchange,
                    canonical_symbol=canonical_symbol,
                    ccxt_symbol=ccxt_sym,
                    timeframe=timeframe,
                    warmup_candles=settings.ingestion_warmup_candles,
                    store=store,
                )
            )

        # REST warmup for all subscriptions
        await asyncio.gather(*[i.warmup(rest_exchange) for i in ingestors])
        await rest_exchange.close()

        logger.info("Starting %d watch loop(s) for exchange=%s", len(ingestors), settings.ingestion_exchange)
        await asyncio.gather(*[i.run(pro_exchange) for i in ingestors])
    finally:
        try:
            await pro_exchange.close()
        except Exception:
            pass
        try:
            await redis_client.aclose()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
