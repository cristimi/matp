import asyncio
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

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


_LLM_TIMEOUT = 90  # seconds — hard ceiling per LLM call


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


async def node_analyze(state: AgentState) -> AgentState:
    try:
        pool   = get_pool()
        prompt = await build_prompt(state, pool)
        tokens = get_estimated_tokens(prompt)

        provider = state['strategy_config'].get('llm_provider', _DEFAULT_PROVIDER)
        model    = state['strategy_config'].get('llm_model',    _DEFAULT_MODEL)

        llm = _get_llm(provider, model)
        # include_raw: the plain structured wrapper returns only the parsed
        # Pydantic object and discards usage_metadata — raw is needed to
        # account actual token spend (input/output incl. thinking).
        structured_llm = llm.with_structured_output(LLMSignalOutput, include_raw=True)
        resp = await asyncio.wait_for(
            structured_llm.ainvoke(prompt), timeout=_LLM_TIMEOUT
        )

        raw    = resp.get('raw')
        signal = resp.get('parsed')
        usage  = getattr(raw, 'usage_metadata', None) or {}
        llm_usage = {
            'input_tokens':  usage.get('input_tokens'),
            'output_tokens': usage.get('output_tokens'),
            'total_tokens':  usage.get('total_tokens'),
        } if usage else None

        if signal is None:
            # Tokens were spent even though parsing failed — keep the usage.
            parse_error = resp.get('parsing_error')
            raw_content = getattr(raw, 'content', None) if raw is not None else None
            llm_error = f"[{provider}/{model}] structured-output parse failed: {parse_error}"
            if raw_content:
                llm_error += f" | raw response: {str(raw_content)[:500]}"
            logger.error("node_analyze %s", llm_error)
            return {
                **state,
                'llm_signal':     None,
                'llm_error':      llm_error,
                'llm_usage':      llm_usage,
                'context_tokens': tokens,
            }

        logger.info(
            "LLM [%s/%s] → action=%s confidence=%.3f tokens=%s",
            provider, model, signal.action, signal.confidence,
            llm_usage.get('total_tokens') if llm_usage else 'n/a',
        )

        return {
            **state,
            'llm_signal':     signal.model_dump(),
            'llm_usage':      llm_usage,
            'context_tokens': tokens,
        }

    except Exception as exc:
        provider = state['strategy_config'].get('llm_provider', _DEFAULT_PROVIDER)
        model    = state['strategy_config'].get('llm_model',    _DEFAULT_MODEL)
        llm_error = f"[{provider}/{model}] {type(exc).__name__}: {exc}"
        logger.error("node_analyze error: %s", llm_error)
        return {
            **state,
            'llm_signal':     None,
            'llm_error':      llm_error,
            'llm_usage':      None,
            'context_tokens': state.get('context_tokens'),
        }
