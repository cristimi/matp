"""
Strategy Tester Service — FastAPI application entry point.
"""

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.database import init_db, get_pool
from app.api.strategies import router as strategies_router
from app.api.debug import router as debug_router
from app.api.runs import router as runs_router
from app.api.estimate import router as estimate_router
from app.api.results import router as results_router
from app.api.migrate import router as migrate_router
from app.engine.backtest_engine import init_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_VENDORED_DIR = Path(__file__).parent / "_vendored"


def _verify_checksums() -> None:
    checksums_file = _VENDORED_DIR / "CHECKSUMS"
    if not checksums_file.exists():
        raise RuntimeError("_vendored/CHECKSUMS missing — run `make sync-vendored`")

    recorded: dict[str, str] = {}
    for line in checksums_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            recorded[parts[1].strip()] = parts[0].strip()

    for filename, expected_hash in recorded.items():
        fpath = _VENDORED_DIR / filename
        if not fpath.exists():
            raise RuntimeError(f"Vendored file missing: {filename}")
        actual = hashlib.sha256(fpath.read_bytes()).hexdigest()
        if actual != expected_hash:
            raise RuntimeError(
                f"Vendored file checksum mismatch: {filename} — "
                "upstream may have changed without running `make sync-vendored`"
            )

    logger.info("Vendored checksums verified OK (%d files)", len(recorded))


async def _cleanup_ohlcv_cache(pool) -> None:
    async with pool.acquire() as conn:
        status_hourly = await conn.execute(
            """
            DELETE FROM tester.ohlcv_cache
            WHERE timeframe IN ('1m','3m','5m','15m','30m','1h','2h','4h','8h')
              AND fetched_at < NOW() - INTERVAL '7 days'
            """
        )
        status_daily = await conn.execute(
            """
            DELETE FROM tester.ohlcv_cache
            WHERE timeframe IN ('1d')
              AND fetched_at < NOW() - INTERVAL '30 days'
            """
        )
    logger.info(
        "OHLCV cache cleanup: %s short-tf rows, %s daily rows",
        status_hourly, status_daily,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Connect with search_path init hook
    await init_db()
    pool = get_pool()

    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(pool, settings)

    # 2. Verify tester schema exists
    schema_exists = await pool.fetchval(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'tester'"
    )
    if not schema_exists:
        raise RuntimeError(
            "tester schema not found in database — "
            "run: docker compose exec postgres psql -U matp -d matp "
            "-f /path/to/db/migrations/011_tester_schema.sql"
        )
    logger.info("tester schema verified")

    # 3. Verify vendored checksums (defence-in-depth)
    _verify_checksums()

    # 4. OHLCV cache cleanup
    await _cleanup_ohlcv_cache(pool)

    # 5. Run semaphore + engine init
    sem = asyncio.Semaphore(settings.tester_max_concurrent_runs)
    app.state.run_semaphore = sem
    init_engine(sem)
    logger.info(
        "Run semaphore initialized (max_concurrent=%d)",
        settings.tester_max_concurrent_runs,
    )

    yield

    logger.info("Strategy Tester shutdown complete")


app = FastAPI(
    title="MATP Strategy Tester",
    version="1.0.0",
    lifespan=lifespan,
)


app.include_router(strategies_router, prefix="/strategies")
app.include_router(runs_router,       prefix="/runs")
app.include_router(results_router,    prefix="/runs")
app.include_router(estimate_router,   prefix="")
app.include_router(migrate_router,    prefix="")
app.include_router(debug_router,      prefix="/debug")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "strategy-tester"}
