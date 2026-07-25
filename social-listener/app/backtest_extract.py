"""Re-extract a window of channel history with the CURRENT extractor.

Read-only with respect to the live tables: it talks to Telegram and the LLM and
writes a JSON dump. Nothing here touches social_signal_log, social_shadow_orders
or social_position_state — a backtest must not mutate live state.

    python -m app.backtest_extract <days> <out_path>
"""
import asyncio
import json
import logging
import sys
import time

from app import db
from app.config import settings
from app.extractor import extract
from app.telegram import build_client, to_record

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("backtest-extract")
log.setLevel(logging.INFO)

CONCURRENCY = 4


async def _one(sem, msg, idx, total):
    async with sem:
        base = await to_record(msg)
        ext = await extract(base["raw_text"], base["preview_text"], base["image_bytes"])
        log.info(
            "[%3d/%d] msg %s %s %s conf=%.2f img=%s",
            idx, total, msg.id, ext["action_type"], ext["asset"] or "-",
            ext["confidence"], "y" if base["has_image"] else "n",
        )
        return {
            "channel_msg_id": base["channel_msg_id"],
            "posted_at": base["posted_at"].isoformat(),
            "raw_text": base["raw_text"],
            "preview_text": base["preview_text"],
            "has_image": base["has_image"],
            "image_sha": base["image_sha"],
            **{k: v for k, v in ext.items() if k != "raw_llm_json"},
            "evidence": ext["raw_llm_json"].get("evidence"),
            "reasoning": ext["raw_llm_json"].get("reasoning"),
        }


async def main(days: int, out_path: str):
    await db.init_db()
    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(db.pool(), settings)

    client = build_client()
    await client.start()
    cutoff = time.time() - days * 86400

    msgs = []
    async for m in client.iter_messages(settings.tg_channel, limit=1000):
        if m.date.timestamp() < cutoff:
            break
        msgs.append(m)
    log.info("re-extracting %d messages over %d days", len(msgs), days)

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *(_one(sem, m, i + 1, len(msgs)) for i, m in enumerate(reversed(msgs)))
    )
    results.sort(key=lambda r: r["channel_msg_id"])

    with open(out_path, "w") as f:
        json.dump(results, f, indent=1)
    tok = sum(r.get("total_tokens") or 0 for r in results)
    log.info("wrote %s (%d records, %d tokens)", out_path, len(results), tok)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
