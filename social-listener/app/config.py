from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://matp:matp@postgres:5432/matp"

    # Telegram MTProto user session (read-only subscriber)
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session: str = ""           # StringSession from app/generate_session.py (pre-authorized)
    tg_channel: str = "AstronomerZero"
    backfill_limit: int = 50

    # Periodic reconciliation: closes gaps left by messages the live event
    # handler never received (reconnect races, missed updates). Runs the
    # normal "live" path (mark price + staleness gate), not backfill-replay.
    catchup_interval_seconds: int = 60
    catchup_limit: int = 200

    # A just-posted message carries a WebPagePending preview: Telegram fills in
    # the title/description/photo a beat later. Re-fetch rather than lose them.
    webpage_resolve_attempts: int = 3
    webpage_resolve_delay_seconds: float = 2.0

    # One human post routinely arrives as several Telegram messages seconds
    # apart — a short comment, then the X link whose preview repeats it in full.
    # Extracting each separately spends a second LLM call and yields two verdicts
    # for one intent (observed 2026-07-26: msgs 9778/9779, one second apart, both
    # scored — 0.85 on the text, 0.15 on the same content with the chart image).
    # Messages arriving within this window are merged and judged once.
    #
    # The cost is latency: every signal waits this long before being evaluated,
    # because a burst is only known to be complete once the window has passed
    # with no new message. Keep it well under max_signal_age_seconds.
    #
    # 60s, not 15s: on 2026-07-23 the trade card (msg 9756, "Entry 66.2k / Lock in
    # W 64.8k") and the message naming the side ("$btc shorts", msg 9749) were 34
    # seconds apart. At 15s they stayed separate and the card was judged with no
    # idea which way the trade went. The context that disambiguates a post is
    # routinely half a minute away from it.
    merge_window_seconds: float = 60.0
    merge_max_messages: int = 6

    # Vision: the X reposts are annotated TradingView charts, and the position
    # change is often written on the chart rather than in the text.
    vision_enabled: bool = True
    image_max_bytes: int = 4 * 1024 * 1024   # Anthropic caps a single image at 5MB
    image_media_type: str = "image/jpeg"     # Telegram serves preview photos as JPEG

    # Extractor LLM (transcriber role). DO NOT point this at a Flash-Lite class model.
    extractor_provider: str = "anthropic"   # anthropic | google | openai | groq
    extractor_model: str = "claude-sonnet-4-6"
    extractor_temperature: float = 0.0

    # Fallback chain, tried in order after the primary when a call fails to produce
    # a verdict at all (no credit, rate limit, 5xx, network). Entries are
    # "provider:model"; a bare "provider" reuses that provider's own default model.
    # A parse failure does NOT fall through — the model answered, so the next
    # provider would likely answer the same way.
    #
    # This exists because a single provider is a single point of failure: the
    # listener went fully down on 2026-07-25 and again on 2026-07-26 when the
    # Anthropic balance ran out, with usable keys sitting unused in llm_keys.
    extractor_fallbacks: str = "google:gemini-3.6-flash"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # All enabled keys per provider slug, priority order, filled at startup from
    # llm_keys by config_secrets. A provider can hold several keys and the first
    # is not necessarily alive — gemini's priority-0 key was out of credit while
    # its priority-1 key worked. Empty means "fall back to the env var above".
    provider_keys: dict[str, list[str]] = Field(default_factory=dict)

    source_tag: str = "telegram:AstronomerZero"
    asset_whitelist: str = "BTC,ETH"

    # Phase 2a — Redis / mark price
    redis_url: str = "redis://redis:6379"
    ingestion_exchange: str = "blofin"

    # Phase 2a — state machine / gates / staleness
    # Live execution. In "live" the listener POSTs order-listener's webhook — the
    # same contract TradingView and the AI engine use — so sizing, the guaranteed
    # stop loss and every exchange call stay owned by order-listener/executor.
    # Only the "live" phase emits: "backfill" acts unconditionally by design and
    # would fire trades on old posts at every restart.
    execution_mode: str = "shadow"          # shadow | live
    execution_strategy_id: str = ""         # strategies.id holding the capital
    execution_quote_asset: str = "USDT"
    listener_url: str = "http://order-listener:8001"
    emit_timeout_seconds: float = 15.0      # homelab load makes a 5s timeout too tight
    confidence_floor: float = 0.5
    staleness_pct: float = 0.01             # skip priced entry if mark already moved >1% the signal's way
    entry_on_missing_price: str = "market"  # priceless signal -> enter at market

    # A signal is only tradeable while it's fresh. This is the backstop for every
    # live decision — it also catches priced signals recovered by the catchup loop
    # long after the fact (a listener outage, a dropped update).
    max_signal_age_seconds: int = 900

    # When a post cites no price, reconstruct one from the 1m bar covering
    # posted_at so the staleness gate can still run, instead of entering blind.
    implied_ref_lookback_ms: int = 600_000  # how far back to scan the stream
    implied_ref_max_gap_ms: int = 300_000   # reject a bar this much older than the post


settings = Settings()
