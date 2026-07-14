"""
LLM provider API key overrides from the `llm_keys` table.

Keys managed via the Settings page are encrypted (AES-256-GCM) by dashboard-api using
CONFIG_SECRET_KEY and stored one row per key in llm_keys (a provider can hold several
keys). This is a separate secret from order-executor's MASTER_KEY — these are not
exchange credentials.

This service does not rotate keys: it takes each provider's highest-priority enabled
key at startup and overrides the env-var default. Takes effect on next restart.
(Rotation across multiple keys lives in ai-signal-generator's key_pool.)
"""

import base64
import logging
import os

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_PROVIDER_SETTINGS_ATTR = {
    "anthropic": "anthropic_api_key",
    "openai":    "openai_api_key",
    "gemini":    "gemini_api_key",
    "groq":      "groq_api_key",
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
        """
        SELECT DISTINCT ON (provider) provider, encrypted_key
        FROM llm_keys
        WHERE enabled AND provider = ANY($1)
        ORDER BY provider, priority, id
        """,
        list(_PROVIDER_SETTINGS_ATTR.keys()),
    )
    for row in rows:
        attr = _PROVIDER_SETTINGS_ATTR[row["provider"]]
        try:
            setattr(settings, attr, _decrypt(row["encrypted_key"]))
            log.info("config: applied DB key for %s", attr)
        except Exception:
            log.exception("config: failed to decrypt key for %s — keeping env value", attr)
