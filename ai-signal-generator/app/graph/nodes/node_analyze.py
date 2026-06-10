import logging
from typing import Literal, Optional

from pydantic import BaseModel

from app.config import settings
from app.database import get_pool
from app.graph.state import AgentState
from app.prompt.builder import build_prompt, get_estimated_tokens

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER = 'google'
_DEFAULT_MODEL    = 'gemini-2.5-flash'


class LLMSignalOutput(BaseModel):
    action:          Literal[
        'open_long', 'open_short', 'close_long', 'close_short',
        'hold', 'partial_close', 'adjust_stops'
    ]
    confidence:      float
    size_pct:        float
    stop_loss_pct:   float
    take_profit_pct: float
    new_sl_price:    Optional[float] = None
    new_tp_price:    Optional[float] = None
    reasoning:       str


def _get_llm(provider: str, model: str):
    if provider == 'openai':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=0.1,
            api_key=settings.openai_api_key or None,
        )
    elif provider == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=0.1,
            api_key=settings.anthropic_api_key or None,
        )
    else:  # google (default)
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=0.1,
            google_api_key=settings.gemini_api_key or None,
        )


async def node_analyze(state: AgentState) -> AgentState:
    try:
        pool   = get_pool()
        prompt = await build_prompt(state, pool)
        tokens = get_estimated_tokens(prompt)

        provider = state['strategy_config'].get('llm_provider', _DEFAULT_PROVIDER)
        model    = state['strategy_config'].get('llm_model',    _DEFAULT_MODEL)

        llm            = _get_llm(provider, model)
        structured_llm = llm.with_structured_output(LLMSignalOutput)
        signal: LLMSignalOutput = await structured_llm.ainvoke(prompt)

        logger.info(
            "LLM [%s/%s] → action=%s confidence=%.3f",
            provider, model, signal.action, signal.confidence,
        )

        return {
            **state,
            'llm_signal':     signal.model_dump(),
            'context_tokens': tokens,
        }

    except Exception as exc:
        logger.error("node_analyze error: %s", exc)
        return {
            **state,
            'llm_signal':     None,
            'context_tokens': state.get('context_tokens'),
        }
