import json
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.database import get_pool
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


def _data_sources_used(sc: dict) -> list[str]:
    sources = []
    if sc.get('use_technical'):    sources.append('technical')
    if sc.get('use_geometry'):     sources.append('geometry')
    if sc.get('use_fear_greed'):   sources.append('fear_greed')
    if sc.get('use_funding_rate'): sources.append('funding_rate')
    if sc.get('use_open_interest'): sources.append('open_interest')
    if sc.get('use_news'):         sources.append('news')
    if sc.get('use_btc_dominance'): sources.append('btc_dominance')
    if sc.get('use_macro'):        sources.append('macro')
    return sources


async def node_dispatch(state: AgentState) -> AgentState:
    pool   = get_pool()
    sc     = state['strategy_config']
    signal = state.get('llm_signal') or {}
    action     = signal.get('action')
    confidence = signal.get('confidence')
    reasoning  = signal.get('reasoning')

    triggered_at = state.get('triggered_at') or datetime.now(timezone.utc)
    if isinstance(triggered_at, str):
        triggered_at = datetime.fromisoformat(triggered_at)

    llm_provider = sc.get('llm_provider', 'google')
    llm_model    = sc.get('llm_model',    'gemini-2.0-flash')

    geometry_data = state.get('geometry_data')
    geometry_data_json = json.dumps(geometry_data) if geometry_data is not None else None

    # ── 1. Always write ai_signal_log ────────────────────────────────────
    signal_log_id = None
    try:
        async with pool.acquire() as conn:
            signal_log_id = await conn.fetchval(
                """
                INSERT INTO ai_signal_log (
                    strategy_id, triggered_at, trigger_reason, cycle_interval,
                    prompt_template, data_sources_used, context_tokens,
                    proposed_action, confidence, reasoning,
                    gate_passed, gate_rejection_reason, dry_run,
                    llm_provider, llm_model, geometry_data,
                    input_tokens, output_tokens, total_tokens
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,
                          $17,$18,$19)
                RETURNING id
                """,
                state['strategy_id'],
                triggered_at,
                state.get('trigger_reason', 'manual'),
                state.get('cycle_interval', '4h'),
                sc.get('template_id', 'trend_following'),
                _data_sources_used(sc),
                state.get('context_tokens'),
                action,
                confidence,
                reasoning,
                bool(state.get('gate_passed', False)),
                state.get('gate_rejection_reason'),
                bool(sc.get('dry_run', True)),
                llm_provider,
                llm_model,
                geometry_data_json,
                (state.get('llm_usage') or {}).get('input_tokens'),
                (state.get('llm_usage') or {}).get('output_tokens'),
                (state.get('llm_usage') or {}).get('total_tokens'),
            )
    except Exception as exc:
        logger.error("Failed to write ai_signal_log: %s", exc)

    # ── 2. Gate failed or hold — no webhook ─────────────────────────────
    if not state.get('gate_passed'):
        logger.info(
            "strategy=%s action=%s gate=%s reason=%s — no webhook",
            state['strategy_id'], action,
            state.get('gate_passed'), state.get('gate_rejection_reason'),
        )
        return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}

    # ── 3a. adjust_stops — dispatches in both dry-run and live mode ──────
    # (dry_run is forwarded to the listener which controls whether the exchange
    #  is actually mutated; the AI log always records webhook_fired=TRUE)
    if action == 'adjust_stops':
        try:
            from app.webhook.dispatcher import dispatch_adjust_stops
            result = await dispatch_adjust_stops(state, settings.matp_listener_url)
            webhook_status = result.get('status_code')
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE ai_signal_log
                        SET webhook_fired  = TRUE,
                            webhook_status = $2
                        WHERE id = $1
                        """,
                        signal_log_id,
                        webhook_status,
                    )
            except Exception as exc:
                logger.error("Failed to update ai_signal_log for adjust_stops: %s", exc)
            dry_run_flag = bool(sc.get('dry_run', True))
            logger.info(
                "adjust_stops dispatched strategy=%s status=%s dry_run=%s",
                state['strategy_id'], webhook_status, dry_run_flag,
            )
            return {
                **state,
                'signal_log_id':  signal_log_id,
                'webhook_fired':  True,
                'webhook_status': webhook_status,
            }
        except Exception as exc:
            logger.error("adjust_stops dispatch failed: %s", exc)
            return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}

    # ── 3b. Dry run — suppress opens/closes/place_limit/cancel/amend, not adjust_stops ──
    if sc.get('dry_run', True):
        logger.info("DRY RUN — webhook suppressed strategy=%s action=%s", state['strategy_id'], action)
        return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}

    # ── 3c. cancel_order / amend_order — proxy to listener order-mgmt routes ──
    if action in ('cancel_order', 'amend_order'):
        try:
            from app.webhook.dispatcher import dispatch_cancel_order, dispatch_amend_order

            dispatch_fn = dispatch_cancel_order if action == 'cancel_order' else dispatch_amend_order
            result = await dispatch_fn(state, settings.matp_listener_url)
            webhook_status = result.get('status_code')
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE ai_signal_log
                        SET webhook_fired  = TRUE,
                            webhook_status = $2
                        WHERE id = $1
                        """,
                        signal_log_id,
                        webhook_status,
                    )
            except Exception as exc:
                logger.error("Failed to update ai_signal_log for %s: %s", action, exc)
            logger.info(
                "%s dispatched strategy=%s status=%s",
                action, state['strategy_id'], webhook_status,
            )
            return {
                **state,
                'signal_log_id':  signal_log_id,
                'webhook_fired':  True,
                'webhook_status': webhook_status,
            }
        except Exception as exc:
            logger.error("%s dispatch failed: %s", action, exc)
            return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}

    # ── 4. Fire webhook ──────────────────────────────────────────────────
    try:
        from app.webhook.dispatcher import build_payload, dispatch_webhook

        payload = await build_payload(state)
        result  = await dispatch_webhook(
            payload,
            state['strategy_id'],
            sc.get('webhook_secret', ''),
            settings.matp_listener_url,
        )

        webhook_status = result.get('status_code')
        order_id_str   = result.get('order_id')

        # Update log row
        try:
            order_uuid = uuid.UUID(order_id_str) if order_id_str else None
        except (ValueError, AttributeError):
            order_uuid = None

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE ai_signal_log
                    SET webhook_fired  = TRUE,
                        webhook_status = $2,
                        order_id       = $3
                    WHERE id = $1
                    """,
                    signal_log_id,
                    webhook_status,
                    order_uuid,
                )
        except Exception as exc:
            logger.error("Failed to update ai_signal_log webhook result: %s", exc)

        logger.info(
            "Webhook fired strategy=%s action=%s status=%s order_id=%s",
            state['strategy_id'], action, webhook_status, order_id_str,
        )

        return {
            **state,
            'signal_log_id': signal_log_id,
            'webhook_fired': True,
            'webhook_status': webhook_status,
            'order_id': order_id_str,
        }

    except Exception as exc:
        logger.error("Webhook dispatch failed: %s", exc)
        return {**state, 'signal_log_id': signal_log_id, 'webhook_fired': False}
