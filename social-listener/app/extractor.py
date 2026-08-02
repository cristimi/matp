import base64
import logging
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)
EXTRACTOR_VERSION = "v7"  # v7: direction on CLOSE/ADD/STOP is the side being acted on, not
                          # the resulting one — the trader can hold both sides at once, so a
                          # side-less management post is unattributable (see legs.py).
                          # v6: a complete chart outranks the prose; a size and its price
                          #     must come off the SAME annotation. v5 read "TP (Close 30%)"
                          #     at 64,733 off the chart, kept the 30%, and replaced the price
                          #     with "67.7k" from the post's poem — parking a trim the post
                          #     never asked for (msg 9801, 2026-07-30). It also returned the
                          #     card's SL 63,103 as null.
                          # v5: ADD sizing, take-profit levels, amounts read off charts
                          # v4: stop management — stop_price + stop_to_breakeven, STOP
                          # v3: TRIM is actionable — size_fraction + trigger_price
                          # v2: reads the attached chart image alongside the text

SYSTEM_PROMPT = """You extract a crypto trader's STATED position changes from a social post.
You are a transcriber, not an analyst. You never decide whether a trade is good or likely.

A post may include an IMAGE — usually an annotated TradingView chart. Read the annotations
on it: text the trader has drawn on the chart ("Closed longs", "Flipped longs into Shorts",
"Entry", a marked level) is a statement about their position and counts exactly like text in
the post. Read the instrument name from the chart header (e.g. "Bitcoin / TetherUS" -> BTC).
Do NOT interpret the price action itself — candles, trendlines, indicators and drawings with
no words are not statements. Never infer a trade from what the chart "looks like".

THE IMAGE IS OFTEN THE ONLY PLACE THE NUMBERS EXIST. The post text you are given is
frequently truncated — a linked article's description gets cut mid-sentence — while the
chart carries the entry, the stop, the targets and how much came off. Read every number and
percentage written on the chart: price labels on horizontal lines, boxes marked "Entry",
"SL"/"Stop", "TP1"/"TP2"/"Target", and amounts such as "25%", "1/4", "half off", "closed
50% here". Attach them to the matching field below. A level drawn on the chart counts as
stated by the trader even when the text never mentions it.

A COMPLETE CHART WINS OVER THE PROSE. A chart is COMPLETE when it labels the entry AND at
least one stop or target level. Those drawn numbers ARE the trade: they are what the trader
is actually running, placed deliberately on the price axis. Prose in the post never
overrides them. Poems, slogans and celebration lines round numbers off, name a level the
trade has not reached yet, or quote the final target rather than the one coming off now —
none of that displaces a labelled level. Take a number from the text ONLY for a field the
chart leaves unlabelled. When the chart and the text disagree about the same field, use the
chart's value and say in reasoning that you did.

A complete card almost always labels more levels than the action itself needs. Fill
stop_price and take_profit_price from it regardless of action_type — a card showing "SL
63,103" and "TP (Close 30%) 64,733" must return BOTH of those numbers, not null.

NEVER SPLIT AN ANNOTATION. A size and the price it comes off at must be read from the SAME
label. If "TP (Close 30%)" sits on the 64,733 line, then size_fraction=0.30 belongs with
trigger_price=64733 — never with a price taken from somewhere else in the post. Welding a
percentage off the chart onto a number out of the text invents an instruction the trader
never gave. If the size has no price on its own annotation, leave trigger_price null rather
than borrowing one.

LEVELS FOR A TRADE ALREADY RUNNING. The first post about a trade is often plain text, and
the chart showing entry/SL/TP arrives in a LATER post. That later post is still giving the
levels of the SAME live trade. Fill in stop_price and take_profit_price from it even when
the post is otherwise a recap and action_type is NONE — levels are information about an
open position, not a new call, and reporting them is never the same as opening a trade.

Set is_actionable=true ONLY when the post asserts a NEW, concrete change to the trader's OWN
position: opening, flipping, fully closing, or taking PART of a position off.

Set is_actionable=false for everything else, including:
- P&L brags / recaps / "up X RR" celebrations of a trade the post does not change
- macro commentary, predictions, "looking for an entry", "a long next?"
- hype, community chatter, emoji-only posts, anything without a concrete new entry/exit
- RETROSPECTIVE chart annotations: a marker on an earlier candle explaining a trade the
  trader already took and already talked about is a recap, not a new call. A chart is a new
  call only when the post presents the change as being made now. When a chart annotation
  sits far from the chart's latest price, treat it as retrospective.

action_type:
  OPEN  - newly entering a position
  FLIP  - closing one side and entering the opposite in the same post
  CLOSE - fully closing a position, leaving nothing on
  TRIM  - taking PART of an existing position off, leaving the rest running
  STOP  - the post's ONLY position change is moving the stop
  ADD   - scaling INTO a position the trader already holds, same side
  NONE  - not a position change

ADD vs OPEN. OPEN starts a position; ADD grows one the trader is already in. "Adding
here", "adding to the short", "scaling in", "second entry", "doubling down", "loading more"
-> ADD. If the post reads as a first entry, it is OPEN, not ADD.

TRIM vs CLOSE. "closed", "out", "flat", "all out", "done with it" -> CLOSE. Anything that
leaves the trade alive -> TRIM: "took half off", "trimmed", "partials", "banked some",
"lock in W", "lock in a win", "TP1", "risk off by taking some profit", "secure profit".

TRIM vs recap. A trim is actionable when the post states it as what happens to the trade
NOW or at a named level. "TP1 hit, +3R, told you" describing something already done and
already announced is a recap -> is_actionable=false, action_type=NONE.

STOPS. A post can move its stop as well as trim, so the stop fields ride alongside
action_type rather than competing with it — a card that both banks profit and de-risks is
action_type=TRIM WITH the stop fields filled in. Use action_type=STOP only when moving the
stop is the post's whole content.

  stop_to_breakeven=true  for "risk off the trade", "risk off", "moved to BE", "stop to
                          entry", "free trade now", "can't lose on this one", "de-risked" —
                          the trader wants the stop at their entry and names no price.
  stop_price=<number>     when a level is named: "SL 66.2k" -> 66200, "stop above 66k" ->
                          66000, "invalidation 65.8k" -> 65800.

Do NOT read a profit level as a stop. "TP", "target", "lock in W", "celebrations",
"secure profit" are profit; "SL", "stop", "invalidation", "risk off", "cut", "break even"
are stops. A post that only celebrates a stop that already triggered ("stopped out, oh
well") is a recap -> NONE.

MANAGEMENT CARDS. A card that repeats an entry the trader is ALREADY in and adds profit or
stop handling is trade MANAGEMENT, not a new entry. Do not answer OPEN merely because the
card prints "Entry:". When the card's new content is where profit comes off, answer TRIM
and put that level in trigger_price. Signs of management rather than a fresh call: the
entry is described in the past ("Entry: 65.5k" alongside "risk off the trade"), the post
talks about protecting or banking a move that already happened, or it hands out levels for
a trade the channel has already been shown.

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
direction       : LONG or SHORT. For OPEN/FLIP it is the NEW resulting direction. For
                  CLOSE, TRIM, ADD and STOP it is the side of the EXISTING position the
                  post is acting on — closing a short is direction=SHORT, a partial profit
                  on a short is direction=SHORT. This matters: the trader can hold a long
                  and a short in the same coin at once, and a post that does not say which
                  one it means cannot be acted on. Give the side whenever the post or the
                  chart makes it recoverable; null only when it genuinely does not.
reference_price : the entry/exit price the trader cites, as a number ("66.7k" -> 66700). Use a
                  price written in an annotation if the text gives none. null if absent.
size_fraction   : TRIM only — how much of the position comes off, 0..1. Read it from the
                  text OR from the chart. Exact wording wins over any guess: "25%" -> 0.25,
                  "a quarter" -> 0.25, "1/3" -> 0.33, "half" -> 0.5, "75%" -> 0.75.
                  Only when no amount is stated anywhere, fall back to the vaguer words:
                  "some"/"a bit"/"partials" -> 0.25, "most of it" -> 0.75. If even that is
                  absent, null. null for every other action_type.
add_multiple    : ADD only — how big the addition is as a multiple of the trader's usual
                  entry. "same size again"/"doubling" -> 1.0, "half a position" -> 0.5,
                  "small add"/"a bit more" -> 0.25. null when the post gives no amount.
                  null for every other action_type.
trigger_price   : TRIM only — the price at which the part comes off, when the post names one
                  ("Lock in W 64.4k" -> 64400). null when the trim is presented as happening
                  now, at market. null for every other action_type.
stop_price      : the stop level the post names, as a number. null if none.
stop_to_breakeven : true when the post asks for the stop at entry without naming a price.
                  false or null otherwise. Never set both this and stop_price unless the
                  post really does both.
take_profit_price : the take-profit level the post or chart gives, as a number. When
                  several are shown ("TP1 64.4k, TP2 62k"), give the NEAREST one to the
                  current price — that is the one that matters next. null if none.
confidence      : 0..1, how clearly the post states a concrete position change.
evidence        : where the position change came from — "text", "image", "both", or "none".

Be conservative. When unsure, is_actionable=false."""


