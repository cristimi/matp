"""
Pydantic models for Order Executor.
"""

from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel


class OrderRequest(BaseModel):
    order_id:    str
    account_id:  str
    symbol:      str
    side:        Literal["buy", "sell"]
    signal:      str
    order_type:  str
    size:        Decimal
    price:       Optional[Decimal] = None
    leverage:    Optional[int] = None
    margin_mode: Optional[str] = None
    tp_price:    Optional[Decimal] = None
    sl_price:    Optional[Decimal] = None
    config:      Optional[dict] = None


class OrderResult(BaseModel):
    success:           bool
    exchange_order_id: Optional[str] = None
    status:            Literal["filled", "pending", "rejected", "route_failed"]
    error_msg:         Optional[str] = None
    raw_response:      Optional[dict] = None


class AccountRecord(BaseModel):
    id:        str
    exchange:  str
    mode:      str
    label:     str
    is_active: bool


class Position(BaseModel):
    symbol:      str
    side:        Literal["long", "short"]
    size:        Decimal
    entry_price: Decimal
    leverage:    int
    unrealized_pnl: Optional[Decimal] = None
