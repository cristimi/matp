"""
LLM call with an ordered failure-fallback chain.

A cycle used to die with llm_error whenever the configured model threw, timed
out, or failed structured-output parsing. call_llm_chain() instead walks an
ordered list of (provider, model) candidates until one answers or the chain is
exhausted. Chain construction (build_fallback_chain) is auto-derived from the
probe-verified models registry cache unless the strategy sets a manual
llm_fallback_chain override.
"""

import asyncio
import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app import models_registry

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER = 'google'
_DEFAULT_MODEL    = 'gemini-2.5-flash'

_LLM_TIMEOUT          = 90  # seconds — hard ceiling for the primary call
# Fallback attempts get a tighter ceiling: a healthy model answers this prompt
# well under 45 s, and the cycle has already burned up to 90 s on the primary.
# Worst case total: 90 + 3*45 = 225 s (see _MAX_FALLBACK_ATTEMPTS).
_FALLBACK_TIMEOUT     = 45
_MAX_FALLBACK_ATTEMPTS = 3   # beyond the primary


class LLMSignalOutput(BaseModel):
    action:          Literal[
        'open_long', 'open_short', 'close_long', 'close_short',
        'hold', 'partial_close', 'adjust_stops',
        'place_limit_long', 'place_limit_short', 'cancel_order', 'amend_order',
    ]
    confidence:      float
    size_pct:        float
    stop_loss_pct:   float = Field(description="Distance from entry as a percent, e.g. 1.5 = 1.5%. Use 0 for hold/close actions.")
    take_profit_pct: float = Field(description="Distance from entry as a percent, e.g. 3.0 = 3.0%. Use 0 for hold/close actions.")
    new_sl_price:    Optional[float] = Field(default=None, description="New stop-loss price for adjust_stops, or the re-fitted stop-loss to carry onto the order for amend_order.")
    new_tp_price:    Optional[float] = Field(default=None, description="New take-profit price for adjust_stops, or the re-fitted take-profit to carry onto the order for amend_order.")
    limit_price:     Optional[float] = Field(default=None, description="Boundary price for place_limit_long/short, or the new price for amend_order.")
    target_order_id: Optional[str] = Field(default=None, description="Resting order id (from OPEN ORDERS context) for cancel_order/amend_order.")
    reasoning:       str


# langchain's default with_structured_output method is "json_schema" (OpenAI's
# strict Structured Outputs API). Third-party OpenAI-compatible gateways don't all
# implement that strict feature — when they silently ignore it, the model answers
# conversationally and can wrap its JSON in a markdown fence, which fails Pydantic
# validation on the full LLMSignalOutput schema (seen live with zhipu/glm-4.5).
# "function_calling" (the older tool-calling API) is far more universally supported.
_STRUCTURED_OUTPUT_METHOD = {
    'zhipu':    'function_calling',
    'cerebras': 'function_calling',
}

# provider → settings attribute holding its API key (empty string = not configured)
_PROVIDER_KEY_ATTR = {
    'google':    'gemini_api_key',
    'openai':    'openai_api_key',
    'anthropic': 'anthropic_api_key',
    'groq':      'groq_api_key',
    'cerebras':  'cerebras_api_key',
    'zhipu':     'zhipu_api_key',
}


def _get_llm(provider: str, model: str):
    if provider == 'openai':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=0.1,
            api_key=settings.openai_api_key or None,
            max_retries=2,
        )
    elif provider == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=0.1,
            api_key=settings.anthropic_api_key or None,
            max_retries=2,
        )
    elif provider == 'groq':
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=model,
            temperature=0.1,
            api_key=settings.groq_api_key or None,
            max_retries=2,
        )
    elif provider == 'cerebras':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=0.1,
            api_key=settings.cerebras_api_key or None,
            base_url="https://api.cerebras.ai/v1",
            max_retries=2,
        )
    elif provider == 'zhipu':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=0.1,
            api_key=settings.zhipu_api_key or None,
            base_url=settings.zhipu_base_url,
            max_retries=2,
        )
    else:  # google (default)
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=0.1,
            google_api_key=settings.gemini_api_key or None,
            max_retries=2,
        )


def _provider_has_key(provider: str) -> bool:
    attr = _PROVIDER_KEY_ATTR.get(provider)
    return bool(attr and getattr(settings, attr, ''))


