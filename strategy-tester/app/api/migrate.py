"""
Strategy migration endpoints.

  POST /migrate/from-matp        — Import a public.strategies row into tester schema
  POST /migrate/to-matp/{id}     — Promote a tester strategy to public.strategies

R2 invariant: the ONLY sanctioned write to public.* is in to_matp().
R2a constraints (enforced unconditionally):
  - enabled=False  — never auto-started
  - webhook_enabled=False
  - single transaction — rolls back entirely on any error
  - account_id from request — never invented or defaulted
  - no start/enable endpoint called
  - promotion logged with source tester_id / new public_id / account_id
"""
import logging
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class FromMaTPRequest(BaseModel):
    source_matp_id: str


class ToMaTPRequest(BaseModel):
    account_id: str   # R2a: required from caller, never defaulted

    @field_validator('account_id')
    @classmethod
    def account_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('account_id must be non-empty (R2a)')
        return v.strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_tester_id() -> str:
    return 'tst_' + secrets.token_hex(6)   # 12 hex chars = 48 bits entropy


def _new_public_id() -> str:
    return 'promo-' + secrets.token_hex(4)  # 8 hex chars


def _new_webhook_secret() -> str:
    return secrets.token_hex(16)            # 32 hex chars


# ── POST /migrate/from-matp ───────────────────────────────────────────────────

