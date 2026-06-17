#!/bin/bash
PAYLOAD='{"symbol":"BTCUSDT","side":"buy","signal":"test_signal","orderType":"market","size":"0.001","leverage":1,"marginMode":"isolated","platform":"test","strategyId":"manual-test","timestamp":"2026-05-24T06:15:00Z"}'
# Send the token directly in the request header, which bypasses the HMAC requirement check
curl -v -X POST http://localhost/api/listener/webhook/manual-test   -H "X-Webhook-Token: manual-test-secret-12345"   -H "Content-Type: application/json"   -d "$PAYLOAD"
