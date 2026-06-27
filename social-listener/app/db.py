import json
import logging

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    log.info("DB pool initialized")


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def already_seen(channel_msg_id: int) -> bool:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT 1 FROM public.social_signal_log WHERE source=$1 AND channel_msg_id=$2",
            settings.source_tag, channel_msg_id,
        )
        return row is not None


async def already_shadow_evaluated(channel_msg_id: int) -> bool:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT 1 FROM public.social_shadow_orders WHERE source=$1 AND channel_msg_id=$2",
            settings.source_tag, channel_msg_id,
        )
        return row is not None


async def load_signal(channel_msg_id: int) -> dict | None:
    """Load an already-extracted record from social_signal_log for brain replay."""
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT channel_msg_id, posted_at, is_actionable, action_type,
                      asset, direction, reference_price, confidence
               FROM public.social_signal_log
               WHERE source=$1 AND channel_msg_id=$2""",
            settings.source_tag, channel_msg_id,
        )
        if row is None:
            return None
        return dict(row)


async def get_state(asset: str) -> str:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT state FROM public.social_position_state WHERE source=$1 AND asset=$2",
            settings.source_tag, asset,
        )
        return row["state"] if row else "FLAT"


async def set_state(asset: str, state: str, msg_id: int) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_position_state (source, asset, state, last_msg_id, updated_at)
               VALUES ($1,$2,$3,$4, now())
               ON CONFLICT (source, asset) DO UPDATE SET state=$3, last_msg_id=$4, updated_at=now()""",
            settings.source_tag, asset, state, msg_id,
        )


async def insert_shadow_order(rec: dict) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_shadow_orders
               (source, channel_msg_id, posted_at, phase, asset, action_type, from_state, to_state,
                intended_signal, reference_price, mark_price, confidence, decision, reason, mode)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
               ON CONFLICT (source, channel_msg_id) DO NOTHING""",
            settings.source_tag, rec["channel_msg_id"], rec["posted_at"], rec["phase"], rec["asset"],
            rec["action_type"], rec["from_state"], rec["to_state"], rec["intended_signal"],
            rec["reference_price"], rec["mark_price"], rec["confidence"], rec["decision"],
            rec["reason"], "shadow",
        )


async def insert_signal(rec: dict) -> bool:
    """Insert one parsed record. Returns True if a NEW row was written, False if duplicate."""
    async with pool().acquire() as c:
        result = await c.execute(
            """
            INSERT INTO public.social_signal_log
              (source, channel_msg_id, posted_at, raw_text, preview_text, x_url,
               is_actionable, action_type, asset, direction, reference_price,
               confidence, in_whitelist, model, extractor_version, raw_llm_json)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (source, channel_msg_id) DO NOTHING
            """,
            settings.source_tag, rec["channel_msg_id"], rec["posted_at"],
            rec["raw_text"], rec["preview_text"], rec["x_url"],
            rec["is_actionable"], rec["action_type"], rec["asset"],
            rec["direction"], rec["reference_price"], rec["confidence"],
            rec["in_whitelist"], rec["model"], rec["extractor_version"],
            json.dumps(rec["raw_llm_json"]),
        )
        return result.endswith("1")
