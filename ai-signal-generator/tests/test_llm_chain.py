"""
Unit tests for app/graph/llm_chain.py — the LLM failure-fallback chain.

Covers: primary success (no fallback), primary exception → fallback serves,
parse failure → fallback serves, timeout → fallback serves, chain exhausted,
manual override chain, auto-derivation ordering (same provider first, key-less
providers excluded), and the cold-cache raw-list fallback.

No live LLM calls: _attempt / registry functions are monkeypatched.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import models_registry
from app.config import settings
from app.graph import llm_chain
from app.graph.llm_chain import (
    LLMSignalOutput, build_fallback_chain, call_llm_chain,
)


def _signal(action='hold') -> LLMSignalOutput:
    return LLMSignalOutput(
        action=action, confidence=0.8, size_pct=1.0,
        stop_loss_pct=0.0, take_profit_pct=0.0, reasoning='test',
    )


def _ok(usage=None):
    return {'signal': _signal(), 'usage': usage or {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}, 'error': None}


def _run(coro):
    return asyncio.run(coro)


class _AttemptScript:
    """Replaces llm_chain._attempt with a scripted per-candidate outcome.
    Script entries: 'ok' | 'parse_fail' | Exception instance."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, provider, model, prompt, timeout):
        self.calls.append((provider, model))
        outcome = self.script[(provider, model)]
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == 'parse_fail':
            return {'signal': None,
                    'usage': {'input_tokens': 9, 'output_tokens': 0, 'total_tokens': 9},
                    'error': 'structured-output parse failed: boom'}
        return _ok()


# ── call_llm_chain ────────────────────────────────────────────────────────────

def test_primary_success_no_fallback(monkeypatch):
    script = _AttemptScript({('google', 'gemini-2.5-flash'): 'ok'})
    monkeypatch.setattr(llm_chain, '_attempt', script)
    result = _run(call_llm_chain('p', [('google', 'gemini-2.5-flash'), ('google', 'gemini-2.5-pro')]))
    assert result['signal'] is not None
    assert result['served_by'] == {'provider': 'google', 'model': 'gemini-2.5-flash'}
    assert result['attempts'] == []
    assert result['error'] is None
    assert script.calls == [('google', 'gemini-2.5-flash')]  # second candidate never called


def test_primary_exception_fallback_serves(monkeypatch):
    script = _AttemptScript({
        ('google', 'gemini-2.5-flash'): RuntimeError('provider down'),
        ('google', 'gemini-2.5-pro'):   'ok',
    })
    monkeypatch.setattr(llm_chain, '_attempt', script)
    result = _run(call_llm_chain('p', [('google', 'gemini-2.5-flash'), ('google', 'gemini-2.5-pro')]))
    assert result['signal'] is not None
    assert result['served_by'] == {'provider': 'google', 'model': 'gemini-2.5-pro'}
    assert len(result['attempts']) == 1
    assert result['attempts'][0]['model'] == 'gemini-2.5-flash'
    assert 'provider down' in result['attempts'][0]['error']


def test_primary_timeout_fallback_serves(monkeypatch):
    script = _AttemptScript({
        ('google', 'gemini-2.5-flash'): asyncio.TimeoutError(),
        ('groq', 'llama-3.3-70b'):      'ok',
    })
    monkeypatch.setattr(llm_chain, '_attempt', script)
    result = _run(call_llm_chain('p', [('google', 'gemini-2.5-flash'), ('groq', 'llama-3.3-70b')]))
    assert result['served_by'] == {'provider': 'groq', 'model': 'llama-3.3-70b'}
    assert len(result['attempts']) == 1


def test_parse_failure_fallback_serves(monkeypatch):
    script = _AttemptScript({
        ('zhipu', 'glm-4.5'):         'parse_fail',
        ('google', 'gemini-2.5-pro'): 'ok',
    })
    monkeypatch.setattr(llm_chain, '_attempt', script)
    result = _run(call_llm_chain('p', [('zhipu', 'glm-4.5'), ('google', 'gemini-2.5-pro')]))
    assert result['signal'] is not None
    assert result['served_by'] == {'provider': 'google', 'model': 'gemini-2.5-pro'}
    assert 'parse failed' in result['attempts'][0]['error']


