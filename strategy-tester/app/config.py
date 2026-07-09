"""
Configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url:                 str   = "postgresql://matp:matp@postgres:5432/matp"
    redis_url:                    str   = "redis://redis:6379"
    gemini_api_key:               str   = ""
    openai_api_key:               str   = ""
    anthropic_api_key:            str   = ""
    groq_api_key:                 str   = ""
    tester_default_balance:       float = 1000.0
    tester_default_slippage_pct:  float = 0.05
    tester_default_fee_pct:       float = 0.02
    tester_max_concurrent_runs:   int   = 1
    tester_llm_failure_threshold: float = 0.05
    tester_ohlcv_fetch_batch:     int   = 1000
    tester_equity_insert_batch:   int   = 500

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
