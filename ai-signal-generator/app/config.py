"""
Configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url:           str = "postgresql://matp:matp@postgres:5432/matp"
    redis_url:              str = "redis://redis:6379"
    gemini_api_key:         str = ""
    openai_api_key:         str = ""
    anthropic_api_key:      str = ""
    groq_api_key:           str = ""
    cryptopanic_api_key:    str = ""
    finnhub_api_key:        str = ""   # economic calendar; unset = field dormant
    signal_venues:          str = "binance,bybit,okx"  # market-flow aggregation venues
    matp_listener_url:      str = "http://order-listener:8001"
    matp_executor_url:      str = "http://order-executor:8004"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
