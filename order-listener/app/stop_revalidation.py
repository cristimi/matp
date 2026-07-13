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
"""

import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# A stop closer to the fill than this fraction is considered degenerate:
# fees + one tick of noise trigger it instantly.
_MIN_STOP_DIST_FRAC = Decimal("0.001")   # 0.1%


def _frac_dist(ref: Decimal, price: Decimal) -> Decimal:
    """Fractional distance of a stop from its reference price, floored at the
    minimum viable distance (covers stops that were degenerate at request time)."""
    if ref <= 0:
        return _MIN_STOP_DIST_FRAC
    return max(abs(ref - price) / ref, _MIN_STOP_DIST_FRAC)


def revalidate_stops_for_fill(
    side: str,                      # 'long' | 'short' (position side)
    ref_price,                      # price the stops were computed from (limit/request price)
    fill_price,                     # actual fill price
    sl_price=None,
    tp_price=None,
) -> tuple[Optional[Decimal], Optional[Decimal], dict]:
    """
    Returns (sl, tp, changes). `changes` is {} when both stops are already
    valid for the fill; otherwise it maps 'sl_price'/'tp_price' to
    {'from': old, 'to': new} for every re-anchored stop.

    Validity for a long:  sl <= fill*(1-min)  and  tp >= fill*(1+min).
    Shorts mirrored. An invalid stop is re-anchored to the fill using its
    original fractional distance from ref_price.
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
        if not sl_ok:
            dist   = _frac_dist(ref, sl)
            new_sl = fill * (1 - dist) if long_side else fill * (1 + dist)
            changes["sl_price"] = {"from": str(sl), "to": str(new_sl)}
            sl = new_sl

    if tp is not None:
        tp_ok = tp >= fill * (1 + _MIN_STOP_DIST_FRAC) if long_side \
           else tp <= fill * (1 - _MIN_STOP_DIST_FRAC)
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
