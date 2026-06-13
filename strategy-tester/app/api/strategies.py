import logging
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalise_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "-").replace("_", "-")


def _generate_id() -> str:
    return "tst_" + secrets.token_hex(6)


class StrategyCreate(BaseModel):
    name: str
    symbol: str
    interval: str = "1h"
    description: str = ""
    default_leverage: int = 1
    margin_mode: str = "isolated"
    max_position_size: float = 1.0
    max_leverage: int = 10
    max_daily_signals: int = 500
    max_daily_drawdown_percent: float = 20.0
    capital_allocation_percent: float = 100.0
    allow_quote_variants: bool = False
    allow_cross_charting: bool = False


class StrategyUpdate(BaseModel):
    name: str | None = None
    symbol: str | None = None
    interval: str | None = None
    enabled: bool | None = None
    description: str | None = None
    default_leverage: int | None = None
    margin_mode: str | None = None
    max_position_size: float | None = None
    max_leverage: int | None = None
    max_daily_signals: int | None = None
    max_daily_drawdown_percent: float | None = None
    capital_allocation_percent: float | None = None
    allow_quote_variants: bool | None = None
    allow_cross_charting: bool | None = None


@router.get("")
async def list_strategies():
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                s.*,
                aic.llm_provider,
                aic.llm_model,
                lr.id                 AS latest_run_id,
                lr.status             AS latest_run_status,
                lr.timeframe          AS latest_run_timeframe,
                lr.date_from          AS latest_run_date_from,
                lr.date_to            AS latest_run_date_to,
                lr.total_trades       AS latest_run_total_trades,
                lr.win_rate           AS latest_run_win_rate,
                lr.total_pnl_pct      AS latest_run_total_pnl_pct,
                lr.total_pnl          AS latest_run_total_pnl,
                lr.completed_at       AS latest_run_completed_at
            FROM tester.strategies s
            LEFT JOIN tester.ai_strategy_config aic ON aic.strategy_id = s.id
            LEFT JOIN LATERAL (
                SELECT id, status, timeframe, date_from, date_to, total_trades,
                       win_rate, total_pnl, total_pnl_pct, completed_at
                FROM tester.backtest_runs
                WHERE strategy_id = s.id
                  AND status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
            ) lr ON true
            WHERE s.is_deleted = false
            ORDER BY s.created_at DESC
            """
        )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_strategy(body: StrategyCreate):
    strategy_id = _generate_id()
    normalised_symbol = _normalise_symbol(body.symbol)
    webhook_secret = secrets.token_hex(16)

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO tester.strategies (
                    id, name, symbol, interval, description,
                    class, config_yaml,
                    webhook_secret, webhook_enabled,
                    default_leverage, margin_mode,
                    max_position_size, max_leverage, max_daily_signals,
                    max_daily_drawdown_percent, capital_allocation_percent,
                    allow_quote_variants, allow_cross_charting,
                    enabled
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    'webhook', '',
                    $6, false,
                    $7, $8,
                    $9, $10, $11,
                    $12, $13,
                    $14, $15,
                    true
                )
                """,
                strategy_id, body.name, normalised_symbol,
                body.interval, body.description,
                webhook_secret,
                body.default_leverage, body.margin_mode,
                body.max_position_size, body.max_leverage, body.max_daily_signals,
                body.max_daily_drawdown_percent, body.capital_allocation_percent,
                body.allow_quote_variants, body.allow_cross_charting,
            )
            await conn.execute(
                "INSERT INTO tester.ai_strategy_config (strategy_id) VALUES ($1)",
                strategy_id,
            )
            await conn.execute(
                "INSERT INTO tester.ai_risk_config (strategy_id) VALUES ($1)",
                strategy_id,
            )

    logger.info("Created tester strategy %s (%s %s)", strategy_id, body.name, normalised_symbol)
    return {
        "id": strategy_id,
        "name": body.name,
        "symbol": normalised_symbol,
        "interval": body.interval,
        "enabled": True,
        "webhook_secret": webhook_secret,
        "message": "Strategy created. Save the webhook_secret — it will not be shown again.",
    }


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                s.*,
                aic.template_id,            aic.llm_provider,       aic.llm_model,
                aic.use_technical,           aic.use_fear_greed,     aic.use_funding_rate,
                aic.use_open_interest,       aic.use_news,           aic.use_btc_dominance,
                aic.use_macro,               aic.indicators,
                aic.confidence_threshold,    aic.cooldown_entry_minutes,
                aic.cooldown_increase_minutes, aic.cooldown_stop_adj_minutes,
                aic.interval_no_position,    aic.interval_position_open,
                aic.interval_at_risk,        aic.at_risk_threshold_pct,
                aic.dry_run,                 aic.custom_instructions,
                arc.max_position_size_pct,
                arc.max_concurrent_trades
            FROM tester.strategies s
            LEFT JOIN tester.ai_strategy_config aic ON aic.strategy_id = s.id
            LEFT JOIN tester.ai_risk_config arc     ON arc.strategy_id = s.id
            WHERE s.id = $1 AND s.is_deleted = false
            """,
            strategy_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    return dict(row)


@router.put("/{strategy_id}")
async def update_strategy(strategy_id: str, body: StrategyUpdate):
    normalised_symbol = _normalise_symbol(body.symbol) if body.symbol else None

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tester.strategies SET
                name                       = COALESCE($1,  name),
                symbol                     = COALESCE($2,  symbol),
                interval                   = COALESCE($3,  interval),
                enabled                    = COALESCE($4,  enabled),
                description                = COALESCE($5,  description),
                default_leverage           = COALESCE($6,  default_leverage),
                margin_mode                = COALESCE($7,  margin_mode),
                max_position_size          = COALESCE($8,  max_position_size),
                max_leverage               = COALESCE($9,  max_leverage),
                max_daily_signals          = COALESCE($10, max_daily_signals),
                max_daily_drawdown_percent = COALESCE($11, max_daily_drawdown_percent),
                capital_allocation_percent = COALESCE($12, capital_allocation_percent),
                allow_quote_variants       = COALESCE($13, allow_quote_variants),
                allow_cross_charting       = COALESCE($14, allow_cross_charting),
                updated_at                 = NOW()
            WHERE id = $15 AND is_deleted = false
            RETURNING id, name, symbol, interval, enabled, updated_at
            """,
            body.name, normalised_symbol, body.interval, body.enabled, body.description,
            body.default_leverage, body.margin_mode,
            body.max_position_size, body.max_leverage, body.max_daily_signals,
            body.max_daily_drawdown_percent, body.capital_allocation_percent,
            body.allow_quote_variants, body.allow_cross_charting,
            strategy_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    return dict(row)


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE tester.strategies
            SET is_deleted = true, updated_at = NOW()
            WHERE id = $1 AND is_deleted = false
            """,
            strategy_id,
        )
    if status == "UPDATE 0":
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")
    return {"deleted": strategy_id}


@router.get("/{strategy_id}/runs")
async def list_runs(strategy_id: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM tester.strategies WHERE id = $1 AND is_deleted = false",
            strategy_id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

        rows = await conn.fetch(
            """
            SELECT
                id, strategy_id, timeframe, date_from, date_to,
                initial_balance, slippage_pct, fee_pct,
                status, candles_processed, total_candles,
                total_trades, winning_trades, losing_trades,
                win_rate, total_pnl, total_pnl_pct, max_drawdown_pct,
                profit_factor, sharpe_approx,
                llm_failures, llm_failure_rate,
                llm_provider, llm_model,
                started_at, completed_at, created_at
            FROM tester.backtest_runs
            WHERE strategy_id = $1
            ORDER BY created_at DESC
            """,
            strategy_id,
        )
    return [dict(r) for r in rows]
