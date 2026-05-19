"""
Pydantic models for Order Listener — webhook payload and internal types.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class WebhookPayload(BaseModel):
    symbol:     str
    side:       Literal["buy", "sell"]
    orderType:  Literal["market", "limit"] = "market"
    size:       Decimal
    price:      Optional[Decimal] = None
    leverage:   Optional[int] = None
    marginMode: Optional[Literal["cross", "isolated"]] = "cross"
    tpPrice:    Optional[Decimal] = None
    slPrice:    Optional[Decimal] = None
    platform:   str = "auto"
    strategy_id: Optional[str] = None
    signal:     Literal["open_long", "close_long", "open_short", "close_short"]
    timestamp:  datetime
    token:      str

    @field_validator("size")
    @classmethod
    def size_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("size must be positive")
        return v

    @field_validator("leverage")
    @classmethod
    def leverage_range(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (1 <= v <= 200):
            raise ValueError("leverage must be between 1 and 200")
        return v

    @field_validator("price")
    @classmethod
    def price_required_for_limit(cls, v: Optional[Decimal], info) -> Optional[Decimal]:
        if info.data.get("orderType") == "limit" and v is None:
            raise ValueError("price is required for limit orders")
        return v



class OrderResult(BaseModel):
    success:           bool
    exchange_order_id: Optional[str] = None
    status:            str   # "filled" | "pending" | "rejected"
    error_msg:         Optional[str] = None
    raw_response:      Optional[dict] = None


class OrderResponse(BaseModel):
    order_id: UUID
    status:   str
    message:  str
