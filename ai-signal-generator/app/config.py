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
    cerebras_api_key:       str = ""
    zhipu_api_key:          str = ""
    zhipu_base_url:         str = "https://open.bigmodel.cn/api/paas/v4/"
    openrouter_api_key:     str = ""
    openrouter_base_url:    str = "https://openrouter.ai/api/v1"
    cryptopanic_api_key:    str = ""
    finnhub_api_key:        str = ""   # economic calendar; unset = field dormant
    signal_venues:          str = "binance,bybit,okx"  # market-flow aggregation venues
    matp_listener_url:      str = "http://order-listener:8001"
    matp_executor_url:      str = "http://order-executor:8004"

    # funding-regime monitor (app/funding_monitor.py)
    funding_monitor_enabled:    bool  = True
    funding_monitor_symbols:    str   = "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,LTC,DOT,NEAR"
    funding_monitor_enter_ann:  float = 0.40   # hot when trailing 3d funding > 40%/yr
    funding_monitor_exit_ann:   float = 0.20   # cooled when it drops back below 20%/yr
    funding_monitor_interval_s: int   = 3600

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
