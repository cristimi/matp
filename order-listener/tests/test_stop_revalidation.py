"""
Unit tests for app/stop_revalidation.py — fill-price SL/TP re-anchoring.

The three live incidents this protects against (2026-07-13 order analysis):
  1. short limit 1733.94 / SL 1744.00 filled at 1743.91 → SL $0.09 from fill
  2. long  limit 62516.93 / SL 62048.10 filled at 61718.90 → fill BELOW its SL
  3. short market, TP above the fill (wrong side after slippage)
"""
from decimal import Decimal

from app.stop_revalidation import revalidate_stops_for_fill, _MIN_STOP_DIST_FRAC


def test_valid_stops_untouched_long():
    sl, tp, changes = revalidate_stops_for_fill(
        "long", 100.0, 100.2, sl_price=99.0, tp_price=102.0)
    assert changes == {}
    assert sl == Decimal("99.0") and tp == Decimal("102.0")


def test_valid_stops_untouched_short():
    sl, tp, changes = revalidate_stops_for_fill(
        "short", 100.0, 99.8, sl_price=101.0, tp_price=97.0)
    assert changes == {}


def test_incident_1_short_sl_degenerate_after_price_improvement():
    # requested 1733.94, SL 1744.00 (0.58% away) — filled 1743.91
    sl, tp, changes = revalidate_stops_for_fill(
        "short", 1733.94, 1743.91, sl_price=1744.00, tp_price=1687.64)
    assert "sl_price" in changes
    # re-anchored: fill * (1 + original 0.58% distance)
    expected = Decimal("1743.91") * (1 + (Decimal("1744.00") - Decimal("1733.94")) / Decimal("1733.94"))
    assert abs(sl - expected) < Decimal("0.01")
    assert sl > Decimal("1743.91") * (1 + _MIN_STOP_DIST_FRAC)   # viable again
    assert "tp_price" not in changes                             # TP still fine


def test_incident_2_long_filled_below_its_sl():
    # requested 62516.93, SL 62048.10 — filled 61718.90 (below the SL)
    sl, tp, changes = revalidate_stops_for_fill(
        "long", 62516.93, 61718.90, sl_price=62048.10, tp_price=63467.19)
    assert "sl_price" in changes
    assert sl < Decimal("61718.90")          # SL back below the fill
    assert "tp_price" not in changes


def test_incident_3_short_tp_wrong_side_after_slippage():
    # decision ref ~63400, TP 63054.70 — market filled at 62768.70 (below TP)
    sl, tp, changes = revalidate_stops_for_fill(
        "short", 63400.0, 62768.70, sl_price=64333.70, tp_price=63054.70)
    assert "tp_price" in changes
    assert tp < Decimal("62768.70")          # TP back below the fill
    assert "sl_price" not in changes


def test_stop_degenerate_at_request_time_gets_min_floor():
    # SL only 0.01% from ref AND wrong side of fill → re-anchor floors at 0.1%
    sl, _tp, changes = revalidate_stops_for_fill(
        "long", 100.0, 100.0, sl_price=99.99, tp_price=110.0)
    assert "sl_price" in changes
    assert sl <= Decimal("100.0") * (1 - _MIN_STOP_DIST_FRAC)


def test_missing_stops_pass_through():
    sl, tp, changes = revalidate_stops_for_fill("long", 100.0, 100.0)
    assert sl is None and tp is None and changes == {}


def test_no_fill_price_no_op():
    sl, tp, changes = revalidate_stops_for_fill(
        "long", 100.0, 0, sl_price=99.0, tp_price=101.0)
    assert changes == {}


def test_decimal_and_float_inputs_mix():
    sl, tp, changes = revalidate_stops_for_fill(
        "short", Decimal("200"), 199.0, sl_price=Decimal("198.5"), tp_price=Decimal("195"))
    # SL 198.5 is BELOW the short fill 199 → wrong side → re-anchored above
    assert "sl_price" in changes
    assert sl > Decimal("199")
