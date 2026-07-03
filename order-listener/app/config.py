"""
Configuration loaded from environment variables.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url:            str = "postgresql://matp:changeme@postgres:5432/matp"
    redis_url:               str = "redis://redis:6379"
    master_key:              str = ""
    blofin_api_key:          str = ""
    blofin_api_secret:       str = ""
    blofin_api_passphrase:   str = ""
    hyperliquid_private_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

# ── Guaranteed safety-SL formula ────────────────────────────────────────────────────
#
# compute_guaranteed_sl() (webhook_handler.py) prefers a LIVE, tier-aware maintenance-
# margin rate fetched from order-executor (get_maintenance_margin() in
# executor_client.py, backed by GET /accounts/{id}/maintenance-margin/{symbol}). The
# constants below are only the FALLBACK path for when that live fetch is unavailable,
# plus a degenerate-input guard.
#
# Historically MMR was a flat 0.01 (1%) constant applied unconditionally. That was
# proven unsafe: docs/process/reports/safety-sl-vs-liq-investigation.md found a live
# BTC 40x short on Hyperliquid whose "safety" SL sat PAST the real exchange liquidation
# price, because real MMR rises toward ~1/(2*max_leverage) as leverage approaches a
# symbol's own exchange max leverage (implied real MMR 1.30% vs the flat 1% assumed).
# The "MMR=0.01 is conservative" comment that used to be here is only true when
# leverage is well below a symbol's max — it silently inverts near max leverage, which
# is exactly when a safety net matters most.

# Added to every derived/fallback MMR to cover the fee/funding gap an exchange folds
# into its live liquidation price but a static formula/table can't model. Keep this in
# sync with order-executor/app/adapters/base.py's MMR_CONSERVATISM_BUFFER (separate
# deployable services, no shared package). Sized from the observed Hyperliquid BTC 40x
# gap (theoretical 1.25% vs. implied-real 1.2961%, ~0.046%), rounded up with headroom.
MMR_CONSERVATISM_BUFFER = 0.0015


def fallback_mmr(effective_leverage: float) -> float:
    """
    Conservative MMR estimate used only when the live executor lookup fails.

    Assumes the worst realistic case: effective_leverage sits at (or beyond) the
    symbol's real exchange max leverage, since real MMR is ~= 1/(2*max_leverage) at
    that tier (Hyperliquid's formula; confirmed against a live position in the
    investigation report). This is always at least as conservative as any live-sourced
    MMR for that leverage — a symbol's real max leverage can only be >= the leverage
    actually granted (adapters reject requests above it), and MMR only rises as tier
    max leverage falls.
    """
    if effective_leverage <= 0:
        return 1.0  # degenerate input: force maximum caution
    return (1.0 / (2.0 * effective_leverage)) + MMR_CONSERVATISM_BUFFER


# Fallback distance used only when the natural formula (1/L - MMR) computes to <= 0 —
# i.e. the MMR (live or fallback) consumed the entire 1/L headroom. This shouldn't
# happen given exchange max-leverage bounds (see fallback_mmr docstring), but is
# guarded rather than trusted. NOT used to widen any positive natural distance: a flat
# floor that widens is mathematically incompatible with "never place the SL past the
# analytically-safe distance" at high leverage — that widening (`max(natural, 0.005)`)
# is what let the old code's SL land past real liquidation (investigation report,
# Hypothesis 1, latent above ~67x leverage).
DEGENERATE_SL_DIST = 0.0005
