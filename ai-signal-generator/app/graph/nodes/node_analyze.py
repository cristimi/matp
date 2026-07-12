import logging

from app.database import get_pool
from app.graph import llm_chain
from app.graph.llm_chain import _DEFAULT_PROVIDER, _DEFAULT_MODEL
from app.graph.state import AgentState
from app.prompt.builder import build_prompt, get_estimated_tokens

logger = logging.getLogger(__name__)


async def _deterministic_premium_trigger(state: AgentState, pool) -> str | None:
    """
    Reasons this cycle must skip the scout and go straight to the premium
    model. Evaluated only from AgentState plus one cheap indexed lookup on
    ai_signal_log (previous cycle) — never a new data fetch.

    Implemented triggers:
      - first_cycle: no ai_signal_log history for the strategy, so no scout
        baseline exists yet.
      - fit_quality_changed: geometry fit_quality differs from the previous
        cycle's logged geometry_data.
      - premium_force_interval: every Nth cycle since the last
        premium-deciding cycle (llm_tier != 'scout'; NULL/historical rows
        count as premium).

    NOT implementable without new data fetches (see 2026-07-12 report):
      - SL/TP proximity — the open position's SL/TP prices exist only on the
        exchange/listener side; AgentState and strategy_positions don't
        carry them.
      - volatility_regime change — the regime is computed per cycle but never
        persisted, so there is no previous value to compare against.
    """
    strategy_id = state['strategy_id']
    sc          = state['strategy_config']

    async with pool.acquire() as conn:
        prev = await conn.fetchrow(
            """
            SELECT geometry_data->>'fit_quality' AS fit_quality
            FROM ai_signal_log
            WHERE strategy_id = $1
            ORDER BY triggered_at DESC LIMIT 1
            """,
            strategy_id,
        )
        if prev is None:
            return 'first_cycle'

        cur_fq  = (state.get('geometry_data') or {}).get('fit_quality')
        prev_fq = prev['fit_quality']
        if cur_fq is not None and prev_fq is not None and cur_fq != prev_fq:
            return f'fit_quality_changed:{prev_fq}->{cur_fq}'

        # Scout-final cycles since the last premium-deciding cycle. This cycle
        # would be number (count + 1); force when that reaches the interval.
        cycles_since = await conn.fetchval(
            """
            SELECT COUNT(*) FROM ai_signal_log
            WHERE strategy_id = $1
              AND triggered_at > COALESCE(
                (SELECT MAX(triggered_at) FROM ai_signal_log
                 WHERE strategy_id = $1 AND llm_tier IS DISTINCT FROM 'scout'),
                'epoch'::timestamptz)
            """,
            strategy_id,
        )
        force_interval = int(sc.get('premium_force_interval') or 12)
        if int(cycles_since or 0) + 1 >= force_interval:
            return f'premium_force_interval:{force_interval}'

    return None


async def _premium_result(prompt, provider: str, model: str, strategy_config: dict) -> dict:
    """Premium call with the feature-A fallback chain."""
    fallbacks  = await llm_chain.build_fallback_chain(provider, model, strategy_config)
    candidates = [(provider, model)] + fallbacks
    return await llm_chain.call_llm_chain(prompt, candidates)


async def _scout_result(prompt, provider: str, model: str) -> dict:
    """Single scout attempt — deliberately NO fallback chain (walking the
    chain for the cheap tier would defeat the cost purpose). Any failure
    escalates directly to premium."""
    try:
        result = await llm_chain._attempt(provider, model, prompt, llm_chain._LLM_TIMEOUT)
    except Exception as exc:
        return {'signal': None, 'usage': None, 'error': f"{type(exc).__name__}: {exc}"}
    return result


def _state_out(state, *, signal=None, error=None, usage=None, tier=None,
               served_by=None, scout_usage=None, attempts=None, tokens=None) -> AgentState:
    return {
        **state,
        'llm_signal':        signal.model_dump() if signal is not None else None,
        'llm_error':         error,
        'llm_usage':         usage,
        'llm_tier':          tier,
        'llm_served_by':     served_by,
        'scout_usage':       scout_usage,
        'fallback_attempts': attempts or None,
        'context_tokens':    tokens,
    }


async def node_analyze(state: AgentState) -> AgentState:
    sc       = state['strategy_config']
    provider = sc.get('llm_provider', _DEFAULT_PROVIDER)
    model    = sc.get('llm_model',    _DEFAULT_MODEL)
    try:
        pool   = get_pool()
        prompt = await build_prompt(state, pool)
        tokens = get_estimated_tokens(prompt)

        scout_provider = sc.get('llm_scout_provider')
        scout_model    = sc.get('llm_scout_model')
        scout_usage    = None
        scout_ran      = False

        if scout_provider and scout_model:
            trigger = await _deterministic_premium_trigger(state, pool)
            if trigger:
                logger.info(
                    "Scout skipped strategy=%s — deterministic premium trigger: %s",
                    state['strategy_id'], trigger,
                )
            else:
                scout = await _scout_result(prompt, scout_provider, scout_model)
                scout_usage = scout['usage']
                if scout['signal'] is None:
                    logger.warning(
                        "Scout [%s/%s] failed strategy=%s — escalating to premium: %s",
                        scout_provider, scout_model, state['strategy_id'], scout['error'],
                    )
                    scout_ran = True  # tokens may have been spent; keep them in scout columns
                elif scout['signal'].action == 'hold':
                    # Scout hold is final — no premium call. One call happened,
                    # so its usage goes in the MAIN token columns and the scout
                    # columns stay NULL.
                    signal = scout['signal']
                    logger.info(
                        "LLM [%s/%s] tier=scout → action=hold confidence=%.3f tokens=%s (premium call saved)",
                        scout_provider, scout_model, signal.confidence,
                        (scout['usage'] or {}).get('total_tokens') or 'n/a',
                    )
                    return _state_out(
                        state, signal=signal, usage=scout['usage'], tier='scout',
                        served_by={'provider': scout_provider, 'model': scout_model},
                        tokens=tokens,
                    )
                else:
                    logger.info(
                        "Scout [%s/%s] proposed action=%s strategy=%s — escalating to premium (scout output is never executed)",
                        scout_provider, scout_model, scout['signal'].action, state['strategy_id'],
                    )
                    scout_ran = True

        result = await _premium_result(prompt, provider, model, sc)

        if result['signal'] is None:
            # Chain exhausted. The scout's proposal (if any) is NEVER promoted.
            logger.error("node_analyze %s", result['error'])
            return _state_out(
                state, error=result['error'], usage=result['usage'],
                scout_usage=scout_usage, attempts=result['attempts'], tokens=tokens,
            )

        signal    = result['signal']
        served_by = result['served_by']
        if scout_ran:
            tier = 'scout_escalated'
        elif result['attempts']:
            tier = 'fallback'
        else:
            tier = 'premium'

        logger.info(
            "LLM [%s/%s] tier=%s → action=%s confidence=%.3f tokens=%s",
            served_by['provider'], served_by['model'], tier,
            signal.action, signal.confidence,
            (result['usage'] or {}).get('total_tokens') or 'n/a',
        )

        return _state_out(
            state, signal=signal, usage=result['usage'], tier=tier,
            served_by=served_by, scout_usage=scout_usage,
            attempts=result['attempts'], tokens=tokens,
        )

    except Exception as exc:
        llm_error = f"[{provider}/{model}] {type(exc).__name__}: {exc}"
        logger.error("node_analyze error: %s", llm_error)
        return _state_out(
            state, error=llm_error, tokens=state.get('context_tokens'),
        )
