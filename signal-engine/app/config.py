from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://matp:matp@postgres:5432/matp"
    redis_url:    str = "redis://redis:6379"
    # How many candles to read from stream for warm-up on startup
    warmup_candles: int = 500
    # Exchange used by the Redis streams (must match market-ingestion)
    ingestion_exchange: str = "blofin"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
