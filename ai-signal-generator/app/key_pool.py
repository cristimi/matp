"""
Per-provider pool of LLM API keys with failover rotation.

Keys live in the llm_keys table (encrypted AES-256-GCM with CONFIG_SECRET_KEY,
written by dashboard-api). The pool decrypts them at load time and hands out the
highest-priority usable key per provider. Runtime feedback drives rotation:

- report_rate_limited(handle)  → cooldown (escalating 60s → 1h), next acquire()
                                 returns the next key in priority order
- report_auth_failed(handle)   → key marked dead in memory until reload; it is
                                 NOT auto-disabled in the DB (a transient 403
                                 must not permanently kill a key)
- report_ok(handle)            → resets the cooldown escalation

If a provider has no DB rows, the env-var key from settings (e.g. GEMINI_API_KEY)
is used as an implicit single-entry fallback, so pre-migration setups keep working.

reload() re-reads the table without a restart (POST /internal/llm-keys/reload,
called by dashboard-api after any key edit). Cooldown/dead state is keyed by DB id
and survives reloads for unchanged keys.

Provider naming: the models registry / llm chain say 'google'; the llm_keys table
and dashboard say 'gemini'. _KEY_PROVIDER maps the former to the latter — pool
methods accept either.
"""

import base64
import logging
import time
from dataclasses import dataclass, field

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

log = logging.getLogger(__name__)

# registry/chain provider name → llm_keys.provider slug
_KEY_PROVIDER = {"google": "gemini"}

# llm_keys.provider slug → settings attribute holding the env-var fallback
_ENV_ATTR = {
    "gemini":    "gemini_api_key",
    "openai":    "openai_api_key",
    "anthropic": "anthropic_api_key",
    "groq":      "groq_api_key",
    "cerebras":  "cerebras_api_key",
    "zhipu":     "zhipu_api_key",
    "openrouter": "openrouter_api_key",
}

_COOLDOWN_BASE = 60      # seconds after the first rate-limit
_COOLDOWN_MAX  = 3600    # cap for escalating cooldowns


def _decrypt(value_b64: str) -> str:
    import os
    key_str = os.environ.get("CONFIG_SECRET_KEY", "")
    if len(key_str) < 32:
        raise ValueError("CONFIG_SECRET_KEY environment variable must be at least 32 characters.")
    raw = base64.b64decode(value_b64)
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(key_str[:32].encode("utf-8")).decrypt(nonce, ciphertext, None).decode("utf-8")


@dataclass
class KeyHandle:
    """One usable key. `id` is the llm_keys row id, or None for the env fallback."""
    id: int | None
    provider: str          # llm_keys slug ('gemini', not 'google')
    label: str
    key: str


@dataclass
class _Entry:
    handle: KeyHandle
    cooldown_until: float = 0.0    # time.monotonic()
    consecutive_429: int = 0
    dead: bool = False             # auth-failed this process; cleared on reload
    dead_reason: str | None = None

    def usable(self) -> bool:
        return not self.dead and time.monotonic() >= self.cooldown_until


