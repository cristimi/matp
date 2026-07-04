"""
Stream consumer: xreadgroup -> dedup -> render -> dispatch to sinks -> log -> xack.

Crash-safety: if anything raises before the notification_log row is written, the
entry is left unacked so it is redelivered on restart. A failed push send is not
an exception here — it is recorded as status='failed' and still acked, because a
dead phone must not block the pipeline.
"""

import asyncio
import json
import logging

from app.config import settings
from app import redis_client
from app.db import get_pool
from app.render import render, compute_dedup_key
from app.sinks.base import Sink

logger = logging.getLogger(__name__)

DEDUP_WINDOW = "24 hours"


async def _already_sent(pool, dedup_key: str) -> bool:
    if not dedup_key:
        return False
    row = await pool.fetchrow(
        f"""
        SELECT 1 FROM notification_log
        WHERE dedup_key = $1 AND created_at > now() - interval '{DEDUP_WINDOW}'
        LIMIT 1
        """,
        dedup_key,
    )
    return row is not None


async def _log(pool, event_type, dedup_key, position_id, title, body, payload, status, error, device_count):
    await pool.execute(
        """
        INSERT INTO notification_log
            (event_type, dedup_key, position_id, title, body, payload, status, error, device_count, sent_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, CASE WHEN $7 = 'sent' THEN now() ELSE NULL END)
        """,
        event_type, dedup_key, position_id, title, body, json.dumps(payload, default=str),
        status, error, device_count,
    )


async def process_entry(pool, sinks: list[Sink], entry_id: str, fields: dict) -> None:
    raw = fields.get("data")
    if not raw:
        logger.warning("Stream entry %s missing 'data' field, acking to drop", entry_id)
        await redis_client.ack(entry_id)
        return

    data = json.loads(raw)
    event = data.get("event")
    position_id = data.get("position_id")
    dedup_key = compute_dedup_key(event, data)

    if await _already_sent(pool, dedup_key):
        logger.info("Skipping duplicate event %s dedup_key=%s", event, dedup_key)
        await _log(pool, event, dedup_key, position_id, None, None, data, "skipped", None, 0)
        await redis_client.ack(entry_id)
        return

    notification = render(event, data)
    if notification is None:
        logger.warning("Unknown event type %s, acking to drop", event)
        await _log(pool, event, dedup_key, position_id, None, None, data, "skipped", "unknown event type", 0)
        await redis_client.ack(entry_id)
        return

    sent_count = 0
    failed_count = 0
    last_error = None
    for sink in sinks:
        result = await sink.send(notification)
        sent_count += result.sent
        failed_count += result.failed
        if result.error:
            last_error = result.error

    status = "sent" if sent_count > 0 else "failed"
    await _log(
        pool, event, dedup_key, position_id,
        notification["title"], notification["body"], data,
        status, last_error, sent_count,
    )
    # Delivery failure (no device reachable) must not block the pipeline — ack regardless.
    await redis_client.ack(entry_id)


async def run_consumer_loop() -> None:
    from app.sinks.webpush import WebPushSink

    pool = get_pool()
    sinks: list[Sink] = [WebPushSink()]

    await redis_client.ensure_group()
    logger.info("Consumer loop starting on stream=%s group=%s", settings.stream_key, settings.consumer_group)

    # Drain any entries left unacked by a previous crash before taking new ones —
    # otherwise a mid-processing crash strands that entry in the PEL forever, since
    # '>' below only ever returns entries this consumer hasn't been given yet.
    # Guarded against a poison-pill entry that always fails to ack: if a batch's
    # ids are identical to the previous batch, nothing was acked, so stop instead
    # of spinning forever — it'll be retried on the next restart.
    last_ids = None
    while True:
        pending = await redis_client.read_pending(count=10)
        if not pending:
            break
        ids = [entry_id for entry_id, _ in pending]
        if ids == last_ids:
            logger.error("No progress draining %d pending entr%s (stuck), leaving for next restart", len(pending), "y" if len(pending) == 1 else "ies")
            break
        last_ids = ids
        logger.info("Redelivering %d unacked entr%s from a previous run", len(pending), "y" if len(pending) == 1 else "ies")
        for entry_id, fields in pending:
            try:
                await process_entry(pool, sinks, entry_id, fields)
            except Exception:
                logger.exception("Failed reprocessing pending entry %s, leaving unacked for retry", entry_id)

    while True:
        try:
            entries = await redis_client.read_group(count=10, block_ms=5000)
            for entry_id, fields in entries:
                try:
                    await process_entry(pool, sinks, entry_id, fields)
                except Exception:
                    logger.exception("Failed processing entry %s, leaving unacked for retry", entry_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Consumer loop error, retrying after backoff")
            await asyncio.sleep(5)
