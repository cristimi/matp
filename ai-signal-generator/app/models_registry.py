"""
Model registry with probe-based verification.

Raw model lists come from each provider's API. A lightweight probe call
(single token generation) verifies that a model is actually callable before
it appears in the dropdown. Results are cached in-memory with a 24 h TTL;
a background task re-probes on startup and daily thereafter.
"""

import asyncio
import logging
import time

from pydantic import BaseModel

from app.config import settings
from app.key_pool import key_pool

logger = logging.getLogger(__name__)


def _key(provider: str) -> str:
    """Current best API key for a provider (pool-managed, env fallback)."""
    handle = key_pool.acquire(provider)
    return handle.key if handle else ""

_TTL            = 86_400   # cache TTL: 24 h
_PROBE_TIMEOUT  = 15       # seconds per probe call
_PROBE_PROMPT   = "ok"
_PROBE_TOKENS   = 3

# "{provider}:{model_id}" → ("ok" | "fail", expires_at)
_cache: dict[str, tuple[str, float]] = {}


def _cache_get(provider: str, model_id: str) -> str | None:
    entry = _cache.get(f"{provider}:{model_id}")
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None


def _cache_set(provider: str, model_id: str, status: str) -> None:
    _cache[f"{provider}:{model_id}"] = (status, time.monotonic() + _TTL)


def clear_cache(provider: str | None = None) -> None:
    """Force expiry so the next probe_all_models() call re-probes everything."""
    if provider:
        keys = [k for k in _cache if k.startswith(f"{provider}:")]
    else:
        keys = list(_cache.keys())
    for k in keys:
        del _cache[k]


# ── Raw model lists ───────────────────────────────────────────────────────────

async def _raw_google() -> list[dict]:
    if not _key('google'):
        return []
    try:
        import google.generativeai as genai
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            genai.configure(api_key=_key('google'))
            models = genai.list_models()
        return [
            {"id": m.name.replace("models/", ""), "display_name": m.display_name, "provider": "google"}
            for m in models
            if "generateContent" in m.supported_generation_methods
        ]
    except Exception as exc:
        logger.error("Failed to list Google models: %s", exc)
        return []


async def _raw_openai() -> list[dict]:
    if not _key('openai'):
        return []
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_key('openai'))
        page = await client.models.list()
        return [
            {"id": m.id, "display_name": m.id, "provider": "openai"}
            for m in page.data
            if m.id.startswith(("gpt-", "o1", "o3", "o4"))
        ]
    except Exception as exc:
        logger.error("Failed to list OpenAI models: %s", exc)
        return []


_ANTHROPIC_FALLBACK = [
    {"id": "claude-opus-4-8",          "display_name": "Claude Opus 4.8",   "provider": "anthropic"},
    {"id": "claude-sonnet-4-6",        "display_name": "Claude Sonnet 4.6", "provider": "anthropic"},
    {"id": "claude-haiku-4-5-20251001","display_name": "Claude Haiku 4.5",  "provider": "anthropic"},
]


async def _raw_anthropic() -> list[dict]:
    if not _key('anthropic'):
        # No key — return fallback so the UI shows options even before key is configured
        return _ANTHROPIC_FALLBACK
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_key('anthropic'))
        page = client.models.list(limit=100)
        return [
            {"id": m.id, "display_name": m.display_name, "provider": "anthropic"}
            for m in page.data
        ]
    except Exception as exc:
        logger.error("Failed to list Anthropic models: %s", exc)
        return _ANTHROPIC_FALLBACK


async def _raw_groq() -> list[dict]:
    if not _key('groq'):
        return []
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=_key('groq'))
        page = await client.models.list()
        return [
            {"id": m.id, "display_name": m.id, "provider": "groq"}
            for m in page.data
            if getattr(m, "active", True)
        ]
    except Exception as exc:
        logger.error("Failed to list Groq models: %s", exc)
        return []


_ZHIPU_FALLBACK = [
    {"id": "glm-4.5-flash", "display_name": "GLM-4.5-Flash", "provider": "zhipu"},
    {"id": "glm-4-flash",   "display_name": "GLM-4-Flash",   "provider": "zhipu"},
]


async def _raw_cerebras() -> list[dict]:
    if not _key('cerebras'):
        return []
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_key('cerebras'),
                             base_url="https://api.cerebras.ai/v1")
        page = await client.models.list()
        return [{"id": m.id, "display_name": m.id, "provider": "cerebras"} for m in page.data]
    except Exception as exc:
        logger.error("Failed to list Cerebras models: %s", exc)
        return []


