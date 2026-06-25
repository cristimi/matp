import asyncio
import logging

from telethon import events

from app import db
from app.config import settings
from app.extractor import extract
from app.telegram import build_client, to_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("social-listener")


async def handle(msg):
    if await db.already_seen(msg.id):
        return
    base = to_record(msg)
    ext = await extract(base["raw_text"], base["preview_text"])
    rec = {**base, **ext}
    if await db.insert_signal(rec):
        flag = "ACTIONABLE" if rec["is_actionable"] else "·"
        log.info(
            "msg %s [%s] %s %s ref=%s conf=%.2f",
            msg.id, flag, rec["action_type"], rec["asset"] or "-",
            rec["reference_price"], rec["confidence"],
        )


async def main():
    await db.init_db()
    client = build_client()
    await client.start()  # StringSession is pre-authorized -> non-interactive
    me = await client.get_me()
    log.info("Telegram connected as %s", getattr(me, "username", None) or me.id)

    channel = await client.get_entity(settings.tg_channel)

    log.info("Backfilling last %d messages from %s", settings.backfill_limit, settings.tg_channel)
    msgs = []
    async for m in client.iter_messages(channel, limit=settings.backfill_limit):
        msgs.append(m)
    for m in reversed(msgs):  # oldest -> newest
        await handle(m)
    log.info("Backfill complete (%d messages)", len(msgs))

    @client.on(events.NewMessage(chats=channel))
    async def _live(event):
        try:
            await handle(event.message)
        except Exception:  # noqa: BLE001
            log.exception("live handler error")

    log.info("Listening for new messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
