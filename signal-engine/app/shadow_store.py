"""Write shadow signals to public.shadow_signals (idempotent)."""
import json
import logging
from datetime import datetime, timezone

import asyncpg

from app.strategies.base import Signal

logger = logging.getLogger(__name__)


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


async def store_shadow_signal(
    pool: asyncpg.Pool,
    strategy_id: str,
    signal_source: str,
    sig: Signal,
    mode: str = "shadow",
) -> None:
    """INSERT into shadow_signals, idempotent on (strategy_id, signal, signal_bar_time)."""
    bar_dt = _ms_to_dt(sig.signal_bar_time)
    bracket_json = json.dumps(sig.bracket_spec)
    fired_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.shadow_signals
                (strategy_id, signal_source, symbol, side, signal,
                 signal_bar_time, bar_close_price, bracket_spec, mode,
                 exit_reason, size_pct, fired_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)
            ON CONFLICT (strategy_id, signal, signal_bar_time, COALESCE(exit_reason, ''))
            DO NOTHING
            """,
            strategy_id,
            signal_source,
            sig.symbol,
            sig.side,
            sig.signal,
            bar_dt,
            sig.bar_close_price,
            bracket_json,
            mode,
            sig.exit_reason,
            sig.size_pct,
            fired_at,
        )
    logger.debug(
        "shadow_store: %s %s bar=%s close=%.2f",
        sig.signal, sig.symbol, bar_dt.isoformat(), sig.bar_close_price,
    )
