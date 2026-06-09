import json
import logging
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import settings
from app.database import get_pool
from app.graph.state import AgentState
from app.prompt.builder import build_prompt, get_estimated_tokens

logger = logging.getLogger(__name__)

_VALID_ACTIONS = {
    'open_long', 'open_short', 'close_long', 'close_short',
    'hold', 'partial_close', 'adjust_stops',
}

# Appended to every prompt so the LLM knows exactly what JSON to return.
_JSON_SCHEMA_HINT = """
Return a JSON object with exactly these fields:
{
  "action": one of ["open_long","open_short","close_long","close_short","hold","partial_close","adjust_stops"],
  "confidence": float 0.0-0.95,
  "size_pct": float (% of balance, 0.1-20.0),
  "stop_loss_pct": float (distance from entry as %, e.g. 1.5),
  "take_profit_pct": float (distance from entry as %, e.g. 3.0),
  "new_sl_price": float or null,
  "new_tp_price": float or null,
  "reasoning": string citing specific indicator values
}
"""

# gemini-1.5-* is deprecated/unavailable; use Gemini 2.x equivalents
_MODEL_POSITION_OPEN = 'gemini-2.5-pro'     # high-stakes: active position management
_MODEL_NO_POSITION   = 'gemini-2.0-flash'   # scanning: faster & cheaper


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


async def node_analyze(state: AgentState) -> AgentState:
    try:
        pool        = get_pool()
        prompt      = await build_prompt(state, pool)
        tokens      = get_estimated_tokens(prompt)
        full_prompt = prompt + _JSON_SCHEMA_HINT

        model_name = _MODEL_POSITION_OPEN if state.get('position_open') else _MODEL_NO_POSITION
        client     = genai.Client(api_key=settings.gemini_api_key)

        response = client.models.generate_content(
            model=model_name,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type='application/json',
            ),
        )

        raw = json.loads(response.text)

        # Coerce action to valid value if not recognised
        if raw.get('action') not in _VALID_ACTIONS:
            raw['action'] = 'hold'

        signal = LLMSignalOutput.model_validate(raw)

        logger.info(
            "LLM [%s] → action=%s confidence=%.3f",
            model_name, signal.action, signal.confidence,
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
            'llm_signal':    None,
            'context_tokens': state.get('context_tokens'),
        }
