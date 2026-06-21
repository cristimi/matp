import logging
import re

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage, WebPage

from app.config import settings

log = logging.getLogger(__name__)
_X_URL = re.compile(r"https?://(?:x|twitter)\.com/\S+", re.I)


def build_client() -> TelegramClient:
    return TelegramClient(
        StringSession(settings.tg_session), settings.tg_api_id, settings.tg_api_hash
    )


def _preview(msg) -> tuple[str, str | None]:
    """Extract (preview_text, x_url) from a message's embedded web preview, if present."""
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaWebPage) and isinstance(media.webpage, WebPage):
        wp = media.webpage
        text = f"{wp.title or ''}\n{wp.description or ''}".strip()
        return text, (wp.url or None)
    return "", None


def to_record(msg) -> dict:
    raw = msg.message or ""
    preview_text, x_url = _preview(msg)
    if not x_url:
        m = _X_URL.search(raw)
        x_url = m.group(0) if m else None
    return {
        "channel_msg_id": msg.id,
        "posted_at": msg.date,   # tz-aware UTC datetime
        "raw_text": raw,
        "preview_text": preview_text,
        "x_url": x_url,
    }
