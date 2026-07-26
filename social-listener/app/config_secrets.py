"""
LLM provider API key overrides from the `llm_keys` table.

Keys managed via the Settings page are encrypted (AES-256-GCM) by dashboard-api using
CONFIG_SECRET_KEY and stored one row per key in llm_keys (a provider can hold several
keys). This is a separate secret from order-executor's MASTER_KEY — these are not
exchange credentials.

Every enabled key is loaded, in priority order, into settings.provider_keys. The
extractor walks that list and moves to the next key on a transient failure, so a
dead high-priority key no longer takes the service down — that is exactly what
happened on 2026-07-26, when gemini's priority-0 key was out of credit while its
priority-1 key was fine and never got tried.

The highest-priority key is still mirrored into the legacy <provider>_api_key
setting so anything reading that attribute keeps working. Loaded at startup;
takes effect on next restart.
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
        SELECT provider, label, encrypted_key
        FROM llm_keys
        WHERE enabled AND provider = ANY($1)
        ORDER BY provider, priority, id
        """,
        list(_PROVIDER_SETTINGS_ATTR.keys()),
    )

    keys: dict[str, list[str]] = {}
    for row in rows:
        provider = row["provider"]
        try:
            keys.setdefault(provider, []).append(_decrypt(row["encrypted_key"]))
        except Exception:
            log.exception("config: failed to decrypt %s key %r — skipping",
                          provider, row["label"])

    settings.provider_keys = keys
    for provider, values in keys.items():
        # Legacy single-key attribute keeps pointing at the highest-priority key.
        setattr(settings, _PROVIDER_SETTINGS_ATTR[provider], values[0])
        log.info("config: loaded %d key(s) for %s", len(values), provider)
