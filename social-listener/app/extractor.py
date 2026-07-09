import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)
EXTRACTOR_VERSION = "v1"

SYSTEM_PROMPT = """You extract a crypto trader's STATED position changes from a social post.
You are a transcriber, not an analyst. You never decide whether a trade is good or likely.

Set is_actionable=true ONLY when the post asserts a NEW, concrete change to the trader's OWN
position: opening, flipping, or fully closing a position.

Set is_actionable=false for everything else, including:
- P&L brags / recaps / "up X RR" / "TP hit" celebrations of an EXISTING trade
- macro commentary, predictions, "looking for an entry", "a long next?"
- hype, community chatter, emoji-only posts, anything without a concrete new entry/exit

action_type:
  OPEN  - newly entering a position
  FLIP  - closing one side and entering the opposite in the same post
  CLOSE - fully closing a position
  ADD / TRIM - scaling an existing position (always set is_actionable=false for these)
  NONE  - not a position change

asset           : uppercase base symbol (BTC, ETH). null if none.
direction       : LONG or SHORT, the NEW resulting direction. null if none.
reference_price : the entry/exit price the trader cites, as a number ("66.7k" -> 66700). null if absent.
confidence      : 0..1, how clearly the text states a concrete position change.

Be conservative. When unsure, is_actionable=false."""


class SocialExtraction(BaseModel):
    is_actionable: bool
    action_type: Literal["OPEN", "FLIP", "CLOSE", "ADD", "TRIM", "NONE"]
    asset: Optional[str] = None
    direction: Optional[Literal["LONG", "SHORT"]] = None
    reference_price: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    reasoning: Optional[str] = None


def _build_llm():
    p = settings.extractor_provider.lower()
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=settings.extractor_model,
                             temperature=settings.extractor_temperature,
                             api_key=settings.anthropic_api_key)
    if p == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=settings.extractor_model,
                                      temperature=settings.extractor_temperature,
                                      google_api_key=settings.gemini_api_key)
    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=settings.extractor_model,
                          temperature=settings.extractor_temperature,
                          api_key=settings.openai_api_key)
    if p == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=settings.extractor_model,
                        temperature=settings.extractor_temperature,
                        api_key=settings.groq_api_key)
    raise ValueError(f"unknown extractor_provider: {p}")


_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        # include_raw: the plain structured wrapper returns only the parsed
        # Pydantic object and discards usage_metadata — raw is needed to
        # account actual token spend (input/output incl. thinking).
        _llm = _build_llm().with_structured_output(SocialExtraction, include_raw=True)
    return _llm


_WHITELIST = {s.strip().upper() for s in settings.asset_whitelist.split(",") if s.strip()}


async def extract(raw_text: str, preview_text: str) -> dict:
    combined = (
        f"NATIVE POST:\n{raw_text or '(none)'}\n\n"
        f"LINKED POST PREVIEW:\n{preview_text or '(none)'}"
    )
    llm_usage = None
    try:
        resp: dict = await _get_llm().ainvoke(
            [("system", SYSTEM_PROMPT), ("human", combined)]
        )
        raw = resp.get("raw")
        result: Optional[SocialExtraction] = resp.get("parsed")
        usage = getattr(raw, "usage_metadata", None) or {}
        if usage:
            llm_usage = {
                "input_tokens":  usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens":  usage.get("total_tokens"),
            }
        if result is None:
            # Tokens were spent even though structured parsing failed — keep the usage.
            log.warning("extraction parse failed: %s", resp.get("parsing_error"))
            result = SocialExtraction(
                is_actionable=False, action_type="NONE", confidence=0.0,
                reasoning=f"parse_error: {resp.get('parsing_error')}",
            )
    except Exception as e:  # noqa: BLE001
        log.warning("extraction failed: %s", e)
        result = SocialExtraction(
            is_actionable=False, action_type="NONE", confidence=0.0,
            reasoning=f"extraction_error: {e}",
        )

    asset = (result.asset or "").upper() or None
    # Force scaling events to non-actionable per the contract.
    is_actionable = result.is_actionable and result.action_type not in ("ADD", "TRIM")
    return {
        "is_actionable": is_actionable,
        "action_type": result.action_type,
        "asset": asset,
        "direction": result.direction,
        "reference_price": result.reference_price,
        "confidence": result.confidence,
        "in_whitelist": (asset in _WHITELIST) if asset else False,
        "model": f"{settings.extractor_provider}:{settings.extractor_model}",
        "extractor_version": EXTRACTOR_VERSION,
        "raw_llm_json": result.model_dump(),
        "input_tokens":  llm_usage.get("input_tokens")  if llm_usage else None,
        "output_tokens": llm_usage.get("output_tokens") if llm_usage else None,
        "total_tokens":  llm_usage.get("total_tokens")  if llm_usage else None,
    }