@router.post("/migrate/from-matp")
async def from_matp(body: FromMaTPRequest):
    """
    Copy a public.strategies row (and its AI configs) into the tester schema.
    If the public strategy has no AI config, schema defaults are inserted and
    ai_config_imported=false is returned so the caller knows to review them.
    All writes are in tester.* (R2 compliant — no write to public.*).
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        # 1. Verify source exists
        pub = await conn.fetchrow(
            """
            SELECT id, name, class, symbol, interval, platform,
                   config_yaml, config, webhook_secret, webhook_enabled,
                   description, platform_override, max_daily_signals,
                   max_position_size, max_leverage,
                   capital_allocation_percent, tags, type, margin_mode,
                   default_leverage, blofin_token
            FROM public.strategies
            WHERE id = $1 AND is_deleted = false
            """,
            body.source_matp_id,
        )
        if pub is None:
            raise HTTPException(
                status_code=404,
                detail=f"Public strategy not found: {body.source_matp_id}",
            )

        # 2. Load AI configs from public (may be absent)
        pub_aic = await conn.fetchrow(
            "SELECT * FROM public.ai_strategy_config WHERE strategy_id = $1",
            body.source_matp_id,
        )
        pub_arc = await conn.fetchrow(
            "SELECT * FROM public.ai_risk_config WHERE strategy_id = $1",
            body.source_matp_id,
        )

        new_id = _new_tester_id()

        async with conn.transaction():
            # 3. Insert tester.strategies
            await conn.execute(
                """
                INSERT INTO tester.strategies (
                    id, name, class, symbol, interval, platform,
                    enabled, config_yaml, config,
                    webhook_secret, webhook_enabled,
                    description, platform_override, max_daily_signals,
                    max_position_size, max_leverage,
                    capital_allocation_percent, tags, type, margin_mode,
                    default_leverage, blofin_token,
                    source_matp_id
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,
                    true,$7,$8,
                    $9,false,
                    $10,$11,$12,
                    $13,$14,
                    $15,$16,$17,$18,
                    $19,$20,
                    $21
                )
                """,
                new_id,
                pub['name'],
                pub['class'] or 'webhook',
                pub['symbol'],
                pub['interval'],
                pub['platform'] or 'auto',
                pub['config_yaml'] or '',
                pub['config'] or {},
                _new_webhook_secret(),
                pub['description'],
                pub['platform_override'],
                pub['max_daily_signals'] or 500,
                pub['max_position_size'] or 1.0,
                pub['max_leverage'] or 10,
                pub['capital_allocation_percent'] or 100,
                list(pub['tags']) if pub['tags'] else [],
                pub['type'] or 'internal',
                pub['margin_mode'] or 'isolated',
                pub['default_leverage'] or 1,
                pub['blofin_token'],
                body.source_matp_id,
            )

            # 4a. Mark ai_config_defaulted on the strategy row
            ai_config_imported = pub_aic is not None
            await conn.execute(
                "UPDATE tester.strategies SET ai_config_defaulted = $1 WHERE id = $2",
                not ai_config_imported,
                new_id,
            )

            # 4b. Insert tester.ai_strategy_config
            if pub_aic is not None:
                await conn.execute(
                    """
                    INSERT INTO tester.ai_strategy_config (
                        strategy_id, template_id, llm_provider, llm_model,
                        use_technical, use_fear_greed, use_funding_rate,
                        use_open_interest, use_news, use_btc_dominance, use_macro,
                        indicators, lookback_days,
                        confidence_threshold, cooldown_entry_minutes,
                        cooldown_increase_minutes, cooldown_stop_adj_minutes,
                        interval_no_position, interval_position_open, interval_at_risk,
                        at_risk_threshold_pct, dry_run, emergency_exit_pct,
                        custom_instructions
                    ) VALUES (
                        $1,$2,$3,$4,
                        $5,$6,$7,
                        $8,$9,$10,$11,
                        $12,$13,
                        $14,$15,
                        $16,$17,
                        $18,$19,$20,
                        $21,$22,$23,
                        $24
                    )
                    """,
                    new_id,
                    pub_aic['template_id'] or 'trend_following',
                    pub_aic['llm_provider'] or 'google',
                    pub_aic['llm_model'] or 'gemini-2.5-flash',
                    bool(pub_aic['use_technical']),
                    bool(pub_aic['use_fear_greed']),
                    bool(pub_aic['use_funding_rate']),
                    bool(pub_aic['use_open_interest']),
                    bool(pub_aic['use_news']),
                    bool(pub_aic['use_btc_dominance']),
                    bool(pub_aic['use_macro']),
                    list(pub_aic['indicators']) if pub_aic['indicators'] else ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP'],
                    int(pub_aic['lookback_days'] or 90),
                    float(pub_aic['confidence_threshold'] or 0.72),
                    int(pub_aic['cooldown_entry_minutes'] or 240),
                    int(pub_aic['cooldown_increase_minutes'] or 60),
                    int(pub_aic['cooldown_stop_adj_minutes'] or 30),
                    pub_aic['interval_no_position'] or '4h',
                    pub_aic['interval_position_open'] or '1h',
                    pub_aic['interval_at_risk'] or '15m',
                    float(pub_aic['at_risk_threshold_pct'] or 3.0),
                    bool(pub_aic['dry_run']),
                    float(pub_aic['emergency_exit_pct'] or 5.0),
                    pub_aic['custom_instructions'],
                )
            else:
                # No public AI config — insert schema defaults
                await conn.execute(
                    "INSERT INTO tester.ai_strategy_config (strategy_id) VALUES ($1)",
                    new_id,
                )

            # 5. Insert tester.ai_risk_config
            if pub_arc is not None:
                await conn.execute(
                    """
                    INSERT INTO tester.ai_risk_config (
                        strategy_id,
                        max_position_size_pct, max_concurrent_trades
                    ) VALUES ($1,$2,$3)
                    """,
                    new_id,
                    float(pub_arc['max_position_size_pct'] or 5.0),
                    int(pub_arc['max_concurrent_trades'] or 1),
                )
            else:
                await conn.execute(
                    "INSERT INTO tester.ai_risk_config (strategy_id) VALUES ($1)",
                    new_id,
                )

    logger.info(
        "from-matp: imported public strategy '%s' → tester id='%s' "
        "(ai_config_imported=%s)",
        body.source_matp_id, new_id, ai_config_imported,
    )

    ai_note = (
        None if ai_config_imported else
        "Source strategy had no ai_strategy_config row; defaults applied. "
        "Review and adjust before running backtests."
    )
    return {
        "new_tester_id":     new_id,
        "source_matp_id":    body.source_matp_id,
        "mode":              "copy",
        "ai_config_imported": ai_config_imported,
        "ai_config_note":    ai_note,
    }


# ── POST /migrate/to-matp/{id} ────────────────────────────────────────────────

@router.post("/migrate/to-matp/{strategy_id}")
async def to_matp(strategy_id: str, body: ToMaTPRequest):
    """
    Promote a tester strategy to public.strategies.

    R2a INVARIANTS (enforced below — do not relax):
      • enabled = False        — strategy is NEVER auto-started
      • webhook_enabled = False — no incoming webhook accepted
      • account_id from request — never invented or defaulted
      • single transaction     — rolls back if any insert fails
      • no start/enable call   — this endpoint only writes rows; caller must
                                 explicitly enable via the dashboard
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        # 1. Verify tester strategy exists
        tst = await conn.fetchrow(
            """
            SELECT id, name, class, symbol, interval, platform,
                   config_yaml, config, description, platform_override,
                   max_daily_signals, max_position_size, max_leverage,
                   capital_allocation_percent,
                   tags, type, margin_mode, default_leverage, blofin_token
            FROM tester.strategies
            WHERE id = $1 AND is_deleted = false
            """,
            strategy_id,
        )
        if tst is None:
            raise HTTPException(
                status_code=404,
                detail=f"Tester strategy not found: {strategy_id}",
            )

        # Load AI configs
        tst_aic = await conn.fetchrow(
            "SELECT * FROM tester.ai_strategy_config WHERE strategy_id = $1",
            strategy_id,
        )
        tst_arc = await conn.fetchrow(
            "SELECT * FROM tester.ai_risk_config WHERE strategy_id = $1",
            strategy_id,
        )

        new_public_id     = _new_public_id()
        new_webhook_secret = _new_webhook_secret()
        account_id        = body.account_id   # R2a: from request, never defaulted

        async with conn.transaction():
            # 2. Insert public.strategies — R2a: enabled=False, webhook_enabled=False
            await conn.execute(
                """
                INSERT INTO public.strategies (
                    id, name, class, symbol, interval, platform,
                    enabled, config_yaml, config,
                    webhook_secret, webhook_enabled,
                    description,
                    max_daily_signals, max_position_size, max_leverage,
                    capital_allocation_percent,
                    tags, type, margin_mode, default_leverage, blofin_token,
                    account_id, strategy_source
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,
                    FALSE,$7,$8,
                    $9,FALSE,
                    $10,
                    $11,$12,$13,
                    $14,
                    $15,$16,$17,$18,$19,
                    $20,'ai'
                )
                """,
                new_public_id,
                tst['name'],
                tst['class'] or 'webhook',
                tst['symbol'],
                tst['interval'],
                tst['platform'] or 'auto',
                tst['config_yaml'] or '',
                tst['config'] or {},
                new_webhook_secret,
                tst['description'],
                tst['max_daily_signals'] or 500,
                tst['max_position_size'] or 1.0,
                tst['max_leverage'] or 10,
                tst['capital_allocation_percent'] or 100,
                list(tst['tags']) if tst['tags'] else [],
                tst['type'] or 'internal',
                tst['margin_mode'] or 'isolated',
                tst['default_leverage'] or 1,
                tst['blofin_token'],
                account_id,     # R2a: from request
            )

            # 3. Insert public.ai_strategy_config
            if tst_aic is not None:
                await conn.execute(
                    """
                    INSERT INTO public.ai_strategy_config (
                        strategy_id, template_id, llm_provider, llm_model,
                        use_technical, use_fear_greed, use_funding_rate,
                        use_open_interest, use_news, use_btc_dominance, use_macro,
                        indicators, lookback_days,
                        confidence_threshold, cooldown_entry_minutes,
                        cooldown_increase_minutes, cooldown_stop_adj_minutes,
                        interval_no_position, interval_position_open, interval_at_risk,
                        at_risk_threshold_pct, dry_run, emergency_exit_pct,
                        custom_instructions
                    ) VALUES (
                        $1,$2,$3,$4,
                        $5,$6,$7,
                        $8,$9,$10,$11,
                        $12,$13,
                        $14,$15,
                        $16,$17,
                        $18,$19,$20,
                        $21,$22,$23,
                        $24
                    )
                    """,
                    new_public_id,
                    tst_aic['template_id'] or 'trend_following',
                    tst_aic['llm_provider'] or 'google',
                    tst_aic['llm_model'] or 'gemini-2.5-flash',
                    bool(tst_aic['use_technical']),
                    bool(tst_aic['use_fear_greed']),
                    bool(tst_aic['use_funding_rate']),
                    bool(tst_aic['use_open_interest']),
                    bool(tst_aic['use_news']),
                    bool(tst_aic['use_btc_dominance']),
                    bool(tst_aic['use_macro']),
                    list(tst_aic['indicators']) if tst_aic['indicators'] else ['RSI', 'MACD', 'EMA50', 'EMA200', 'BB', 'VWAP'],
                    int(tst_aic['lookback_days'] or 90),
                    float(tst_aic['confidence_threshold'] or 0.72),
                    int(tst_aic['cooldown_entry_minutes'] or 240),
                    int(tst_aic['cooldown_increase_minutes'] or 60),
                    int(tst_aic['cooldown_stop_adj_minutes'] or 30),
                    tst_aic['interval_no_position'] or '4h',
                    tst_aic['interval_position_open'] or '1h',
                    tst_aic['interval_at_risk'] or '15m',
                    float(tst_aic['at_risk_threshold_pct'] or 3.0),
                    bool(tst_aic['dry_run']),
                    float(tst_aic['emergency_exit_pct'] or 5.0),
                    tst_aic['custom_instructions'],
                )
            else:
                await conn.execute(
                    "INSERT INTO public.ai_strategy_config (strategy_id) VALUES ($1)",
                    new_public_id,
                )

            # 4. Insert public.ai_risk_config
            if tst_arc is not None:
                await conn.execute(
                    """
                    INSERT INTO public.ai_risk_config (
                        strategy_id,
                        max_position_size_pct, max_concurrent_trades
                    ) VALUES ($1,$2,$3)
                    """,
                    new_public_id,
                    float(tst_arc['max_position_size_pct'] or 5.0),
                    int(tst_arc['max_concurrent_trades'] or 1),
                )
            else:
                await conn.execute(
                    "INSERT INTO public.ai_risk_config (strategy_id) VALUES ($1)",
                    new_public_id,
                )

    # R2a: LOUD logging — source tester id / new public id / account
    logger.warning(
        "TO-MATP PROMOTION: tester_strategy_id='%s' → public_strategy_id='%s' "
        "account_id='%s' | enabled=False webhook_enabled=False | "
        "To activate: manually set enabled=true in dashboard after review.",
        strategy_id, new_public_id, account_id,
    )

    return {
        "tester_strategy_id": strategy_id,
        "public_strategy_id": new_public_id,
        "account_id":         account_id,
        "enabled":            False,   # R2a confirmation — always False
        "webhook_enabled":    False,
    }
