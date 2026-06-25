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
