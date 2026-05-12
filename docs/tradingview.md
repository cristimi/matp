# TradingView Alert Setup

## Webhook URL

```
http://<your-local-ip>/api/listener/webhook
```

> **Note:** TradingView requires a publicly accessible URL. For local hosting use:
> - A VPN (WireGuard / Tailscale) to expose your local machine, **or**
> - A tunnel service (ngrok, Cloudflare Tunnel) during testing

## Alert Message Format

Paste this JSON into the TradingView alert **Message** field:

```json
{
  "symbol": "{{ticker}}",
  "side": "buy",
  "signal": "open_long",
  "orderType": "market",
  "size": "0.01",
  "leverage": 10,
  "marginMode": "cross",
  "platform": "auto",
  "strategyId": "my-tv-strategy",
  "timestamp": "{{timenow}}",
  "token": "YOUR_WEBHOOK_SECRET"
}
```

## Signal Values

| `signal` | `side` | Description |
|----------|--------|-------------|
| `open_long` | `buy` | Enter a long position |
| `close_long` | `sell` | Close an existing long |
| `open_short` | `sell` | Enter a short position |
| `close_short` | `buy` | Close an existing short |

## Platform Routing

| `platform` value | Routes to |
|-----------------|-----------|
| `auto` | Whichever platform is set in Dashboard → Settings |
| `blofin` | Blofin Signal Bot always |
| `hyperliquid` | Hyperliquid always |

## Multiple Alerts (Long & Short)

Create two separate TradingView alerts — one for entry, one for exit:

**Open Long alert:**
```json
{
  "symbol": "{{ticker}}",
  "side": "buy",
  "signal": "open_long",
  "orderType": "market",
  "size": "0.01",
  "leverage": 10,
  "platform": "auto",
  "strategyId": "my-rsi-strategy",
  "timestamp": "{{timenow}}",
  "token": "YOUR_WEBHOOK_SECRET"
}
```

**Close Long alert:**
```json
{
  "symbol": "{{ticker}}",
  "side": "sell",
  "signal": "close_long",
  "orderType": "market",
  "size": "0.01",
  "leverage": 10,
  "platform": "auto",
  "strategyId": "my-rsi-strategy",
  "timestamp": "{{timenow}}",
  "token": "YOUR_WEBHOOK_SECRET"
}
```

## Optional Fields

| Field | Description |
|-------|-------------|
| `price` | Required for limit orders |
| `tpPrice` | Take profit trigger price |
| `slPrice` | Stop loss trigger price |
| `marginMode` | `"cross"` (default) or `"isolated"` |

## Testing with curl

```bash
curl -X POST http://localhost/api/listener/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC-USDT",
    "side": "buy",
    "signal": "open_long",
    "orderType": "market",
    "size": "0.001",
    "leverage": 10,
    "platform": "auto",
    "strategyId": "test",
    "timestamp": "2026-05-11T10:00:00Z",
    "token": "YOUR_WEBHOOK_SECRET"
  }'
```

Expected response:
```json
{"order_id": "uuid", "status": "received", "message": "OK"}
```
