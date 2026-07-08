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

    # Extractor LLM (transcriber role). DO NOT point this at a Flash-Lite class model.
    extractor_provider: str = "anthropic"   # anthropic | google | openai
    extractor_model: str = "claude-sonnet-4-6"
    extractor_temperature: float = 0.0

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    source_tag: str = "telegram:AstronomerZero"
    asset_whitelist: str = "BTC,ETH"

    # Phase 2a — Redis / mark price
    redis_url: str = "redis://redis:6379"
    ingestion_exchange: str = "blofin"

    # Phase 2a — state machine / gates / staleness
    execution_mode: str = "shadow"          # shadow | live  (live is a LATER prompt)
    confidence_floor: float = 0.5
    staleness_pct: float = 0.01             # skip priced entry if mark already moved >1% the signal's way
    entry_on_missing_price: str = "market"  # priceless signal -> enter at market


settings = Settings()
