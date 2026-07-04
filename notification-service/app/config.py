"""
Configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://matp:matp@postgres:5432/matp"
    redis_url:    str = "redis://redis:6379"

    # VAPID (Web Push) keypair. Private key never leaves the container/.env.
    vapid_private_key: str = ""
    vapid_public_key:  str = ""
    vapid_subject:     str = "mailto:admin@example.com"

    # Web Push delivery. Urgency=high tells the push service (FCM) to deliver
    # immediately instead of batching under Android Doze. ttl (seconds) lets a
    # briefly-offline device still receive the message on reconnect.
    webpush_urgency: str = "high"
    webpush_ttl_s:   int = 600

    # Redis Stream + consumer group for the notification event bus.
    stream_key:    str = "notifications:events"
    consumer_group: str = "notification-service"
    consumer_name:  str = "notification-service-1"

    # Exchanges to watch for ingestion-heartbeat staleness (comma-separated).
    # Only exchanges with a running market-ingestion instance publish a heartbeat —
    # currently that's just blofin (see docker-compose.yml market-ingestion service).
    exchanges: str = "blofin"
    heartbeat_stale_ms: int = 60_000

    # Critical services polled for /health, edge-triggered service.down/up.
    listener_url: str = "http://order-listener:8001"
    executor_url: str = "http://order-executor:8004"
    health_poll_interval_s: int = 10

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


def exchange_list() -> list[str]:
    return [e.strip() for e in settings.exchanges.split(",") if e.strip()]
