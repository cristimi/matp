"""
LLM provider API key overrides from the `config` table.

Keys edited via the Settings page are encrypted (AES-256-GCM) by dashboard-api using
CONFIG_SECRET_KEY and stored as config.key = 'llm_key_<provider>'. This is a separate
secret from order-executor's MASTER_KEY — these are not exchange credentials.

A key found here overrides this service's env-var default at startup. Takes effect on
next restart, same as editing .env did before this existed.
"""

import base64
import logging
import os

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_PROVIDER_SETTINGS_ATTR = {
    "llm_key_anthropic": "anthropic_api_key",
    "llm_key_openai":    "openai_api_key",
    "llm_key_gemini":    "gemini_api_key",
}


def _get_key() -> bytes:
    key_str = os.environ.get("CONFIG_SECRET_KEY", "")
    if len(key_str) < 32:
        raise ValueError("CONFIG_SECRET_KEY environment variable must be at least 32 characters.")
    return key_str[:32].encode("utf-8")


def _decrypt(value_b64: str) -> str:
    raw = base64.b64decode(value_b64)
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(_get_key()).decrypt(nonce, ciphertext, None).decode("utf-8")


async def apply_llm_key_overrides(pool: asyncpg.Pool, settings) -> None:
    rows = await pool.fetch(
        "SELECT key, value FROM config WHERE key = ANY($1)",
        list(_PROVIDER_SETTINGS_ATTR.keys()),
    )
    for row in rows:
        attr = _PROVIDER_SETTINGS_ATTR[row["key"]]
        try:
            setattr(settings, attr, _decrypt(row["value"]))
            log.info("config: applied DB override for %s", attr)
        except Exception:
            log.exception("config: failed to decrypt override for %s — keeping env value", attr)
