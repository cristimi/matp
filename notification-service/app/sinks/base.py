"""
Abstract notification delivery sink. Add a new class here (e.g. TelegramSink) plus
its config/token to enable a new delivery channel — publishers never change.
"""

from abc import ABC, abstractmethod


class SinkResult:
    def __init__(self, sent: int = 0, failed: int = 0, error: str | None = None):
        self.sent = sent
        self.failed = failed
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None


class Sink(ABC):
    name: str = "sink"

    @abstractmethod
    async def send(self, notification: dict) -> SinkResult:
        """notification: {title, body, tag, renotify, data}."""
        raise NotImplementedError
