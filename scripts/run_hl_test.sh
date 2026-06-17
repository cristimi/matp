#!/bin/bash
curl -v -X POST http://localhost:8001/webhook/test_hl_demo_01 \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: test-secret-hl-01" \
  -d "{
    \"base_asset\": \"BTC\",
    \"quote_asset\": \"USDT\",
    \"side\": \"buy\",
    \"order_type\": \"market\",
    \"size\": \"0.001\",
    \"leverage\": 10,
    \"margin_mode\": \"cross\",
    \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
    \"signal\": \"open_long\",
    \"signal_source\": \"TV_hyperliquid_BTCUSDT\",
    \"platform\": \"hyperliquid\",
    \"token\": \"test-secret-hl-01\"
  }"
