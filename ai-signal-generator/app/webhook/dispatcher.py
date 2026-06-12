import logging
from datetime import datetime

import httpx

from app.graph.state import AgentState
from app.webhook.signer import sign_payload

logger = logging.getLogger(__name__)

ACTION_TO_SIGNAL = {
    'open_long':   ('buy',  'open_long'),
    'open_short':  ('sell', 'open_short'),
    'close_long':  ('sell', 'close_long'),
    'close_short': ('buy',  'close_short'),
}


async def build_payload(state: AgentState) -> dict:
    sc     = state['strategy_config']
    signal = state['llm_signal']
    action = signal['action']

    if action == 'partial_close':
        position_side = state.get('position_side') or 'long'
        if position_side == 'long':
            side, sig = 'sell', 'close_long'
        else:
            side, sig = 'buy', 'close_short'
    else:
        side, sig = ACTION_TO_SIGNAL[action]

    return {
        'base_asset':  sc['base_asset'],
        'quote_asset': sc['quote_asset'],
        'side':        side,
        'order_type':  'market',
        'size':        str(state['resolved_size']),
        'sl_price':    str(state['resolved_sl_price']) if state.get('resolved_sl_price') else None,
        'tp_price':    str(state['resolved_tp_price']) if state.get('resolved_tp_price') else None,
        'signal':      sig,
        'timestamp':   datetime.utcnow().isoformat() + 'Z',
        'token':       sc.get('webhook_secret', ''),
        'signal_source': 'ai_engine',
        'signal_metadata': {
            'confidence':     signal['confidence'],
            'reasoning':      signal['reasoning'],
            'trigger_reason': state.get('trigger_reason', 'scheduled'),
            'template_id':    sc.get('template_id', 'trend_following'),
            'dry_run':        sc.get('dry_run', True),
        },
    }


async def dispatch_adjust_stops(state: AgentState, listener_url: str) -> dict:
    """
    POST to listener /strategies/{strategy_id}/adjust-stops with new TP/SL prices.
    Uses the strategy's webhook_secret for auth.
    """
    sc           = state['strategy_config']
    strategy_id  = state['strategy_id']
    secret       = sc.get('webhook_secret', '')
    tp_price     = state.get('resolved_tp_price')
    sl_price     = state.get('resolved_sl_price')

    body: dict = {
        'token':   secret,
        'dry_run': bool(sc.get('dry_run', True)),
    }
    if tp_price is not None:
        body['tp_price'] = tp_price
    if sl_price is not None:
        body['sl_price'] = sl_price

    url = f"{listener_url.rstrip('/')}/strategies/{strategy_id}/adjust-stops"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
        return {
            'status_code': resp.status_code,
            'error': None if resp.status_code == 200 else resp.text,
        }
    except Exception as exc:
        logger.error("dispatch_adjust_stops error: %s", exc)
        return {'status_code': None, 'error': str(exc)}


async def dispatch_webhook(
    payload: dict,
    strategy_id: str,
    secret: str,
    listener_url: str,
) -> dict:
    try:
        signature = sign_payload(payload, secret)
        url = f"{listener_url.rstrip('/')}/webhook/{strategy_id}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={'X-Agent-Signature': signature},
            )

        order_id = None
        if resp.status_code == 200:
            try:
                order_id = resp.json().get('order_id')
            except Exception:
                pass

        return {
            'status_code': resp.status_code,
            'order_id':    str(order_id) if order_id else None,
            'error':       None if resp.status_code == 200 else resp.text,
        }

    except Exception as exc:
        logger.error("dispatch_webhook error: %s", exc)
        return {'status_code': None, 'order_id': None, 'error': str(exc)}
