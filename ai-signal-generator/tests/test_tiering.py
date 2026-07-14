"""
Unit tests for scout/premium tiering (app/graph/nodes/node_analyze.py).

Covers: scout NULL → single premium call; scout hold → final (no premium
call, tier/token placement correct); scout non-hold action → premium decides;
scout failure → premium; deterministic triggers (first cycle, fit_quality
change, Nth-cycle force) skipping the scout; premium chain exhausted after
scout escalation (scout output never promoted).

No live LLM calls and no DB: llm_chain internals and the pool are faked.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.graph import llm_chain
from app.graph.llm_chain import LLMSignalOutput
from app.graph.nodes import node_analyze as na


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeConn:
    def __init__(self, prev_row, cycles_since):
        self.prev_row     = prev_row
        self.cycles_since = cycles_since

    async def fetchrow(self, _q, *_a):
        return self.prev_row

    async def fetchval(self, _q, *_a):
        return self.cycles_since


class FakePool:
    def __init__(self, prev_row=None, cycles_since=0):
        self.conn = FakeConn(prev_row, cycles_since)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _signal(action='hold', confidence=0.8) -> LLMSignalOutput:
    return LLMSignalOutput(
        action=action, confidence=confidence, size_pct=1.0,
        stop_loss_pct=0.0, take_profit_pct=0.0, reasoning='test',
    )


SCOUT_USAGE   = {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}
PREMIUM_USAGE = {'input_tokens': 900, 'output_tokens': 90, 'total_tokens': 990}


def _state(sc_extra=None, geometry=None) -> dict:
    sc = {'llm_provider': 'google', 'llm_model': 'gemini-2.5-pro',
          'template_id': 'trend_following', **(sc_extra or {})}
    return {
        'strategy_id':     'test-strat',
        'strategy_config': sc,
        'position_open':   False,
        'geometry_data':   geometry,
        'context_tokens':  None,
    }


def _wire(monkeypatch, *, pool, scout_outcome=None, premium_result=None):
    """Patch node_analyze's collaborators. Returns a call recorder."""
    calls = {'scout': [], 'premium': []}

    monkeypatch.setattr(na, 'get_pool', lambda: pool)

    async def fake_build_prompt(_state, _pool):
        return 'PROMPT'
    monkeypatch.setattr(na, 'build_prompt', fake_build_prompt)
    monkeypatch.setattr(na, 'get_estimated_tokens', lambda _p: 1234)

    async def fake_attempt(provider, model, prompt, timeout, api_key=None):
        calls['scout'].append((provider, model))
        if isinstance(scout_outcome, Exception):
            raise scout_outcome
        return scout_outcome
    monkeypatch.setattr(llm_chain, '_attempt', fake_attempt)

    async def fake_chain(_pp, _pm, _sc):
        return []
    monkeypatch.setattr(llm_chain, 'build_fallback_chain', fake_chain)

    async def fake_call_llm_chain(prompt, candidates):
        calls['premium'].append(candidates)
        return premium_result
    monkeypatch.setattr(llm_chain, 'call_llm_chain', fake_call_llm_chain)

    return calls


def _premium_ok(action='open_long', attempts=None):
    return {'signal': _signal(action), 'usage': PREMIUM_USAGE,
            'served_by': {'provider': 'google', 'model': 'gemini-2.5-pro'},
            'attempts': attempts or [], 'error': None}


def _run(state, monkeypatch, **wire_kwargs):
    _wire_calls = _wire(monkeypatch, **wire_kwargs)
    out = asyncio.run(na.node_analyze(state))
    return out, _wire_calls


# prev row matching current geometry so no deterministic trigger fires
_NO_TRIGGER_POOL = lambda: FakePool(prev_row={'fit_quality': 'strong'}, cycles_since=0)
_GEOM = {'fit_quality': 'strong'}


# ── Scout NULL → current behavior ────────────────────────────────────────────

def test_scout_null_single_premium_call(monkeypatch):
    out, calls = _run(_state(), monkeypatch, pool=FakePool(),
                      premium_result=_premium_ok())
    assert calls['scout'] == []            # scout never called
    assert len(calls['premium']) == 1
    assert out['llm_tier'] == 'premium'
    assert out['llm_usage'] == PREMIUM_USAGE
    assert out['scout_usage'] is None
    assert out['llm_signal']['action'] == 'open_long'


# ── Scout hold → final ────────────────────────────────────────────────────────

