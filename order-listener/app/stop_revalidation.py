"""
Fill-price SL/TP revalidation.

SL/TP prices are computed upstream (AI guard or TV alert) from a *reference*
price — the decision-time mark or the limit order's requested price. The
actual fill can land elsewhere: market slippage, or a limit filling with
price improvement. Observed live (2026-07-13 analysis of ai_engine orders):

  - ETH short: limit requested 1733.94 / SL 1744.00, filled 1743.91 —
    the stop ended up $0.09 (0.005%) from the fill.
  - BTC long: limit requested 62516.93 / SL 62048.10, filled 61718.90 —
    the position was born BELOW its own stop-loss.
  - BTC short (market): TP landed above the fill — wrong side entirely.

revalidate_stops_for_fill() repairs exactly these cases: any stop that sits
on the wrong side of the fill, or within _MIN_STOP_DIST_FRAC of it, is
re-anchored to the fill price preserving the stop's ORIGINAL fractional
distance from the reference price (the geometry the strategy intended).
Stops that are already valid relative to the fill are returned untouched —
a structural level chosen by the strategy is respected whenever it is still
viable.

`distance_based=True` changes that last rule, and only that rule. When the
caller asked for the bracket in *distances* (the AI engine's tp_pct/sl_pct)
rather than levels, there is no structural level to respect: the ask was
"1.5% away from where I get in", so every leg is re-anchored to the fill
whenever slippage moved its distance by more than _DISTANCE_TOLERANCE. Left
alone, ordinary slippage quietly rewrites the reward/risk it was sized for —
the 2026-08-11 BTC entry slipped 0.21% above its reference and turned a
1.5 reward/risk into 0.25 while every leg still looked "valid".
"""

import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# A stop closer to the fill than this fraction is considered degenerate:
# fees + one tick of noise trigger it instantly.
_MIN_STOP_DIST_FRAC = Decimal("0.001")   # 0.1%

# For a distance-based bracket: how far a leg's distance may drift from what was
# asked before it is re-anchored to the fill. 2% of the distance itself, so a 1.5%
# target may sit between 1.47% and 1.53% of the fill and is left alone — small
# enough that reward/risk is preserved, loose enough not to re-place stops over
# rounding.
_DISTANCE_TOLERANCE = Decimal("0.02")


def _frac_dist(ref: Decimal, price: Decimal) -> Decimal:
    """Fractional distance of a stop from its reference price, floored at the
    minimum viable distance (covers stops that were degenerate at request time)."""
    if ref <= 0:
        return _MIN_STOP_DIST_FRAC
    return max(abs(ref - price) / ref, _MIN_STOP_DIST_FRAC)


def _distance_drifted(ref: Decimal, fill: Decimal, stop: Decimal) -> bool:
    """True when `stop` no longer sits the asked distance away from the fill.

    The ask is its fractional distance from `ref`; what it got is its fractional
    distance from `fill`. Compared relative to the ask, so a 2% tolerance means 2%
    of the distance, not 2% of the price.
    """
    asked = _frac_dist(ref, stop)
    got   = abs(fill - stop) / fill
    return abs(got - asked) > asked * _DISTANCE_TOLERANCE


def revalidate_stops_for_fill(
    side: str,                      # 'long' | 'short' (position side)
    ref_price,                      # price the stops were computed from (limit/request price)
    fill_price,                     # actual fill price
    sl_price=None,
    tp_price=None,
    distance_based: bool = False,   # the bracket was asked for as distances, not levels
) -> tuple[Optional[Decimal], Optional[Decimal], dict]:
    """
    Returns (sl, tp, changes). `changes` is {} when both stops are already
    valid for the fill; otherwise it maps 'sl_price'/'tp_price' to
    {'from': old, 'to': new} for every re-anchored stop.

    Validity for a long:  sl <= fill*(1-min)  and  tp >= fill*(1+min).
    Shorts mirrored. An invalid stop is re-anchored to the fill using its
    original fractional distance from ref_price.

    With `distance_based=True` a leg also counts as invalid when it is merely in
    the wrong PLACE — its distance from the fill drifted past _DISTANCE_TOLERANCE —
    because a distance is all the caller ever asked for. See the module docstring.
    """
    ref  = Decimal(str(ref_price))  if ref_price  is not None else Decimal("0")
    fill = Decimal(str(fill_price)) if fill_price is not None else Decimal("0")
    sl   = Decimal(str(sl_price))   if sl_price   is not None else None
    tp   = Decimal(str(tp_price))   if tp_price   is not None else None

    changes: dict = {}
    if fill <= 0:
        return sl, tp, changes   # no fill price to validate against

    long_side = side == "long"

    if sl is not None:
        sl_ok = sl <= fill * (1 - _MIN_STOP_DIST_FRAC) if long_side \
           else sl >= fill * (1 + _MIN_STOP_DIST_FRAC)
        if sl_ok and distance_based and _distance_drifted(ref, fill, sl):
            sl_ok = False
        if not sl_ok:
            dist   = _frac_dist(ref, sl)
            new_sl = fill * (1 - dist) if long_side else fill * (1 + dist)
            changes["sl_price"] = {"from": str(sl), "to": str(new_sl)}
            sl = new_sl

    if tp is not None:
        tp_ok = tp >= fill * (1 + _MIN_STOP_DIST_FRAC) if long_side \
           else tp <= fill * (1 - _MIN_STOP_DIST_FRAC)
        if tp_ok and distance_based and _distance_drifted(ref, fill, tp):
            tp_ok = False
        if not tp_ok:
            dist   = _frac_dist(ref, tp)
            new_tp = fill * (1 + dist) if long_side else fill * (1 - dist)
            changes["tp_price"] = {"from": str(tp), "to": str(new_tp)}
            tp = new_tp

    if changes:
        logger.warning(
            "stop revalidation: %s fill=%s ref=%s re-anchored %s",
            side, fill, ref,
            ", ".join(f"{k} {v['from']} -> {v['to']}" for k, v in changes.items()),
        )
    return sl, tp, changes
