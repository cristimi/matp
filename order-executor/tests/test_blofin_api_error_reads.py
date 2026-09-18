"""
A BloFin read that answers HTTP 200 with an error `code` in the body (e.g. 152401
"Access key does not exist") must be reported as UNKNOWN, never as an empty
confirmed read. On 2026-09-17 a dead demo API key made get_open_positions return []
three reconciler passes in a row, and the reconciler closed a live BTC short in the DB.

No network calls: the pooled _client is patched.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.adapters.base import ExchangeUnavailableError
from app.adapters.blofin import BlofinAdapter

FAKE_CREDS = {"api_key": "k", "api_secret": "s", "api_passphrase": "p"}
DEAD_KEY = {"code": "152401", "msg": "Access key does not exist", "data": None}


def _adapter_answering(body: dict) -> BlofinAdapter:
    a = BlofinAdapter(FAKE_CREDS, mode="demo", position_mode="net")
    resp = MagicMock(status_code=200)
    resp.json.return_value = body
    a._client = MagicMock()
    a._client.get = AsyncMock(return_value=resp)
    return a


@pytest.mark.asyncio
async def test_positions_dead_key_is_unavailable_not_empty():
    with pytest.raises(ExchangeUnavailableError, match="152401"):
        await _adapter_answering(DEAD_KEY).get_open_positions()


@pytest.mark.asyncio
async def test_positions_ok_empty_is_still_empty():
    assert await _adapter_answering({"code": "0", "data": []}).get_open_positions() == []


@pytest.mark.asyncio
async def test_trigger_orders_dead_key_is_none():
    assert await _adapter_answering(DEAD_KEY).list_trigger_orders("BTC-USDT") is None


@pytest.mark.asyncio
async def test_open_orders_dead_key_raises():
    with pytest.raises(ExchangeUnavailableError, match="152401"):
        await _adapter_answering(DEAD_KEY).get_open_orders("BTC-USDT")