def test_scout_hold_is_final_no_premium_call(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    out, calls = _run(_state(sc, _GEOM), monkeypatch, pool=_NO_TRIGGER_POOL(),
                      scout_outcome={'signal': _signal('hold'), 'usage': SCOUT_USAGE, 'error': None},
                      premium_result=_premium_ok())
    assert calls['scout'] == [('groq', 'llama-3.1-8b')]
    assert calls['premium'] == []          # premium call saved
    assert out['llm_tier'] == 'scout'
    # one call happened → its usage lives in the MAIN columns, scout columns NULL
    assert out['llm_usage'] == SCOUT_USAGE
    assert out['scout_usage'] is None
    assert out['llm_served_by'] == {'provider': 'groq', 'model': 'llama-3.1-8b'}
    assert out['llm_signal']['action'] == 'hold'


# ── Scout proposes action → premium decides ──────────────────────────────────

def test_scout_action_escalates_premium_decides(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    out, calls = _run(_state(sc, _GEOM), monkeypatch, pool=_NO_TRIGGER_POOL(),
                      scout_outcome={'signal': _signal('open_short'), 'usage': SCOUT_USAGE, 'error': None},
                      premium_result=_premium_ok('close_long'))
    assert len(calls['premium']) == 1
    assert out['llm_tier'] == 'scout_escalated'
    assert out['llm_signal']['action'] == 'close_long'   # premium's output, not scout's
    assert out['llm_usage'] == PREMIUM_USAGE             # deciding call in main columns
    assert out['scout_usage'] == SCOUT_USAGE             # both tiers ran → scout columns set


# ── Scout failure → premium (no chain walk for scout) ────────────────────────

def test_scout_failure_escalates_to_premium(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    out, calls = _run(_state(sc, _GEOM), monkeypatch, pool=_NO_TRIGGER_POOL(),
                      scout_outcome=RuntimeError('scout down'),
                      premium_result=_premium_ok('hold'))
    assert calls['scout'] == [('groq', 'llama-3.1-8b')]  # exactly ONE scout attempt
    assert len(calls['premium']) == 1
    assert out['llm_tier'] == 'scout_escalated'
    assert out['llm_signal']['action'] == 'hold'


def test_scout_parse_failure_escalates_and_keeps_scout_spend(monkeypatch):
    sc = {'llm_scout_provider': 'zhipu', 'llm_scout_model': 'glm-4.5-flash'}
    out, _ = _run(_state(sc, _GEOM), monkeypatch, pool=_NO_TRIGGER_POOL(),
                  scout_outcome={'signal': None, 'usage': SCOUT_USAGE, 'error': 'parse failed'},
                  premium_result=_premium_ok('hold'))
    assert out['llm_tier'] == 'scout_escalated'
    assert out['scout_usage'] == SCOUT_USAGE   # failed scout still spent tokens


# ── Premium chain exhausted after escalation — scout never promoted ──────────

def test_premium_exhausted_scout_not_promoted(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    exhausted = {'signal': None, 'usage': None, 'served_by': None,
                 'attempts': [{'provider': 'google', 'model': 'gemini-2.5-pro', 'error': 'down'}],
                 'error': 'LLM chain exhausted (1 attempt(s)): [google/gemini-2.5-pro] down'}
    out, _ = _run(_state(sc, _GEOM), monkeypatch, pool=_NO_TRIGGER_POOL(),
                  scout_outcome={'signal': _signal('open_long'), 'usage': SCOUT_USAGE, 'error': None},
                  premium_result=exhausted)
    assert out['llm_signal'] is None                     # scout's open_long NOT promoted
    assert 'exhausted' in out['llm_error']
    assert out['fallback_attempts'] == exhausted['attempts']
    assert out['scout_usage'] == SCOUT_USAGE


# ── Deterministic triggers ────────────────────────────────────────────────────

def test_first_cycle_forces_premium(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    out, calls = _run(_state(sc, _GEOM), monkeypatch,
                      pool=FakePool(prev_row=None),      # no history
                      premium_result=_premium_ok('hold'))
    assert calls['scout'] == []
    assert out['llm_tier'] == 'premium'


def test_fit_quality_change_forces_premium(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b'}
    out, calls = _run(_state(sc, {'fit_quality': 'strong'}), monkeypatch,
                      pool=FakePool(prev_row={'fit_quality': 'weak'}, cycles_since=0),
                      premium_result=_premium_ok('hold'))
    assert calls['scout'] == []
    assert out['llm_tier'] == 'premium'


def test_nth_cycle_force(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b',
          'premium_force_interval': 12}
    # 11 scout cycles since last premium → this is the 12th → force
    out, calls = _run(_state(sc, _GEOM), monkeypatch,
                      pool=FakePool(prev_row={'fit_quality': 'strong'}, cycles_since=11),
                      premium_result=_premium_ok('hold'))
    assert calls['scout'] == []
    assert out['llm_tier'] == 'premium'


def test_nth_cycle_not_yet_reached_scout_runs(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b',
          'premium_force_interval': 12}
    out, calls = _run(_state(sc, _GEOM), monkeypatch,
                      pool=FakePool(prev_row={'fit_quality': 'strong'}, cycles_since=5),
                      scout_outcome={'signal': _signal('hold'), 'usage': SCOUT_USAGE, 'error': None},
                      premium_result=_premium_ok())
    assert calls['scout'] == [('groq', 'llama-3.1-8b')]
    assert out['llm_tier'] == 'scout'


def test_force_interval_one_always_premium(monkeypatch):
    sc = {'llm_scout_provider': 'groq', 'llm_scout_model': 'llama-3.1-8b',
          'premium_force_interval': 1}
    out, calls = _run(_state(sc, _GEOM), monkeypatch,
                      pool=FakePool(prev_row={'fit_quality': 'strong'}, cycles_since=0),
                      premium_result=_premium_ok('hold'))
    assert calls['scout'] == []
    assert out['llm_tier'] == 'premium'
