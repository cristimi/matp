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
    margin_mode:     Optional[Literal["cross", "isolated"]] = None
    tp_price:        Optional[Decimal] = None
    sl_price:        Optional[Decimal] = None
    # Distance form of the bracket, in percent of the entry (2.5 = 2.5%). A caller
    # that thinks in distances rather than levels sends these instead of
    # tp_price/sl_price, and the handler prices them against the account's own
    # exchange — the only price the order will really fill at. The AI engine sends
    # these for market entries: its candles come from the venue's public market,
    # which on a demo account is ~1% away from the market that fills the order, and
    # pricing the bracket there collapsed a 1.5 R:R into 0.25 (see
    # docs/process/reports/btc-ai-position-chart-investigation.md).
    # When both forms arrive, the distance wins — it is the intent.
    tp_pct:          Optional[Decimal] = None
    sl_pct:          Optional[Decimal] = None
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
    actual_fill_size:  Optional[Decimal] = None
    pnl:               Optional[Decimal] = None
    realized_pnl:      Optional[Decimal] = None
    fee:               Optional[Decimal] = None


class OrderResponse(BaseModel):
    order_id: UUID
    status:   str
    message:  str
