from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ingestion_exchange: str = "blofin"
    ingestion_mode: str = "live"
    ingestion_subscriptions: str = "BTC-USDT:1h,BTC-USDT:1m"
    ingestion_warmup_candles: int = 500
    redis_url: str = "redis://redis:6379"

    @property
    def subscriptions(self) -> list[tuple[str, str]]:
        result = []
        for sub in self.ingestion_subscriptions.split(","):
            sub = sub.strip()
            if ":" in sub:
                symbol, tf = sub.rsplit(":", 1)
                result.append((symbol.strip(), tf.strip()))
        return result


settings = Settings()