async def _raw_zhipu() -> list[dict]:
    if not _key('zhipu'):
        return []
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_key('zhipu'),
                             base_url=settings.zhipu_base_url)
        page = await client.models.list()
        models = [{"id": m.id, "display_name": m.id, "provider": "zhipu"} for m in page.data]
        return models or _ZHIPU_FALLBACK
    except Exception as exc:
        logger.error("Failed to list Zhipu models: %s — using fallback", exc)
        return _ZHIPU_FALLBACK


_RAW_FNS: dict[str, object] = {
    "google":    _raw_google,
    "openai":    _raw_openai,
    "anthropic": _raw_anthropic,
    "groq":      _raw_groq,
    "cerebras":  _raw_cerebras,
    "zhipu":     _raw_zhipu,
}


# ── Probes ────────────────────────────────────────────────────────────────────

async def _probe_google(model_id: str) -> bool:
    """
    Use the same LangChain path as the real cycle (not raw google.genai) so
    that API-surface differences don't cause false negatives.
    Only hard-fail on definitive NOT_FOUND — everything else (503, timeout,
    permission) is treated as uncertain and returns True so the model stays
    visible in the UI.
    """
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=model_id,
            temperature=0.1,
            google_api_key=_key('google'),
            max_retries=0,
        )
        resp = await asyncio.wait_for(llm.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return bool(resp.content)
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in ("NOT_FOUND", "404", "no longer available", "deprecated")):
            logger.debug("Google probe %s: definitively unavailable — %s", model_id, exc)
            return False
        # 503 rate-limit, timeout, permission, etc. — don't hide the model
        logger.debug("Google probe %s: transient/uncertain error — %s", model_id, exc)
        return True


async def _probe_openai(model_id: str) -> bool:
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=model_id,
            temperature=0.1,
            api_key=_key('openai'),
            max_retries=0,
        )
        resp = await asyncio.wait_for(llm.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return bool(resp.content)
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in ("404", "model_not_found", "does not exist", "deprecated")):
            logger.debug("OpenAI probe %s: definitively unavailable — %s", model_id, exc)
            return False
        logger.debug("OpenAI probe %s: transient/uncertain error — %s", model_id, exc)
        return True


async def _probe_anthropic(model_id: str) -> bool:
    if not _key('anthropic'):
        return True  # no key to verify with — pass through
    try:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model=model_id,
            temperature=0.1,
            api_key=_key('anthropic'),
            max_retries=0,
        )
        resp = await asyncio.wait_for(llm.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return bool(resp.content)
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in ("404", "not_found_error", "does not exist", "deprecated")):
            logger.debug("Anthropic probe %s: definitively unavailable — %s", model_id, exc)
            return False
        logger.debug("Anthropic probe %s: transient/uncertain error — %s", model_id, exc)
        return True


class _ProbeSchema(BaseModel):
    ok: bool


async def _probe_groq(model_id: str) -> bool:
    """
    Groq serves some models (e.g. 'compound', 'compound-mini') that answer plain
    chat fine but reject tool-calling outright ('tool calling is not supported
    with this model') — and every real signal cycle uses
    with_structured_output(..., include_raw=True), which needs tool-calling. A
    plain-chat probe would let those models pass and then fail 100% of the time
    in production, so this probes the same structured-output path node_analyze
    actually uses.
    """
    try:
        from langchain_groq import ChatGroq
        llm = ChatGroq(
            model=model_id,
            temperature=0.1,
            api_key=_key('groq'),
            max_retries=0,
        )
        structured = llm.with_structured_output(_ProbeSchema, include_raw=True)
        resp = await asyncio.wait_for(structured.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return resp.get("parsed") is not None
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in (
            "404", "model_not_found", "does not exist", "decommissioned",
            "tool calling", "tool_use_failed", "does not support tool", "function calling",
        )):
            logger.debug("Groq probe %s: definitively unavailable — %s", model_id, exc)
            return False
        logger.debug("Groq probe %s: transient/uncertain error — %s", model_id, exc)
        return True


async def _probe_cerebras(model_id: str) -> bool:
    """
    Mirrors _probe_groq: probe the structured-output path (with_structured_output
    + include_raw) since node_analyze always uses that, not plain chat.
    """
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model_id, temperature=0.1,
                         api_key=_key('cerebras'),
                         base_url="https://api.cerebras.ai/v1", max_retries=0)
        # method="function_calling": matches node_analyze._STRUCTURED_OUTPUT_METHOD —
        # the default "json_schema" method isn't reliably honored by this
        # OpenAI-compatible gateway, so probing with the default would false-pass.
        structured = llm.with_structured_output(_ProbeSchema, include_raw=True, method="function_calling")
        resp = await asyncio.wait_for(structured.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return resp.get("parsed") is not None
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in (
            "404", "model_not_found", "does not exist", "decommissioned",
            "tool calling", "tool_use_failed", "does not support tool", "function calling",
        )):
            logger.debug("Cerebras probe %s: definitively unavailable — %s", model_id, exc)
            return False
        logger.debug("Cerebras probe %s: transient/uncertain — %s", model_id, exc)
        return True