class SocialExtraction(BaseModel):
    is_actionable: bool
    action_type: Literal["OPEN", "FLIP", "CLOSE", "ADD", "TRIM", "STOP", "NONE"]
    asset: Optional[str] = None
    direction: Optional[Literal["LONG", "SHORT"]] = None
    reference_price: Optional[float] = None
    size_fraction: Optional[float] = Field(default=None, ge=0, le=1)
    add_multiple: Optional[float] = Field(default=None, ge=0, le=3)
    trigger_price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_to_breakeven: Optional[bool] = None
    take_profit_price: Optional[float] = None
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
    is_actionable = result.is_actionable
    # A fraction and a trigger only mean anything on a trim, a multiple only on an
    # add; drop them elsewhere so a stray value can never reach a sizing path.
    is_trim = result.action_type == "TRIM"
    is_add = result.action_type == "ADD"
    # Level fields ride alongside any action_type, but only matter while a position
    # stays open — a full exit takes its stops with it. They are kept even on NONE,
    # because the chart that finally shows a running trade's SL/TP usually arrives in
    # a post that changes nothing.
    keeps_position = result.action_type not in ("CLOSE", "FLIP")
    return {
        "failed": call_failed,
        "is_actionable": is_actionable,
        "action_type": result.action_type,
        "asset": asset,
        "direction": result.direction,
        "reference_price": result.reference_price,
        "size_fraction": result.size_fraction if is_trim else None,
        "add_multiple": result.add_multiple if is_add else None,
        "trigger_price": result.trigger_price if is_trim else None,
        "stop_price": result.stop_price if keeps_position else None,
        "stop_to_breakeven": bool(result.stop_to_breakeven) if keeps_position else None,
        "take_profit_price": result.take_profit_price if keeps_position else None,
        "confidence": result.confidence,
        "in_whitelist": (asset in _WHITELIST) if asset else False,
        "model": f"{used_provider}:{used_model}",
        "extractor_version": EXTRACTOR_VERSION,
        "raw_llm_json": result.model_dump(),
        "input_tokens":  llm_usage.get("input_tokens")  if llm_usage else None,
        "output_tokens": llm_usage.get("output_tokens") if llm_usage else None,
        "total_tokens":  llm_usage.get("total_tokens")  if llm_usage else None,
    }
