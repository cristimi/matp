import base64
import logging
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)
EXTRACTOR_VERSION = "v2"  # v2: reads the attached chart image alongside the text

SYSTEM_PROMPT = """You extract a crypto trader's STATED position changes from a social post.
You are a transcriber, not an analyst. You never decide whether a trade is good or likely.

A post may include an IMAGE — usually an annotated TradingView chart. Read the annotations
on it: text the trader has drawn on the chart ("Closed longs", "Flipped longs into Shorts",
"Entry", a marked level) is a statement about their position and counts exactly like text in
the post. Read the instrument name from the chart header (e.g. "Bitcoin / TetherUS" -> BTC).
Do NOT interpret the price action itself — candles, trendlines, indicators and drawings with
no words are not statements. Never infer a trade from what the chart "looks like".

Set is_actionable=true ONLY when the post asserts a NEW, concrete change to the trader's OWN
position: opening, flipping, or fully closing a position.

Set is_actionable=false for everything else, including:
- P&L brags / recaps / "up X RR" / "TP hit" celebrations of an EXISTING trade
- macro commentary, predictions, "looking for an entry", "a long next?"
- hype, community chatter, emoji-only posts, anything without a concrete new entry/exit
- RETROSPECTIVE chart annotations: a marker on an earlier candle explaining a trade the
  trader already took and already talked about is a recap, not a new call. A chart is a new
  call only when the post presents the change as being made now. When a chart annotation
  sits far from the chart's latest price, treat it as retrospective.

action_type:
  OPEN  - newly entering a position
  FLIP  - closing one side and entering the opposite in the same post
  CLOSE - fully closing a position
  ADD / TRIM - scaling an existing position (always set is_actionable=false for these)
  NONE  - not a position change

DIRECTION FROM A TRADE CARD. Posts often list levels without the words "long" or "short":

    Entry: 66.2k
    Risk off the trade: ...
    Lock in W 64.8k: celebrations
    TP 2 to be revealed

Work the direction out from where the PROFIT levels sit relative to the entry:
  - profit levels BELOW the entry  -> SHORT
  - profit levels ABOVE the entry  -> LONG

Use the wording to tell a profit level from a stop. "TP", "target", "take profit",
"lock in W", "lock in a win", "celebrations", "secure profit" mark a PROFIT level.
"SL", "stop", "stopped", "invalidation", "risk off", "cut", "break even" mark a STOP.

A level below the entry is NOT automatically a stop-loss — on a short, the profit sits
below the entry. Never infer the direction from one level's position alone; identify which
levels are profits first, then compare them to the entry.

If the levels are ambiguous and the post never states a side, leave direction null and
lower confidence rather than guessing.

asset           : uppercase base symbol (BTC, ETH). null if none.
direction       : LONG or SHORT, the NEW resulting direction. null if none.
reference_price : the entry/exit price the trader cites, as a number ("66.7k" -> 66700). Use a
                  price written in an annotation if the text gives none. null if absent.
confidence      : 0..1, how clearly the post states a concrete position change.
evidence        : where the position change came from — "text", "image", "both", or "none".

Be conservative. When unsure, is_actionable=false."""


class SocialExtraction(BaseModel):
    is_actionable: bool
    action_type: Literal["OPEN", "FLIP", "CLOSE", "ADD", "TRIM", "NONE"]
    asset: Optional[str] = None
    direction: Optional[Literal["LONG", "SHORT"]] = None
    reference_price: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    evidence: Literal["text", "image", "both", "none"] = "none"
    reasoning: Optional[str] = None


# Provider slug in llm_keys / settings for each extractor_provider value.
_KEY_SLUG = {"anthropic": "anthropic", "google": "gemini",
             "openai": "openai", "groq": "groq"}

# Default model per provider, used when a fallback entry names no model.
_DEFAULT_MODEL = {"anthropic": "claude-sonnet-4-6", "google": "gemini-3.6-flash",
                  "openai": "gpt-4o", "groq": "llama-3.3-70b-versatile"}


def _build_llm(provider: str, model: str, api_key: str):
    p = provider.lower()
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=settings.extractor_temperature,
                             api_key=api_key)
    if p == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=settings.extractor_temperature,
                                      google_api_key=api_key)
    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=settings.extractor_temperature,
                          api_key=api_key)
    if p == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=settings.extractor_temperature,
                        api_key=api_key)
    raise ValueError(f"unknown extractor provider: {p}")


def _keys_for(provider: str) -> list[str]:
    """Every usable key for a provider, priority order, DB keys before the env var."""
    slug = _KEY_SLUG.get(provider.lower(), provider.lower())
    keys = list(settings.provider_keys.get(slug) or [])
    env = getattr(settings, f"{slug}_api_key", "") or ""
    if env and env not in keys:
        keys.append(env)
    return keys


