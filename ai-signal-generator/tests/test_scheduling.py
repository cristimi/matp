"""
Unit tests for scheduling.py — candle-close-aligned wake calculation.

Boundaries are UTC-epoch aligned (epoch 0 = 1970-01-01T00:00:00 UTC lands on a
boundary for every timeframe), so for a given timeframe of N seconds, boundaries
are every multiple of N seconds since epoch. Each test picks a real UTC timestamp,
works out the expected boundary by hand, and asserts the function's returned sleep
duration lands the wake time at exactly boundary+buffer (or, if that point has
already passed, the *next* boundary+buffer).
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from app.scheduling import parse_interval_seconds, seconds_until_aligned_wake


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _wake_epoch(timeframe, now, buffer_seconds, min_sleep_seconds=5.0):
    sleep = seconds_until_aligned_wake(timeframe, now, buffer_seconds, min_sleep_seconds)
    assert sleep >= 0
    return now.timestamp() + sleep


# ── parse_interval_seconds ──────────────────────────────────────────────────

def test_parse_interval_seconds_units():
    assert parse_interval_seconds('1m')  == 60
    assert parse_interval_seconds('15m') == 900
    assert parse_interval_seconds('10m') == 600      # not a ccxt timeframe, still parses
    assert parse_interval_seconds('1h')  == 3600
    assert parse_interval_seconds('4h')  == 14400
    assert parse_interval_seconds('1d')  == 86400


def test_parse_interval_seconds_invalid_unit():
    with pytest.raises(ValueError):
        parse_interval_seconds('5x')


# ── seconds_until_aligned_wake: just after a boundary ───────────────────────

def test_1h_just_after_boundary():
    # 2026-07-02T10:00:05Z is 5s past the 10:00:00 hour boundary.
    # buffer point for that boundary (10:02:30) is still ahead → wake there.
    now = _dt('2026-07-02T10:00:05')
    wake = _wake_epoch('1h', now, buffer_seconds=150)
    assert wake == _dt('2026-07-02T10:02:30').timestamp()


def test_4h_just_after_boundary():
    # 4h boundaries fall at 00/04/08/12/16/20 UTC. 12:00:10 is 10s past 12:00:00.
    now = _dt('2026-07-02T12:00:10')
    wake = _wake_epoch('4h', now, buffer_seconds=150)
    assert wake == _dt('2026-07-02T12:02:30').timestamp()


# ── seconds_until_aligned_wake: just before a boundary ──────────────────────

def test_1h_just_before_boundary():
    # 10:59:55 — the 10:00:00 boundary's buffer point (10:02:30) already passed,
    # so wake at the *next* boundary's buffer point: 11:02:30.
    now = _dt('2026-07-02T10:59:55')
    wake = _wake_epoch('1h', now, buffer_seconds=150)
    assert wake == _dt('2026-07-02T11:02:30').timestamp()


def test_15m_just_before_boundary():
    # 15m boundaries at :00/:15/:30/:45. 10:14:50 → next boundary 10:15:00, buffer 30s.
    now = _dt('2026-07-02T10:14:50')
    wake = _wake_epoch('15m', now, buffer_seconds=30)
    assert wake == _dt('2026-07-02T10:15:30').timestamp()


# ── seconds_until_aligned_wake: exactly on a boundary ───────────────────────

def test_1h_exactly_on_boundary():
    now = _dt('2026-07-02T10:00:00')
    wake = _wake_epoch('1h', now, buffer_seconds=150)
    assert wake == _dt('2026-07-02T10:02:30').timestamp()


def test_5m_exactly_on_boundary():
    now = _dt('2026-07-02T10:05:00')
    wake = _wake_epoch('5m', now, buffer_seconds=60)
    assert wake == _dt('2026-07-02T10:06:00').timestamp()


# ── edge case: buffer point is now (or a hair past) — wake ~immediately,
#    never jump a near-full extra period ───────────────────────────────────

def test_wake_exactly_on_buffer_point_floors_to_min_sleep():
    # now IS the buffer point itself (10:02:30 == 10:00:00 + 150s buffer).
    # wake_at == now_epoch, not < now_epoch, so it does NOT get pushed a full
    # period ahead — it floors to min_sleep_seconds instead.
    now = _dt('2026-07-02T10:02:30')
    sleep = seconds_until_aligned_wake('1h', now, buffer_seconds=150, min_sleep_seconds=5.0)
    assert sleep == 5.0


def test_zero_buffer_exactly_on_boundary_floors_to_min_sleep():
    now = _dt('2026-07-02T10:00:00')
    sleep = seconds_until_aligned_wake('1m', now, buffer_seconds=0, min_sleep_seconds=5.0)
    assert sleep == 5.0


def test_naive_next_boundary_would_be_wrong_regression():
    # Regression guard for the exact edge case called out in the spec: scheduler
    # starts 2s after a close. A naive "always jump to the *next* boundary"
    # implementation would wait ~59min (almost a full period) instead of ~148s.
    now = _dt('2026-07-02T10:00:02')
    sleep = seconds_until_aligned_wake('1h', now, buffer_seconds=150)
    assert sleep == pytest.approx(148.0)
    assert sleep < 3600 / 2  # nowhere near a full period


# ── sanity: sleep duration never negative, and monotonically shrinks as
#    `now` approaches the target from before ─────────────────────────────

def test_sleep_never_negative_across_a_full_period():
    tf = 60  # 1m
    base = _dt('2026-07-02T10:00:00').timestamp()
    for offset in range(0, tf, 5):
        now = datetime.fromtimestamp(base + offset, tz=timezone.utc)
        sleep = seconds_until_aligned_wake('1m', now, buffer_seconds=20)
        assert sleep >= 0
