# TradingView Alert Setup

## Webhook URL

Each strategy has its own unique webhook endpoint:

```
http://<your-public-ip>/api/listener/webhook/<strategy_id>
```

Replace `<strategy_id>` with the ID of your strategy as configured in the MATP Dashboard.

> **Note:** TradingView requires a publicly accessible URL. For local hosting use:
> - A VPN (WireGuard / Tailscale) to expose your local machine, **or**
> - A tunnel service (ngrok, Cloudflare Tunnel) during testing

## Alert Message Format

Paste this JSON into the TradingView alert **Message** field. It is recommended to use the `signalToken` field for authentication.

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
  "indicator_price": "{{close}}",
  "signal_source": "tradingview",
  "timestamp": "{{timenow}}",
  "signalToken": "YOUR_STRATEGY_WEBHOOK_SECRET"
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

## Example: RSI Strategy (Long & Short)

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
  "indicator_price": "{{close}}",
  "timestamp": "{{timenow}}",
  "signalToken": "YOUR_STRATEGY_WEBHOOK_SECRET"
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
  "indicator_price": "{{close}}",
  "timestamp": "{{timenow}}",
  "signalToken": "YOUR_STRATEGY_WEBHOOK_SECRET"
}
```

## Optional Fields

| Field | Description |
|-------|-------------|
| `price` | Required for limit orders |
| `tpPrice` | Take profit trigger price |
| `slPrice` | Stop loss trigger price |
| `marginMode` | `"cross"` (default) or `"isolated"` |
| `indicator_price` | The price of the indicator at signal time |
| `signal_metadata` | JSON object for custom data |

## Testing with curl

```bash
curl -X POST http://localhost/api/listener/webhook/strat-001 \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC-USDT",
    "side": "buy",
    "signal": "open_long",
    "orderType": "market",
    "size": "0.001",
    "leverage": 10,
    "indicator_price": 65000,
    "timestamp": "2026-05-20T10:00:00Z",
    "signalToken": "STRATEGY_SECRET_TOKEN"
  }'
```
