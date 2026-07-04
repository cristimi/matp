"""
WebPushSink: delivers a notification to every enabled push_subscriptions row via
the Web Push protocol (pywebpush + VAPID). Sends run one at a time per device so a
slow/dead device can't stall the others.
"""

import asyncio
import logging

from pywebpush import webpush, WebPushException

from app.config import settings
from app.db import get_pool
from app.sinks.base import Sink, SinkResult

logger = logging.getLogger(__name__)


class WebPushSink(Sink):
    name = "webpush"

    async def send(self, notification: dict) -> SinkResult:
        pool = get_pool()
        subs = await pool.fetch(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE enabled = true"
        )
        if not subs:
            return SinkResult(sent=0, failed=0, error="no enabled subscriptions")

        sent = 0
        failed = 0
        last_error = None
        for sub in subs:
            ok, err, gone = await asyncio.to_thread(self._send_one, sub, notification)
            if ok:
                sent += 1
            else:
                failed += 1
                last_error = err
                if gone:
                    logger.info("Subscription %s gone, disabling", sub["id"])
                    await self._disable(sub["id"])

        return SinkResult(sent=sent, failed=failed, error=last_error if sent == 0 else None)

    def _send_one(self, sub, notification: dict) -> tuple[bool, str | None, bool]:
        """Runs in a worker thread (via asyncio.to_thread) — no asyncpg/event-loop
        access here; the caller handles DB updates back on the main loop."""
        import json

        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        payload = json.dumps({
            "title": notification["title"],
            "body": notification["body"],
            "tag": notification["tag"],
            "renotify": notification.get("renotify", False),
            "data": notification.get("data", {}),
        })
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=settings.webpush_ttl_s,
                headers={"Urgency": settings.webpush_urgency},
            )
            return True, None, False
        except WebPushException as e:
            status_code = getattr(e.response, "status_code", None)
            gone = status_code in (404, 410)
            if not gone:
                logger.warning("WebPush send failed for %s: %s", sub["id"], e)
            return False, str(e), gone
        except Exception as e:
            # Malformed subscription data (bad base64 keys, etc.) raises outside
            # WebPushException — a bad device must not crash the pipeline either.
            logger.warning("WebPush send errored for %s: %s", sub["id"], e)
            return False, str(e), False

    async def _disable(self, sub_id) -> None:
        pool = get_pool()
        await pool.execute("UPDATE push_subscriptions SET enabled = false WHERE id = $1", sub_id)
