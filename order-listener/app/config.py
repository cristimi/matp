"""
Configuration loaded from environment variables.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url:            str = "postgresql://matp:changeme@postgres:5432/matp"
    redis_url:               str = "redis://redis:6379"
    master_key:              str = ""
    blofin_api_key:          str = ""
    blofin_api_secret:       str = ""
    blofin_api_passphrase:   str = ""
    hyperliquid_private_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

# Maintenance-margin rate for the safety-SL formula.
# Conservative default — real exchange MMRs are usually lower, so the stop lands before liquidation.
MMR = 0.01
# Floor: prevents zero/negative distance at extreme leverage
MIN_SAFETY_SL_DIST = 0.005