async def _probe_zhipu(model_id: str) -> bool:
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model_id, temperature=0.1,
                         api_key=_key('zhipu'),
                         base_url=settings.zhipu_base_url, max_retries=0)
        # method="function_calling": matches node_analyze._STRUCTURED_OUTPUT_METHOD —
        # the default "json_schema" method isn't reliably honored by this
        # OpenAI-compatible gateway, so probing with the default would false-pass.
        structured = llm.with_structured_output(_ProbeSchema, include_raw=True, method="function_calling")
        resp = await asyncio.wait_for(structured.ainvoke(_PROBE_PROMPT), timeout=_PROBE_TIMEOUT)
        return resp.get("parsed") is not None
    except Exception as exc:
        exc_str = str(exc)
        if any(s in exc_str for s in (
            "404", "model_not_found", "does not exist", "decommissioned",
            "tool calling", "tool_use_failed", "does not support tool", "function calling",
        )):
            logger.debug("Zhipu probe %s: definitively unavailable — %s", model_id, exc)
            return False
        logger.debug("Zhipu probe %s: transient/uncertain — %s", model_id, exc)
        return True


_PROBE_FNS: dict[str, object] = {
    "google":    _probe_google,
    "openai":    _probe_openai,
    "anthropic": _probe_anthropic,
    "groq":      _probe_groq,
    "cerebras":  _probe_cerebras,
    "zhipu":     _probe_zhipu,
}


# ── Public API ────────────────────────────────────────────────────────────────

def known_providers() -> list[str]:
    """All providers the registry knows about, in stable definition order."""
    return list(_RAW_FNS.keys())


def cached_ok_models(provider: str) -> list[str]:
    """Model ids with a fresh 'ok' probe result for this provider — cache only,
    never touches the network. Used by the LLM fallback chain, which must not
    re-probe inline on a live cycle."""
    now = time.monotonic()
    out = []
    for key, (status, expires_at) in _cache.items():
        p, _, model_id = key.partition(":")
        if p == provider and status == "ok" and now < expires_at:
            out.append(model_id)
    return out


async def raw_models(provider: str) -> list[dict]:
    """Provider's raw model list (network call). Cold-cache fallback for the
    LLM chain: an unverified attempt beats a dead cycle."""
    raw_fn = _RAW_FNS.get(provider)
    return await raw_fn() if raw_fn else []



async def get_available_models(provider: str) -> list[dict]:
    """
    Return models for the given provider, sorted verified-first.
    - verified=True  → probe passed (cached ok)
    - verified=False → probe failed or not yet probed — shown with ⚠ in UI

    No model is ever hidden: a failed probe might be a false negative
    (e.g. a model supports structured output but not plain text generation).
    """
    raw_fn = _RAW_FNS.get(provider)
    if not raw_fn:
        return []
    raw = await raw_fn()

    verified, unverified = [], []
    for m in raw:
        status = _cache_get(provider, m["id"])
        if status == "ok":
            verified.append({**m, "verified": True})
        else:
            unverified.append({**m, "verified": False})

    return verified + unverified


async def probe_all_models(provider: str | None = None, force: bool = False) -> dict[str, dict]:
    """
    Probe all listed models for the given provider(s).
    Skips models whose cache entry is still fresh unless force=True.
    Returns a per-provider summary: {passed, failed, skipped}.
    """
    targets = [provider] if provider else list(_RAW_FNS.keys())
    summary: dict[str, dict] = {}

    for p in targets:
        raw_fn   = _RAW_FNS.get(p)
        probe_fn = _PROBE_FNS.get(p)
        if not raw_fn or not probe_fn:
            continue

        raw = await raw_fn()
        passed = failed = skipped = 0

        for m in raw:
            if not force and _cache_get(p, m["id"]) is not None:
                skipped += 1
                continue
            ok = await probe_fn(m["id"])
            _cache_set(p, m["id"], "ok" if ok else "fail")
            logger.info("Model probe %s/%s → %s", p, m["id"], "ok" if ok else "fail")
            if ok:
                passed += 1
            else:
                failed += 1

        summary[p] = {"passed": passed, "failed": failed, "skipped": skipped}
        logger.info("Probe summary for %s: %s", p, summary[p])

    return summary
