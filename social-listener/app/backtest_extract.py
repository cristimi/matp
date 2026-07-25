"""Re-extract a window of channel history with the CURRENT extractor.

Read-only with respect to the live tables: it talks to Telegram and the LLM and
writes a JSON dump plus a cache row per message. Nothing here touches
social_signal_log, social_shadow_orders or social_position_state — a backtest
must not mutate live state.

Successful extractions are cached in public.social_extraction_cache, keyed by
(message, extractor_version), and reused on the next run. Re-extraction costs
real money (~2.3M tokens for a 62-day window), so a run that dies partway — or a
provider outage mid-run — must not force paying for the whole window again.
Failures are never cached: they are exactly what a re-run needs to retry.

    python -m app.backtest_extract <days> <out.json> [--no-cache] [--concurrency N]
"""
import argparse
import asyncio
import json
import logging
import time

from app import db
from app.config import settings
from app.extractor import EXTRACTOR_VERSION, extract
from app.telegram import build_client, to_record

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("backtest-extract")
log.setLevel(logging.INFO)

# Stop hammering a provider that is clearly down. The first 62-day attempt burned
# through 1085 pointless calls after the credit balance went; this caps that.
MAX_CONSECUTIVE_FAILURES = 20


class Aborted(Exception):
    """Provider looks down — stop the run and keep what succeeded."""


def _is_failed(rec: dict) -> bool:
    if rec.get("failed"):
        return True
    # dumps written before extract() grew a `failed` flag
    return (rec.get("reasoning") or "").startswith("extraction_error")


class Runner:
    def __init__(self, total: int, use_cache: bool):
        self.total = total
        self.use_cache = use_cache
        self.done = 0
        self.failed = 0
        self.consecutive = 0
        self.results: list[dict] = []

    async def one(self, sem: asyncio.Semaphore, msg):
        async with sem:
            if self.consecutive >= MAX_CONSECUTIVE_FAILURES:
                raise Aborted
            base = await to_record(msg)
            ext = await extract(base["raw_text"], base["preview_text"], base["image_bytes"])

            rec = {
                "channel_msg_id": base["channel_msg_id"],
                "posted_at": base["posted_at"],
                "raw_text": base["raw_text"],
                "preview_text": base["preview_text"],
                "has_image": base["has_image"],
                "image_sha": base["image_sha"],
                **{k: v for k, v in ext.items() if k != "raw_llm_json"},
                "evidence": ext["raw_llm_json"].get("evidence"),
                "reasoning": ext["raw_llm_json"].get("reasoning"),
            }

            self.done += 1
            if _is_failed(rec):
                self.failed += 1
                self.consecutive += 1
                log.warning("[%d/%d] msg %s FAILED (%d consecutive)",
                            self.done, self.total, msg.id, self.consecutive)
            else:
                self.consecutive = 0
                if self.use_cache:
                    await db.cache_extraction(rec)   # checkpoint as we go
                log.info("[%d/%d] msg %s %s %s conf=%.2f img=%s",
                         self.done, self.total, msg.id, rec["action_type"],
                         rec["asset"] or "-", rec["confidence"],
                         "y" if rec["has_image"] else "n")

            self.results.append(rec)
            return rec


async def main(args):
    await db.init_db()
    from app.config_secrets import apply_llm_key_overrides
    await apply_llm_key_overrides(db.pool(), settings)

    client = build_client()
    await client.start()
    cutoff = time.time() - args.days * 86400

    msgs = []
    async for m in client.iter_messages(settings.tg_channel, limit=5000):
        if m.date.timestamp() < cutoff:
            break
        msgs.append(m)
    msgs.reverse()

    cached: dict[int, dict] = {}
    if args.cache:
        cached = await db.load_extraction_cache(EXTRACTOR_VERSION, args.days)
        cached = {k: v for k, v in cached.items() if not _is_failed(v)}
    todo = [m for m in msgs if m.id not in cached]

    log.info("%s: %d messages in window, %d cached (%s), %d to extract",
             EXTRACTOR_VERSION, len(msgs), len(cached),
             "reused" if args.cache else "cache disabled", len(todo))

    runner = Runner(len(todo), args.cache)
    sem = asyncio.Semaphore(args.concurrency)
    try:
        await asyncio.gather(*(runner.one(sem, m) for m in todo))
    except Aborted:
        log.error("ABORTED after %d consecutive failures — provider looks down. "
                  "%d successful extractions are cached and will be reused.",
                  MAX_CONSECUTIVE_FAILURES, runner.done - runner.failed)

    merged = list(cached.values()) + [r for r in runner.results if not _is_failed(r)]
    merged.sort(key=lambda r: r["channel_msg_id"])
    with open(args.out, "w") as f:
        json.dump(merged, f, indent=1, default=str)

    tokens = sum(r.get("total_tokens") or 0 for r in runner.results)
    log.info("wrote %s — %d records (%d reused, %d new), %d failed, %d tokens",
             args.out, len(merged), len(cached), len(merged) - len(cached),
             runner.failed, tokens)
    if len(merged) < len(msgs):
        log.warning("INCOMPLETE: %d of %d messages have no extraction — "
                    "re-run to retry them", len(msgs) - len(merged), len(msgs))
    await client.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", type=int)
    ap.add_argument("out")
    ap.add_argument("--no-cache", dest="cache", action="store_false",
                    help="ignore and do not write the cache (forces full re-extraction)")
    ap.add_argument("--concurrency", type=int, default=8)
    asyncio.run(main(ap.parse_args()))