class KeyPool:
    def __init__(self):
        self._entries: dict[str, list[_Entry]] = {}   # slug → priority-ordered entries
        self._pool: asyncpg.Pool | None = None
        # True env-var key values, snapshotted on first load(). Must NOT be read
        # live from settings after that: a deleted DB key would otherwise leak
        # back in as a phantom "env" key via any stale settings mutation.
        self._env_defaults: dict[str, str] | None = None

    @staticmethod
    def _slug(provider: str) -> str:
        return _KEY_PROVIDER.get(provider, provider)

    # ── Loading ───────────────────────────────────────────────────────────────

    async def load(self, pool: asyncpg.Pool) -> None:
        """(Re)load keys from llm_keys. Runtime state (cooldown/dead) carries over
        for rows whose id and ciphertext are unchanged."""
        self._pool = pool
        if self._env_defaults is None:
            self._env_defaults = {slug: getattr(settings, attr, "") or ""
                                  for slug, attr in _ENV_ATTR.items()}
        rows = await pool.fetch(
            "SELECT id, provider, label, encrypted_key FROM llm_keys "
            "WHERE enabled ORDER BY provider, priority, id"
        )
        old = {e.handle.id: e for entries in self._entries.values() for e in entries
               if e.handle.id is not None}

        entries: dict[str, list[_Entry]] = {}
        for row in rows:
            try:
                key = _decrypt(row["encrypted_key"])
            except Exception:
                log.exception("key_pool: failed to decrypt llm_keys id=%s (%s/%s) — skipping",
                              row["id"], row["provider"], row["label"])
                continue
            handle = KeyHandle(id=row["id"], provider=row["provider"],
                               label=row["label"], key=key)
            entry = _Entry(handle=handle)
            prev = old.get(row["id"])
            if prev is not None and prev.handle.key == key:
                entry.cooldown_until  = prev.cooldown_until
                entry.consecutive_429 = prev.consecutive_429
            entries.setdefault(row["provider"], []).append(entry)

        # Env-var fallback for providers with no DB rows (snapshotted values only).
        for slug in _ENV_ATTR:
            if slug not in entries and self._env_defaults.get(slug):
                entries[slug] = [_Entry(handle=KeyHandle(
                    id=None, provider=slug, label="env", key=self._env_defaults[slug]))]

        self._entries = entries
        counts = {p: len(es) for p, es in entries.items()}
        log.info("key_pool: loaded %s", counts or "no keys")

    async def reload(self) -> dict:
        if self._pool is None:
            raise RuntimeError("key_pool.reload() before load()")
        await self.load(self._pool)
        return {p: len(es) for p, es in self._entries.items()}

    # ── Selection ─────────────────────────────────────────────────────────────

    def _env_handle(self, slug: str) -> KeyHandle | None:
        """Ephemeral env-var key for a provider the pool has no entries for —
        covers the window before load() and unit tests that only set settings.
        After load(), only the snapshot is consulted (an unlisted provider then
        means "no keys", not "check settings again")."""
        if self._env_defaults is not None:
            key = self._env_defaults.get(slug, "")
        else:
            attr = _ENV_ATTR.get(slug)
            key = getattr(settings, attr, "") if attr else ""
        return KeyHandle(id=None, provider=slug, label="env", key=key) if key else None

    def acquire(self, provider: str) -> KeyHandle | None:
        """Highest-priority usable key for the provider, or None. Keys under
        cooldown or dead are skipped; if every key is unavailable, fall back to
        the least-cooled one (an attempt with a maybe-limited key beats none)."""
        slug = self._slug(provider)
        entries = self._entries.get(slug, [])
        if not entries:
            return self._env_handle(slug)
        for e in entries:
            if e.usable():
                return e.handle
        alive = [e for e in entries if not e.dead]
        if alive:
            return min(alive, key=lambda e: e.cooldown_until).handle
        return None

    def acquire_after(self, provider: str, tried_ids: set[int | None]) -> KeyHandle | None:
        """Next usable key whose id is not in tried_ids — used to rotate within
        one (provider, model) candidate after a rate-limit/auth failure."""
        entries = self._entries.get(self._slug(provider), [])
        for e in entries:
            if e.handle.id not in tried_ids and e.usable():
                return e.handle
        return None

    def has_key(self, provider: str) -> bool:
        slug = self._slug(provider)
        entries = self._entries.get(slug)
        if not entries:
            return self._env_handle(slug) is not None
        return any(not e.dead for e in entries)

    # ── Feedback ──────────────────────────────────────────────────────────────

    def _find(self, handle: KeyHandle) -> _Entry | None:
        for e in self._entries.get(handle.provider, []):
            if e.handle.id == handle.id:
                return e
        return None

    def report_ok(self, handle: KeyHandle) -> None:
        e = self._find(handle)
        if e:
            e.consecutive_429 = 0

    def report_rate_limited(self, handle: KeyHandle, retry_after: float | None = None) -> None:
        e = self._find(handle)
        if e is None:
            return
        e.consecutive_429 += 1
        cooldown = retry_after or min(_COOLDOWN_BASE * (2 ** (e.consecutive_429 - 1)), _COOLDOWN_MAX)
        e.cooldown_until = time.monotonic() + cooldown
        log.warning("key_pool: %s/%s rate-limited — cooldown %.0fs",
                    handle.provider, handle.label, cooldown)

    def report_auth_failed(self, handle: KeyHandle, reason: str = "") -> None:
        e = self._find(handle)
        if e is None:
            return
        e.dead = True
        e.dead_reason = (reason or "auth failed")[:200]
        log.error("key_pool: %s/%s auth failed — key disabled until reload: %s",
                  handle.provider, handle.label, e.dead_reason)

    # ── Introspection (for /internal/llm-keys/status) ─────────────────────────

    def status(self) -> dict:
        now = time.monotonic()
        out: dict[str, list[dict]] = {}
        for slug, entries in self._entries.items():
            out[slug] = [{
                "id":            e.handle.id,
                "label":         e.handle.label,
                "state":         ("auth_failed" if e.dead
                                  else "cooldown" if now < e.cooldown_until
                                  else "active"),
                "cooldown_remaining_s": max(0, round(e.cooldown_until - now)) or None,
                "dead_reason":   e.dead_reason,
            } for e in entries]
        return out


key_pool = KeyPool()
