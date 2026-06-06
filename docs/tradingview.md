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

Paste this JSON into the TradingView alert **Message** field.

```json
{
  "base_asset":  "{{syminfo.basecurrency}}",
  "quote_asset": "{{syminfo.currency}}",
  "side":        "buy",
  "signal":      "open_long",
  "order_type":  "market",
  "size":        "0.01",
  "leverage":    10,
  "margin_mode": "cross",
  "timestamp":   "{{timenow}}",
  "token":       "YOUR_STRATEGY_WEBHOOK_SECRET"
}
```

## Symbol Coupling

MATP can accept signals from chart symbols that differ from the execution symbol configured on the strategy.

### Allow Quote Variants
Enable this flag on a strategy to accept signals where the quote currency differs but is economically equivalent:
- Acceptable variants: USD, USDC, USDT, PERP
- Example: BTC-USDC signal → executes as BTC-USDT
- **Price parameters are automatically stripped** — order executes as market order

### Allow Cross-Charting
Enable this flag to accept signals from any chart with the same base asset, regardless of quote currency:
- Example: BTC-EUR chart signal → executes as BTC-USDT
- ⚠️ Price parameters are always stripped when this flag is active
- Use with caution — enable only when trading index charts

## Signal Values

| `signal` | `side` | Description |
|----------|--------|-------------|
| `open_long` | `buy` | Enter a long position |
| `close_long` | `sell` | Close an existing long |
| `open_short` | `sell` | Enter a short position |
| `close_short` | `buy` | Close an existing short |

## Target Position

| `target_position` | Description |
|-------------------|-------------|
| `flat` | Closes any open position for the strategy regardless of side |

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
  "base_asset":  "{{syminfo.basecurrency}}",
  "quote_asset": "{{syminfo.currency}}",
  "side":        "buy",
  "signal":      "open_long",
  "order_type":  "market",
  "size":        "0.01",
  "leverage":    10,
  "timestamp":   "{{timenow}}",
  "token":       "YOUR_STRATEGY_WEBHOOK_SECRET"
}
```

**Close Long alert:**
```json
{
  "base_asset":  "{{syminfo.basecurrency}}",
  "quote_asset": "{{syminfo.currency}}",
  "side":        "sell",
  "signal":      "close_long",
  "order_type":  "market",
  "size":        "0.01",
  "timestamp":   "{{timenow}}",
  "token":       "YOUR_STRATEGY_WEBHOOK_SECRET"
}
```

## Optional Fields

| Field | Description |
|-------|-------------|
| `price` | Required for limit orders |
| `tp_price` | Take profit trigger price |
| `sl_price` | Stop loss trigger price |
| `margin_mode` | `"cross"` (default) or `"isolated"` |
| `indicator_price` | The price of the indicator at signal time |
| `signal_source` | Source of the signal (e.g., `"tradingview"`) |
| `signal_metadata` | JSON object for custom data |

## Testing with curl

```bash
curl -X POST http://localhost/api/listener/webhook/strat-001 \
  -H "Content-Type: application/json" \
  -d '{
    "base_asset": "BTC",
    "quote_asset": "USDT",
    "side": "buy",
    "signal": "open_long",
    "order_type": "market",
    "size": "0.001",
    "leverage": 10,
    "indicator_price": 65000,
    "timestamp": "2026-05-20T10:00:00Z",
    "token": "STRATEGY_SECRET_TOKEN"
  }'
```
