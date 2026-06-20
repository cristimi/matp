import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "8h": 28800,
    "1d": 86400,
}


def _candle_from_raw(raw) -> dict:
    return {
        "t": int(raw[0]),
        "o": float(raw[1]),
        "h": float(raw[2]),
        "l": float(raw[3]),
        "c": float(raw[4]),
        "v": float(raw[5]),
    }


class Ingestor:
    def __init__(
        self,
        exchange_id: str,
        canonical_symbol: str,
        ccxt_symbol: str,
        timeframe: str,
        warmup_candles: int,
        store,
    ):
        self.exchange_id = exchange_id
        self.canonical = canonical_symbol
        self.ccxt_symbol = ccxt_symbol
        self.timeframe = timeframe
        self.warmup_candles = warmup_candles
        self.store = store
        self._tf_ms = _TIMEFRAME_SECONDS.get(timeframe, 3600) * 1000

    async def warmup(self, rest_exchange) -> None:
        logger.info(
            "Warmup starting: %s %s (%d candles)", self.canonical, self.timeframe, self.warmup_candles
        )
        try:
            raw = await rest_exchange.fetch_ohlcv(
                self.ccxt_symbol,
                timeframe=self.timeframe,
                limit=self.warmup_candles + 1,
            )
        except Exception as e:
            logger.error("Warmup fetch failed %s %s: %s", self.canonical, self.timeframe, e)
            return

        if not raw:
            logger.warning("Warmup: no data for %s %s", self.canonical, self.timeframe)
            return

        # All candles except the last are definitively closed
        closed = raw[:-1]

        # Idempotent: skip bars already in the stream (no duplicate `t` across restarts)
        last_ts = await self.store.get_last_closed_ts(self.canonical, self.timeframe)
        written = 0
        skipped = 0
        for c in closed:
            if last_ts is not None and int(c[0]) <= last_ts:
                skipped += 1
                continue
            await self.store.add_closed_candle(
                self.canonical, self.timeframe, _candle_from_raw(c)
            )
            written += 1

        # Warn (don't silently hole) if an outage exceeded the warmup window
        if last_ts is not None and written > 0:
            oldest_new = next(int(c[0]) for c in closed if int(c[0]) > last_ts)
            if oldest_new > last_ts + self._tf_ms:
                logger.warning(
                    "Warmup discontinuity %s %s: stream tail t=%d, oldest new t=%d "
                    "(outage exceeded warmup window; older gap not backfilled)",
                    self.canonical, self.timeframe, last_ts, oldest_new,
                )

        if raw:
            await self.store.set_forming_candle(
                self.canonical, self.timeframe, _candle_from_raw(raw[-1])
            )

        logger.info(
            "Warmup done: %s %s — %d new bars written, %d already present",
            self.canonical, self.timeframe, written, skipped,
        )

    async def run(self, pro_exchange) -> None:
        """Main watch loop. Handles bar-close detection, gap-stitch, and stall simulation."""
        prev_open_time: Optional[int] = None
        prev_candle: Optional[dict] = None
        first_event = True

        while True:
            try:
                # Check for simulate-gap stall flag
                stall_until = await self.store.get_stall_until(self.exchange_id)
                now_ms = int(time.time() * 1000)
                if stall_until and now_ms < stall_until:
                    sleep_sec = max(1.0, (stall_until - now_ms) / 1000)
                    logger.info(
                        "Simulated stall: %s %s sleeping %.0fs",
                        self.canonical, self.timeframe, sleep_sec,
                    )
                    await asyncio.sleep(sleep_sec)
                    continue

                candles = await pro_exchange.watch_ohlcv(self.ccxt_symbol, self.timeframe)
                if not candles:
                    continue

                candle = _candle_from_raw(candles[-1])
                current_open_time = candle["t"]

                await self.store.set_forming_candle(self.canonical, self.timeframe, candle)
                await self.store.update_heartbeat()

                if first_event:
                    # On first WS event, check for a startup gap vs the stream
                    first_event = False
                    last_in_stream = await self.store.get_last_closed_ts(
                        self.canonical, self.timeframe
                    )
                    if last_in_stream is not None and current_open_time > last_in_stream + self._tf_ms:
                        await self._gap_stitch(pro_exchange, last_in_stream, current_open_time)
                    prev_open_time = current_open_time
                    prev_candle = candle
                    continue

                if current_open_time > prev_open_time:
                    # A new bar has opened — the previous one is now closed
                    if prev_candle is not None:
                        await self.store.add_closed_candle(
                            self.canonical, self.timeframe, prev_candle
                        )
                        logger.info(
                            "Closed bar: %s %s t=%d o=%.4f h=%.4f l=%.4f c=%.4f v=%.4f",
                            self.canonical, self.timeframe,
                            prev_candle["t"], prev_candle["o"], prev_candle["h"],
                            prev_candle["l"], prev_candle["c"], prev_candle["v"],
                        )

                    # Check for gap (more than one interval elapsed since prev bar)
                    expected_next = prev_open_time + self._tf_ms
                    if current_open_time > expected_next:
                        await self._gap_stitch(pro_exchange, prev_open_time, current_open_time)

                    prev_open_time = current_open_time
                    prev_candle = candle
                else:
                    # Same bar updating (forming candle)
                    prev_candle = candle

            except Exception as e:
                logger.error(
                    "Watch loop error %s %s: %s — retrying in 10s",
                    self.canonical, self.timeframe, e,
                )
                await asyncio.sleep(10)

    async def _gap_stitch(self, exchange, from_closed_ts: int, to_forming_ts: int) -> None:
        """Fetch and insert bars between from_closed_ts (exclusive) and to_forming_ts (exclusive)."""
        n_expected = max(0, (to_forming_ts - from_closed_ts) // self._tf_ms - 1)
        logger.info(
            "Gap detected %s %s: %d->%d, fetching ~%d bars",
            self.canonical, self.timeframe, from_closed_ts, to_forming_ts, n_expected,
        )
        try:
            since = from_closed_ts + self._tf_ms
            limit = min(max(n_expected + 10, 10), 1000)
            raw = await exchange.fetch_ohlcv(
                self.ccxt_symbol,
                timeframe=self.timeframe,
                since=since,
                limit=limit,
            )
            if not raw:
                logger.warning("Gap stitch: no data returned for %s %s", self.canonical, self.timeframe)
                return

            count = 0
            for c in raw:
                if c[0] < to_forming_ts:  # exclude the forming candle
                    await self.store.add_closed_candle(
                        self.canonical, self.timeframe, _candle_from_raw(c)
                    )
                    count += 1

            logger.info(
                "Gap stitch complete: %s %s %d->%d, inserted %d bars",
                self.canonical, self.timeframe, from_closed_ts, to_forming_ts, count,
            )
        except Exception as e:
            logger.error("Gap stitch failed %s %s: %s", self.canonical, self.timeframe, e)
