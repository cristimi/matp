"""
Configuration loaded from environment variables.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url:            str = "postgresql://matp:changeme@postgres:5432/matp"
    redis_url:               str = "redis://redis:6379"
    webhook_secret:          str = ""
    master_key:              str = ""
    blofin_api_key:          str = ""
    blofin_api_secret:       str = ""
    blofin_api_passphrase:   str = ""
    hyperliquid_private_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
