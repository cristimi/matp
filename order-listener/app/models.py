"""
Pydantic models for Order Listener — webhook payload and internal types.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Strategy(BaseModel):
    id:          str
    name:        str
    class_:      str = Field(..., alias="class")
    symbol:      str
    interval:    str
    platform:    str
    enabled:     bool
    type:        Literal["internal", "tradingview"]
    config_yaml: str


class WebhookPayload(BaseModel):
    # Original MATP fields
    base_asset:  Optional[str] = None
    quote_asset: Optional[str] = None
    side:       Optional[str] = None # Added back for side_to_lower validator
    orderType:  Literal["market", "limit"] = "market"
    size:       Optional[Decimal] = None
    price:      Optional[Decimal] = None
    leverage:   Optional[int] = None
    marginMode: Optional[Literal["cross", "isolated"]] = "cross"
    tpPrice:    Optional[Decimal] = None
    slPrice:    Optional[Decimal] = None
    platform:   str = "auto"
    strategy_id: Optional[str] = None
    signal:     Optional[str] = None
    timestamp:  datetime
    token:      Optional[str] = None
    # New fields
    signal_source: Optional[str] = "tradingview"
    signal_metadata: Optional[dict] = {}
    indicator_price: Optional[Decimal] = None
    # TradingView-specific payload fields
    action:             Optional[str] = None
    marketPosition:     Optional[str] = None
    prevMarketPosition: Optional[str] = None
    instrument:         Optional[str] = None
    signalToken:        Optional[str] = None
    maxLag:             Optional[int] = 60
    investmentType:     Optional[str] = None
    amount:             Optional[Decimal] = None
    id:                 Optional[str] = None

    @property
    def pair_label(self) -> str:
        return f"{self.base_asset}-{self.quote_asset}"


    @field_validator("side", mode="before")
    @classmethod
    def side_to_lower(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.lower()
            # Map 'short' to 'sell' and 'long' to 'buy'
            mapping = {"short": "sell", "long": "buy"}
            v = mapping.get(v, v)
            if v not in ["buy", "sell"]:
                raise ValueError("side must be 'buy' or 'sell'")
        return v

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
    actual_fill_price: Optional[Decimal] = None
    pnl:               Optional[Decimal] = None


class OrderResponse(BaseModel):
    order_id: UUID
    status:   str
    message:  str
