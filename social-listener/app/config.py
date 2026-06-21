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

    # Extractor LLM (transcriber role). DO NOT point this at a Flash-Lite class model.
    extractor_provider: str = "anthropic"   # anthropic | google | openai
    extractor_model: str = "claude-sonnet-4-6"
    extractor_temperature: float = 0.0

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    source_tag: str = "telegram:AstronomerZero"
    asset_whitelist: str = "BTC,ETH"


settings = Settings()
