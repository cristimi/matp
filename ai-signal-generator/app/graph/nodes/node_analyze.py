import logging

from app.database import get_pool
from app.graph.llm_chain import (
    _DEFAULT_PROVIDER, _DEFAULT_MODEL,
    build_fallback_chain, call_llm_chain,
)
from app.graph.state import AgentState
from app.prompt.builder import build_prompt, get_estimated_tokens

logger = logging.getLogger(__name__)


async def node_analyze(state: AgentState) -> AgentState:
    provider = state['strategy_config'].get('llm_provider', _DEFAULT_PROVIDER)
    model    = state['strategy_config'].get('llm_model',    _DEFAULT_MODEL)
    try:
        pool   = get_pool()
        prompt = await build_prompt(state, pool)
        tokens = get_estimated_tokens(prompt)

        fallbacks  = await build_fallback_chain(provider, model, state['strategy_config'])
        candidates = [(provider, model)] + fallbacks
        result     = await call_llm_chain(prompt, candidates)

        if result['signal'] is None:
            logger.error("node_analyze %s", result['error'])
            return {
                **state,
                'llm_signal':        None,
                'llm_error':         result['error'],
                'llm_usage':         result['usage'],
                'llm_tier':          None,
                'llm_served_by':     None,
                'fallback_attempts': result['attempts'] or None,
                'context_tokens':    tokens,
            }

        signal    = result['signal']
        served_by = result['served_by']
        tier      = 'premium' if not result['attempts'] else 'fallback'

        logger.info(
            "LLM [%s/%s] tier=%s → action=%s confidence=%.3f tokens=%s",
            served_by['provider'], served_by['model'], tier,
            signal.action, signal.confidence,
            (result['usage'] or {}).get('total_tokens') or 'n/a',
        )

        return {
            **state,
            'llm_signal':        signal.model_dump(),
            'llm_usage':         result['usage'],
            'llm_tier':          tier,
            'llm_served_by':     served_by,
            'fallback_attempts': result['attempts'] or None,
            'context_tokens':    tokens,
        }

    except Exception as exc:
        llm_error = f"[{provider}/{model}] {type(exc).__name__}: {exc}"
        logger.error("node_analyze error: %s", llm_error)
        return {
            **state,
            'llm_signal':        None,
            'llm_error':         llm_error,
            'llm_usage':         None,
            'llm_tier':          None,
            'llm_served_by':     None,
            'fallback_attempts': None,
            'context_tokens':    state.get('context_tokens'),
        }