def _attempts() -> list[tuple[str, str, str]]:
    """(provider, model, api_key) to try in order: primary first, then the chain.

    Each provider contributes one attempt per key it holds, so a dead
    highest-priority key falls through to its sibling before the next provider.
    """
    chain: list[tuple[str, str]] = [
        (settings.extractor_provider.lower(), settings.extractor_model)
    ]
    for entry in settings.extractor_fallbacks.split(","):
        entry = entry.strip()
        if not entry:
            continue
        provider, _, model = entry.partition(":")
        provider = provider.lower()
        chain.append((provider, model or _DEFAULT_MODEL.get(provider, "")))

    out, seen = [], set()
    for provider, model in chain:
        if not model or (provider, model) in seen:
            continue
        seen.add((provider, model))
        for key in _keys_for(provider):
            out.append((provider, model, key))
    return out


_llm_cache: dict[tuple[str, str, str], object] = {}


def _get_llm(provider: str, model: str, api_key: str):
    # include_raw: the plain structured wrapper returns only the parsed
    # Pydantic object and discards usage_metadata — raw is needed to
    # account actual token spend (input/output incl. thinking).
    cached = _llm_cache.get((provider, model, api_key))
    if cached is None:
        cached = _build_llm(provider, model, api_key).with_structured_output(
            SocialExtraction, include_raw=True)
        _llm_cache[(provider, model, api_key)] = cached
    return cached


_WHITELIST = {s.strip().upper() for s in settings.asset_whitelist.split(",") if s.strip()}


def _image_block(image_bytes: bytes, provider: str) -> dict:
    """A base64 image content block in the shape the given provider expects."""
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    media_type = settings.image_media_type
    if provider.lower() == "anthropic":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
    # OpenAI-style data URI — accepted by ChatOpenAI, ChatGoogleGenerativeAI and ChatGroq.
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}


async def extract(raw_text: str, preview_text: str, image_bytes: bytes | None = None) -> dict:
    combined = (
        f"NATIVE POST:\n{raw_text or '(none)'}\n\n"
        f"LINKED POST PREVIEW:\n{preview_text or '(none)'}\n\n"
        f"IMAGE: {'attached below' if image_bytes else '(none)'}"
    )
    llm_usage = None
    call_failed = False   # True only for transient API failures, never parse failures
    result: Optional[SocialExtraction] = None
    attempts = _attempts()
    used_provider, used_model = settings.extractor_provider, settings.extractor_model
    last_error: Exception | None = None

    for i, (provider, model, api_key) in enumerate(attempts):
        content: list[dict] = [{"type": "text", "text": combined}]
        if image_bytes:
            content.append(_image_block(image_bytes, provider))
        try:
            resp: dict = await _get_llm(provider, model, api_key).ainvoke(
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
            )
        except Exception as e:  # noqa: BLE001
            # The call never produced a verdict (no credit, rate limit, 5xx, network).
            # Try the next key, then the next provider, before giving up.
            last_error = e
            log.warning("extraction failed on %s:%s (attempt %d/%d): %s",
                        provider, model, i + 1, len(attempts), e)
            continue

        used_provider, used_model = provider, model
        if i > 0:
            log.warning("extraction fell back to %s:%s after %d failed attempt(s)",
                        provider, model, i)
        raw = resp.get("raw")
        result = resp.get("parsed")
        usage = getattr(raw, "usage_metadata", None) or {}
        if usage:
            llm_usage = {
                "input_tokens":  usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens":  usage.get("total_tokens"),
            }
        if result is None:
            # Tokens were spent even though structured parsing failed — keep the usage.
            # Deliberately NOT a fallback trigger: the model did answer.
            log.warning("extraction parse failed: %s", resp.get("parsing_error"))
            result = SocialExtraction(
                is_actionable=False, action_type="NONE", confidence=0.0,
                reasoning=f"parse_error: {resp.get('parsing_error')}",
            )
        break

    if result is None:
        # Every provider and key failed. Flagged so the caller leaves the message
        # unrecorded and retries later — persisting this placeholder would mark the
        # message permanently seen and silently bury a real signal.
        call_failed = True
        log.error("extraction unavailable: all %d attempt(s) failed, last: %s",
                  len(attempts), last_error)
        result = SocialExtraction(
            is_actionable=False, action_type="NONE", confidence=0.0,
            reasoning=f"extraction_error: {last_error}",
        )

    asset = (result.asset or "").upper() or None
    # Force scaling events to non-actionable per the contract.
    is_actionable = result.is_actionable and result.action_type not in ("ADD", "TRIM")
    return {
        "failed": call_failed,
        "is_actionable": is_actionable,
        "action_type": result.action_type,
        "asset": asset,
        "direction": result.direction,
        "reference_price": result.reference_price,
        "confidence": result.confidence,
        "in_whitelist": (asset in _WHITELIST) if asset else False,
        "model": f"{used_provider}:{used_model}",
        "extractor_version": EXTRACTOR_VERSION,
        "raw_llm_json": result.model_dump(),
        "input_tokens":  llm_usage.get("input_tokens")  if llm_usage else None,
        "output_tokens": llm_usage.get("output_tokens") if llm_usage else None,
        "total_tokens":  llm_usage.get("total_tokens")  if llm_usage else None,
    }
