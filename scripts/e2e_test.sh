#!/bin/bash
# MATP End-to-End Pipeline Test
# Tests the full signal → listener → executor → response chain.
# Run from the repo root: bash scripts/e2e_test.sh
#
# Prerequisites:
#   - All services running (docker compose up -d)
#   - A strategy exists in the DB with webhook_secret set
#   - Edit STRATEGY_ID and WEBHOOK_SECRET below

set -e

STRATEGY_ID="${1:-manual-test}"
WEBHOOK_SECRET="${2:-manual-test-secret-12345}"
BASE_URL="http://localhost"
PASS=0
FAIL=0

run_test() {
  local name="$1"
  local expected_status="$2"
  local payload="$3"

  response=$(curl -s -w "\n%{http_code}" -X POST \
    "$BASE_URL/api/listener/webhook/$STRATEGY_ID" \
    -H "Content-Type: application/json" \
    -d "$payload")

  http_code=$(echo "$response" | tail -1)
  body=$(echo "$response" | head -n -1)

  if [ "$http_code" = "$expected_status" ]; then
    echo "✓ PASS: $name (HTTP $http_code)"
    PASS=$((PASS + 1))
  else
    echo "✗ FAIL: $name (expected HTTP $expected_status, got HTTP $http_code)"
    echo "  Body: $body"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "MATP End-to-End Pipeline Test"
echo "Strategy: $STRATEGY_ID"
echo "Base URL: $BASE_URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Test 1: Invalid token → 403
run_test "Invalid token rejected" "403" "{
  \"base_asset\": \"BTC\", \"quote_asset\": \"USDT\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"0.001\",
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"wrong_token\"
}"

# Test 2: Missing field → 422
run_test "Missing base_asset rejected" "422" "{
  \"quote_asset\": \"USDT\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"0.001\",
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"$WEBHOOK_SECRET\"
}"

# Test 3: Symbol mismatch (assumes flags are off) → 422
run_test "Symbol mismatch rejected (flags off)" "422" "{
  \"base_asset\": \"BTC\", \"quote_asset\": \"EUR\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"0.001\",
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"$WEBHOOK_SECRET\"
}"

# Test 4: Oversized order → 422
run_test "Oversized order rejected" "422" "{
  \"base_asset\": \"BTC\", \"quote_asset\": \"USDT\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"99999.0\",
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"$WEBHOOK_SECRET\"
}"

# Test 5: Excessive leverage → 422
run_test "Excessive leverage rejected" "422" "{
  \"base_asset\": \"BTC\", \"quote_asset\": \"USDT\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"0.001\",
  \"leverage\": 9999,
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"$WEBHOOK_SECRET\"
}"

# Test 6: Valid signal reaches executor → 200
run_test "Valid signal accepted" "200" "{
  \"base_asset\": \"BTC\", \"quote_asset\": \"USDT\",
  \"side\": \"buy\", \"signal\": \"open_long\",
  \"order_type\": \"market\", \"size\": \"0.001\",
  \"leverage\": 10,
  \"margin_mode\": \"cross\",
  \"timestamp\": \"2026-06-01T00:00:00Z\",
  \"token\": \"$WEBHOOK_SECRET\"
}"

# Test 7: Dashboard API health
echo ""
echo "━━ Dashboard API ━━━━━━━━━━━━━━━━━━━━━━━━"
stats=$(curl -s "$BASE_URL/api/dashboard/stats?period=all")
if echo "$stats" | grep -q '"total_orders"'; then
  echo "✓ PASS: Stats endpoint returns data"
  PASS=$((PASS + 1))
else
  echo "✗ FAIL: Stats endpoint missing total_orders"
  FAIL=$((FAIL + 1))
fi

accounts=$(curl -s "$BASE_URL/api/dashboard/accounts")
if echo "$accounts" | grep -q '"exchange"'; then
  echo "✓ PASS: Accounts endpoint returns data"
  PASS=$((PASS + 1))
else
  echo "✗ FAIL: Accounts endpoint missing exchange field"
  FAIL=$((FAIL + 1))
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then
  echo "✓ All tests passed"
  exit 0
else
  echo "✗ $FAIL test(s) failed"
  exit 1
fi
