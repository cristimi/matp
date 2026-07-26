import asyncio
import hashlib
import logging
import re

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageMediaWebPage, WebPage

from app.config import settings

log = logging.getLogger(__name__)
_X_URL = re.compile(r"https?://(?:x|twitter)\.com/\S+", re.I)


def build_client() -> TelegramClient:
    return TelegramClient(
        StringSession(settings.tg_session), settings.tg_api_id, settings.tg_api_hash
    )


def _webpage(msg):
    """Return the resolved WebPage of a message's link preview, or None."""
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaWebPage) and isinstance(media.webpage, WebPage):
        return media.webpage
    return None


def _preview_pending(msg) -> bool:
    """True when Telegram has attached a link preview but hasn't fetched it yet.

    A freshly-posted message arrives with WebPagePending; the title, description
    and photo land a second or two later. Reading it too early silently drops the
    X post's text (and its chart image) on the live path.
    """
    media = getattr(msg, "media", None)
    return isinstance(media, MessageMediaWebPage) and not isinstance(media.webpage, WebPage)


async def _resolve_preview(msg):
    """Re-fetch a message until its pending web preview resolves. Returns the message."""
    for attempt in range(settings.webpage_resolve_attempts):
        if not _preview_pending(msg):
            return msg
        await asyncio.sleep(settings.webpage_resolve_delay_seconds)
        client = getattr(msg, "client", None)
        if client is None:
            return msg
        try:
            fresh = await client.get_messages(msg.chat_id, ids=msg.id)
        except Exception:  # noqa: BLE001
            log.warning("preview re-fetch failed for msg %s", msg.id, exc_info=True)
            return msg
        if fresh is None:
            return msg
        msg = fresh
        log.info("msg %s: web preview resolved on attempt %d", msg.id, attempt + 1)
    return msg


def _preview_text(msg) -> tuple[str, str | None]:
    wp = _webpage(msg)
    if wp is None:
        return "", None
    text = f"{wp.title or ''}\n{wp.description or ''}".strip()
    return text, (wp.url or None)


def _photo(msg):
    """The downloadable Photo for this message, if any.

    Two shapes appear in the channel: an X repost, where the chart rides on the
    link preview (`webpage.photo`), and a natively attached image
    (`MessageMediaPhoto`). Both are Telegram-hosted, so neither needs X access.
    """
    wp = _webpage(msg)
    if wp is not None and getattr(wp, "photo", None) is not None:
        return wp.photo
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaPhoto):
        return media.photo
    return None


async def _download_image(msg) -> bytes | None:
    if not settings.vision_enabled:
        return None
    photo = _photo(msg)
    if photo is None:
        return None
    client = getattr(msg, "client", None)
    if client is None:
        return None
    try:
        data = await client.download_media(photo, file=bytes)
    except Exception:  # noqa: BLE001
        log.warning("image download failed for msg %s", msg.id, exc_info=True)
        return None
    if not data:
        return None
    if len(data) > settings.image_max_bytes:
        log.warning(
            "msg %s: image %d bytes exceeds cap %d — extracting text only",
            msg.id, len(data), settings.image_max_bytes,
        )
        return None
    return data


async def to_record(msg) -> dict:
    """Build the base record: native text, resolved link preview, and chart image."""
    if _preview_pending(msg):
        msg = await _resolve_preview(msg)

    raw = msg.message or ""
    preview_text, x_url = _preview_text(msg)
    if not x_url:
        m = _X_URL.search(raw)
        x_url = m.group(0) if m else None

    image = await _download_image(msg)

    return {
        "channel_msg_id": msg.id,
        "posted_at": msg.date,   # tz-aware UTC datetime
        "raw_text": raw,
        "preview_text": preview_text,
        "x_url": x_url,
        "image_bytes": image,
        "has_image": image is not None,
        "image_sha": hashlib.sha256(image).hexdigest() if image else None,
        "merged_msg_ids": [msg.id],
    }


def merge_records(records: list[dict]) -> dict:
    """
    Fold a burst of Telegram messages into the single post they represent.

    The author often splits one thought across messages seconds apart — a
    comment, then the X link whose preview carries the full article and chart.
    Judging each separately costs an extra LLM call and produces two verdicts
    for one intent, so they are concatenated and extracted once.

    Keyed on the **highest** message id: max_channel_msg_id then advances past
    every part, so the catchup loop will not re-fetch the earlier ones. posted_at
    is the **earliest**, because that is when the human actually posted, and the
    staleness gate is measured against it.
    """
    if len(records) == 1:
        return records[0]

    ordered = sorted(records, key=lambda r: r["channel_msg_id"])
    last    = ordered[-1]

    texts = [r["raw_text"].strip() for r in ordered if (r["raw_text"] or "").strip()]
    # The preview is the fullest rendering of the linked post; take the first one
    # that resolved rather than concatenating near-identical copies.
    preview = next((r["preview_text"] for r in ordered if (r["preview_text"] or "").strip()), "")
    x_url   = next((r["x_url"] for r in ordered if r["x_url"]), None)
    imaged  = next((r for r in ordered if r["image_bytes"]), None)

    return {
        "channel_msg_id": last["channel_msg_id"],
        "posted_at":      ordered[0]["posted_at"],
        "raw_text":       "\n\n".join(texts),
        "preview_text":   preview,
        "x_url":          x_url,
        "image_bytes":    imaged["image_bytes"] if imaged else None,
        "has_image":      imaged is not None,
        "image_sha":      imaged["image_sha"] if imaged else None,
        "merged_msg_ids": [r["channel_msg_id"] for r in ordered],
    }