def test_chain_exhausted(monkeypatch):
    script = _AttemptScript({
        ('google', 'a'): RuntimeError('e1'),
        ('google', 'b'): 'parse_fail',
        ('openai', 'c'): asyncio.TimeoutError(),
    })
    monkeypatch.setattr(llm_chain, '_attempt', script)
    result = _run(call_llm_chain('p', [('google', 'a'), ('google', 'b'), ('openai', 'c')]))
    assert result['signal'] is None
    assert result['served_by'] is None
    assert len(result['attempts']) == 3
    assert 'exhausted' in result['error']
    assert 'e1' in result['error'] and 'parse failed' in result['error']
    # token spend of the parse-failed attempt survives for cost accounting
    assert result['usage'] == {'input_tokens': 9, 'output_tokens': 0, 'total_tokens': 9}


# ── build_fallback_chain ──────────────────────────────────────────────────────

def _clear_registry_cache():
    models_registry._cache.clear()


def _cache_ok(provider, model):
    models_registry._cache[f"{provider}:{model}"] = ("ok", time.monotonic() + 1000)


def test_manual_override_respected(monkeypatch):
    _clear_registry_cache()
    override = [
        {'provider': 'openai', 'model': 'gpt-4o-mini'},
        {'provider': 'groq',   'model': 'llama-3.3-70b'},
    ]
    # asyncpg returns jsonb as a string — both forms must work
    for raw in (override, json.dumps(override)):
        chain = _run(build_fallback_chain('google', 'gemini-2.5-flash',
                                          {'llm_fallback_chain': raw}))
        assert chain == [('openai', 'gpt-4o-mini'), ('groq', 'llama-3.3-70b')]


def test_manual_override_capped_at_three():
    override = [{'provider': 'openai', 'model': f'm{i}'} for i in range(6)]
    chain = _run(build_fallback_chain('google', 'g', {'llm_fallback_chain': override}))
    assert len(chain) == 3


def test_auto_derivation_same_provider_first(monkeypatch):
    _clear_registry_cache()
    _cache_ok('google', 'gemini-2.5-flash')   # primary — must be excluded
    _cache_ok('google', 'gemini-2.5-pro')
    _cache_ok('openai', 'gpt-4o-mini')
    _cache_ok('groq',   'llama-3.3-70b')
    monkeypatch.setattr(settings, 'gemini_api_key', 'k')
    monkeypatch.setattr(settings, 'openai_api_key', 'k')
    monkeypatch.setattr(settings, 'groq_api_key', '')   # no key → excluded

    chain = _run(build_fallback_chain('google', 'gemini-2.5-flash', {}))
    assert chain[0] == ('google', 'gemini-2.5-pro')          # same provider first
    assert ('openai', 'gpt-4o-mini') in chain
    assert all(p != 'groq' for p, _ in chain)                # key-less provider excluded
    assert ('google', 'gemini-2.5-flash') not in chain       # primary excluded


def test_cold_cache_raw_list_fallback(monkeypatch):
    _clear_registry_cache()

    async def fake_raw(provider):
        assert provider == 'google'
        return [{'id': 'gemini-2.5-flash'}, {'id': 'gemini-2.5-pro'},
                {'id': 'gemini-2.0-flash'}, {'id': 'gemini-1.5-pro'}, {'id': 'extra'}]

    monkeypatch.setattr(models_registry, 'raw_models', fake_raw)
    chain = _run(build_fallback_chain('google', 'gemini-2.5-flash', {}))
    # primary excluded, capped at 3, order preserved
    assert chain == [('google', 'gemini-2.5-pro'), ('google', 'gemini-2.0-flash'),
                     ('google', 'gemini-1.5-pro')]


def test_invalid_override_falls_back_to_auto(monkeypatch):
    _clear_registry_cache()
    _cache_ok('google', 'gemini-2.5-pro')
    monkeypatch.setattr(settings, 'gemini_api_key', 'k')
    chain = _run(build_fallback_chain('google', 'gemini-2.5-flash',
                                      {'llm_fallback_chain': 'not-json{{'}))
    assert chain == [('google', 'gemini-2.5-pro')]
