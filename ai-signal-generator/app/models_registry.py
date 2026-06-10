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

from app.config import settings

logger = logging.getLogger(__name__)

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
    if not settings.gemini_api_key:
        return []
    try:
        import google.generativeai as genai
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            genai.configure(api_key=settings.gemini_api_key)
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
    if not settings.openai_api_key:
        return []
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
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
    if not settings.anthropic_api_key:
        # No key — return fallback so the UI shows options even before key is configured
        return _ANTHROPIC_FALLBACK
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        page = client.models.list(limit=100)
        return [
            {"id": m.id, "display_name": m.display_name, "provider": "anthropic"}
            for m in page.data
        ]
    except Exception as exc:
        logger.error("Failed to list Anthropic models: %s", exc)
        return _ANTHROPIC_FALLBACK


_RAW_FNS: dict[str, object] = {
    "google":    _raw_google,
    "openai":    _raw_openai,
    "anthropic": _raw_anthropic,
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
            google_api_key=settings.gemini_api_key,
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
            api_key=settings.openai_api_key,
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
    if not settings.anthropic_api_key:
        return True  # no key to verify with — pass through
    try:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model=model_id,
            temperature=0.1,
            api_key=settings.anthropic_api_key,
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


_PROBE_FNS: dict[str, object] = {
    "google":    _probe_google,
    "openai":    _probe_openai,
    "anthropic": _probe_anthropic,
}


# ── Public API ────────────────────────────────────────────────────────────────

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
