-- Migration 056: multiple API keys per LLM provider.
--
-- Replaces the single-slot config rows (config.key = 'llm_key_<provider>') with a
-- dedicated llm_keys table so each provider can hold several keys. Consumers pick
-- keys in priority order (lowest number first) and rotate to the next key when one
-- hits a rate limit or auth failure (runtime state, not persisted here).
--
-- encrypted_key uses the same AES-256-GCM + CONFIG_SECRET_KEY scheme as the old
-- config rows (wire format: nonce(12) + ciphertext + tag(16), base64) — existing
-- ciphertexts are moved over verbatim.

CREATE TABLE IF NOT EXISTS llm_keys (
    id            SERIAL PRIMARY KEY,
    provider      TEXT        NOT NULL,          -- 'anthropic'|'openai'|'gemini'|'groq'|'cerebras'|'zhipu'
    label         TEXT        NOT NULL DEFAULT 'default',
    encrypted_key TEXT        NOT NULL,
    enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    priority      INT         NOT NULL DEFAULT 0, -- selection order within a provider (lower = first)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_keys_provider ON llm_keys (provider, enabled, priority);

-- Move existing single keys over (idempotent: skip if already migrated).
INSERT INTO llm_keys (provider, label, encrypted_key, created_at, updated_at)
SELECT replace(c.key, 'llm_key_', ''), 'migrated', c.value, c.updated_at, c.updated_at
FROM config c
WHERE c.key LIKE 'llm_key\_%' ESCAPE '\'
  AND NOT EXISTS (
    SELECT 1 FROM llm_keys k WHERE k.provider = replace(c.key, 'llm_key_', '')
  );

-- Remove the old single-slot rows — llm_keys is now the single source of truth.
DELETE FROM config WHERE key LIKE 'llm_key\_%' ESCAPE '\';
