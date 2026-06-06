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
    """
    Incoming webhook payload from TradingView, Telegram, or Order Generator.

    Breaking changes from legacy format:
    - `symbol` removed: replaced by base_asset + quote_asset
    - `action` removed: use signal field
    - `instrument` removed: redundant
    - `amount` removed: use size field
    - `platform` removed: exchange determined by strategy.account_id

    New fields:
    - base_asset: e.g. "BTC"
    - quote_asset: e.g. "USDT", "USDC", "USD"
    - target_position: optional state-sync signal ("long", "short", "flat")
    """
    # Structured asset identification
    base_asset:      str
    quote_asset:     str

    side:            Literal["buy", "sell"]
    order_type:      Literal["market", "limit"] = "market"
    size:            Decimal
    price:           Optional[Decimal] = None
    leverage:        Optional[int] = None
    margin_mode:     Optional[Literal["cross", "isolated"]] = "cross"
    tp_price:        Optional[Decimal] = None
    sl_price:        Optional[Decimal] = None
    signal:          Literal["open_long", "close_long", "open_short", "close_short"]
    target_position: Optional[Literal["long", "short", "flat"]] = None
    timestamp:       datetime
    token:           str
    signal_source:   Optional[str] = "tradingview"
    signal_metadata: Optional[dict] = {}
    indicator_price: Optional[Decimal] = None



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
