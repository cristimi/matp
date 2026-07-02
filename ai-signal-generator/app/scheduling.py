"""
Candle-close-aligned scheduling helpers.

Boundaries are UTC-epoch aligned per standard exchange convention: epoch 0
(1970-01-01T00:00:00 UTC) falls on a boundary for every timeframe, so e.g. 4h
candles close at 00:00/04:00/08:00/... UTC, 1h at every hour, 15m at
:00/:15/:30/:45, etc. Aligning to `now.timestamp() // tf_seconds` reproduces
exactly this convention with no special-casing per unit.
"""
from datetime import datetime

_UNIT_SECONDS = {'m': 60, 'h': 3600, 'd': 86400}


def parse_interval_seconds(interval_str: str) -> int:
    """Converts '4h', '15m', '1d', '10m' etc. to seconds.

    Deliberately generic rather than restricted to ccxt's candle-timeframe
    whitelist: the scheduler's polling-cadence strings (e.g. '10m' for
    interval_position_open) can be values ccxt doesn't support as an actual
    candle timeframe — see node_ingest.py's cycle_interval/timeframe conflation.
    """
    unit  = interval_str[-1]
    value = int(interval_str[:-1])
    mult = _UNIT_SECONDS.get(unit)
    if mult is None:
        raise ValueError(f"Unsupported interval unit in {interval_str!r}")
    return value * mult


def seconds_until_aligned_wake(
    timeframe: str,
    now: datetime,
    buffer_seconds: int,
    min_sleep_seconds: float = 5.0,
) -> float:
    """
    Seconds to sleep from `now` until `buffer_seconds` past the next candle-close
    boundary for `timeframe`.

    The most recently closed candle's buffer point is `last_boundary +
    buffer_seconds`. If that point is still ahead of `now`, wake there — this is
    what makes a scheduler that starts (or resumes) partway through a candle wake
    shortly after the *current* candle's close instead of jumping a full period
    ahead. If that point has already passed (steady-state operation: we just
    consumed it, or resumed after it), wake at the next candle's buffer point
    instead.

    Always returns at least `min_sleep_seconds`, so a `now` that lands exactly on
    (or a hair past) a buffer point wakes almost immediately rather than sleeping
    for a full extra period or a literal 0s.
    """
    tf_sec = parse_interval_seconds(timeframe)
    now_epoch = now.timestamp()
    last_boundary = (now_epoch // tf_sec) * tf_sec
    wake_at = last_boundary + buffer_seconds
    if wake_at < now_epoch:
        wake_at += tf_sec
    return max(min_sleep_seconds, wake_at - now_epoch)