def _parse_manual_chain(raw) -> list[tuple[str, str]]:
    """llm_fallback_chain arrives as a jsonb string from asyncpg (no codec is
    registered on the pool) or as a list when already decoded. Bad entries are
    skipped, not fatal — a broken override should not kill the cycle."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("llm_fallback_chain override is not valid JSON — ignoring")
            return []
    if not isinstance(raw, list):
        return []
    chain = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get('provider') and entry.get('model'):
            chain.append((str(entry['provider']), str(entry['model'])))
    return chain


async def build_fallback_chain(
    primary_provider: str, primary_model: str, strategy_config: dict,
) -> list[tuple[str, str]]:
    """
    Ordered fallback candidates AFTER the primary, capped at
    _MAX_FALLBACK_ATTEMPTS.

    Manual override (llm_fallback_chain) is used verbatim when set. Otherwise
    auto-derive from the registry's probe cache (no inline probing, no network):
    verified models of the same provider first (excluding the primary), then
    verified models of other providers that have an API key configured. If the
    cache is cold (startup before the background probe finishes), fall back to
    the primary provider's raw model list — an unverified attempt beats a dead
    cycle.
    """
    manual = _parse_manual_chain(strategy_config.get('llm_fallback_chain'))
    if manual:
        return manual[:_MAX_FALLBACK_ATTEMPTS]

    chain: list[tuple[str, str]] = []
    for model_id in models_registry.cached_ok_models(primary_provider):
        if model_id != primary_model:
            chain.append((primary_provider, model_id))

    for provider in models_registry.known_providers():
        if provider == primary_provider or not _provider_has_key(provider):
            continue
        for model_id in models_registry.cached_ok_models(provider):
            chain.append((provider, model_id))

    if not chain:
        try:
            raw = await models_registry.raw_models(primary_provider)
        except Exception as exc:
            logger.warning("Cold-cache raw model list for %s failed: %s", primary_provider, exc)
            raw = []
        chain = [
            (primary_provider, m['id']) for m in raw if m['id'] != primary_model
        ]

    return chain[:_MAX_FALLBACK_ATTEMPTS]


async def _attempt(provider: str, model: str, prompt, timeout: int) -> dict:
    """One structured-output call. Returns {'signal','usage'} on success;
    raises on exception/timeout; returns {'signal': None, ...} on parse failure."""
    llm = _get_llm(provider, model)
    method_kwargs = {}
    if provider in _STRUCTURED_OUTPUT_METHOD:
        method_kwargs['method'] = _STRUCTURED_OUTPUT_METHOD[provider]
    # include_raw: the plain structured wrapper returns only the parsed
    # Pydantic object and discards usage_metadata — raw is needed to
    # account actual token spend (input/output incl. thinking).
    structured_llm = llm.with_structured_output(LLMSignalOutput, include_raw=True, **method_kwargs)
    resp = await asyncio.wait_for(structured_llm.ainvoke(prompt), timeout=timeout)

    raw    = resp.get('raw')
    signal = resp.get('parsed')
    usage  = getattr(raw, 'usage_metadata', None) or {}
    llm_usage = {
        'input_tokens':  usage.get('input_tokens'),
        'output_tokens': usage.get('output_tokens'),
        'total_tokens':  usage.get('total_tokens'),
    } if usage else None

    if signal is None:
        parse_error = resp.get('parsing_error')
        raw_content = getattr(raw, 'content', None) if raw is not None else None
        error = f"structured-output parse failed: {parse_error}"
        if raw_content:
            error += f" | raw response: {str(raw_content)[:500]}"
        return {'signal': None, 'usage': llm_usage, 'error': error}

    return {'signal': signal, 'usage': llm_usage, 'error': None}


async def call_llm_chain(prompt, candidates: list[tuple[str, str]]) -> dict:
    """
    Try each (provider, model) candidate in order; first success wins.
    A failure = exception, timeout, or structured-output parse failure.

    Returns:
      signal    LLMSignalOutput | None (None = chain exhausted)
      usage     deciding call's token usage (on exhaustion: last attempt's
                usage if any tokens were spent, so cost accounting survives)
      served_by {'provider','model'} of the successful candidate, or None
      attempts  [{'provider','model','error'}] — every FAILED attempt in order
      error     summary string when exhausted, else None
    """
    attempts: list[dict] = []
    last_usage = None

    for i, (provider, model) in enumerate(candidates):
        timeout = _LLM_TIMEOUT if i == 0 else _FALLBACK_TIMEOUT
        try:
            result = await _attempt(provider, model, prompt, timeout)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            attempts.append({'provider': provider, 'model': model, 'error': error})
            logger.warning("LLM attempt %d [%s/%s] failed: %s", i + 1, provider, model, error)
            continue

        if result['usage']:
            last_usage = result['usage']

        if result['signal'] is None:
            attempts.append({'provider': provider, 'model': model, 'error': result['error']})
            logger.warning("LLM attempt %d [%s/%s] failed: %s", i + 1, provider, model, result['error'])
            continue

        if i > 0:
            logger.warning(
                "LLM fallback served: [%s/%s] answered after %d failed attempt(s); failed: %s",
                provider, model, len(attempts),
                ", ".join(f"{a['provider']}/{a['model']}" for a in attempts),
            )
        return {
            'signal':    result['signal'],
            'usage':     result['usage'],
            'served_by': {'provider': provider, 'model': model},
            'attempts':  attempts,
            'error':     None,
        }

    summary = "; ".join(f"[{a['provider']}/{a['model']}] {a['error']}" for a in attempts) \
              or "no LLM candidates to try"
    return {
        'signal':    None,
        'usage':     last_usage,
        'served_by': None,
        'attempts':  attempts,
        'error':     f"LLM chain exhausted ({len(attempts)} attempt(s)): {summary}",
    }
