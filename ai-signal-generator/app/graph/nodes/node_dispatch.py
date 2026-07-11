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
    if sc.get('use_economic_calendar'): sources.append('economic_calendar')
    if sc.get('use_mtf_structure'): sources.append('mtf_structure')
    if sc.get('use_orderbook'):    sources.append('orderbook')
    if sc.get('use_volume_profile'): sources.append('volume_profile')
    if sc.get('use_cvd'):          sources.append('cvd')
    if sc.get('use_momentum_divergence'): sources.append('momentum_divergence')
    if sc.get('use_volatility_regime'): sources.append('volatility_regime')
    if sc.get('use_funding_history'): sources.append('funding_history')
    if sc.get('use_liquidations'): sources.append('liquidations')
    if sc.get('use_limit_orders'): sources.append('limit_orders')
    return sources


# (use_* flag, label, state key, sub-key within that state value | None).
# label matches the vocabulary of _data_sources_used() so the two lists pair
# up in the UI: "requested" vs "requested but came back empty".
# use_limit_orders/open_orders is deliberately excluded — node_ingest sets
# open_orders=[] both on fetch failure and on a genuine zero-open-orders
# result, so an empty list there isn't a reliable "missing" signal.
_MISSING_INPUT_CHECKS = [
    ('use_technical',           'technical',          'technical_indicators', None),
    ('use_geometry',            'geometry',            'geometry_data',        None),
    ('use_volume_profile',      'volume_profile',      'volume_profile',       None),
    ('use_momentum_divergence', 'momentum_divergence', 'momentum_divergence',  None),
    ('use_volatility_regime',   'volatility_regime',   'volatility_regime',    None),
    ('use_mtf_structure',       'mtf_structure',        'mtf_structure',       None),
    ('use_fear_greed',          'fear_greed',           'sentiment_data',      'fear_greed'),
    ('use_funding_rate',        'funding_rate',         'sentiment_data',      'funding_rate'),
    ('use_open_interest',       'open_interest',        'sentiment_data',      'open_interest'),
    ('use_funding_history',     'funding_history',      'sentiment_data',      'funding_history'),
    ('use_news',                'news',                 'news_data',           None),
    ('use_economic_calendar',   'economic_calendar',    'calendar_data',       None),
    ('use_btc_dominance',       'btc_dominance',        'market_context',      'btc_dominance'),
    ('use_macro',               'macro',                'market_context',      'macro'),
    ('use_orderbook',           'orderbook',            'orderbook_data',      None),
    ('use_cvd',                 'cvd',                  'cvd_data',            None),
    ('use_liquidations',        'liquidations',         'liquidation_data',    None),
]


def _missing_inputs(sc: dict, state: dict) -> list[str]:
    """Enabled sources whose fetch came back empty on this cycle — the gap
    between what the strategy asked for (use_* flags) and what actually made
    it into the prompt (an LLM-reported "input X missing" is diagnosable from
    here without reading prose)."""
    missing = []
    for flag, label, top_key, sub_key in _MISSING_INPUT_CHECKS:
        if not sc.get(flag):
            continue
        value = state.get(top_key)
        if sub_key is not None:
            value = (value or {}).get(sub_key)
        if not value:
            missing.append(label)
    return missing


async def node_dispatch(state: AgentState) -> AgentState:
    pool   = get_pool()
    sc     = state['strategy_config']
    signal = state.get('llm_signal') or {}
    action     = signal.get('action')
    confidence = signal.get('confidence')
    reasoning  = signal.get('reasoning') or state.get('llm_error')

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
                    input_tokens, output_tokens, total_tokens, missing_inputs
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,
                          $17,$18,$19,$20)
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
                _missing_inputs(sc, state),
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
