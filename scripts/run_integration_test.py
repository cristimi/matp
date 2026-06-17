import hmac
import hashlib
import json
import os
from datetime import datetime, timezone
import requests

# Use values from database
strategy_id = "manual-test"
webhook_secret = "manual-test-secret-12345"

payload_dict = {
    "symbol": "BTCUSDT",
    "side": "buy",
    "signal": "test_signal",
    "orderType": "market",
    "size": "0.001",
    "leverage": 1,
    "marginMode": "isolated",
    "platform": "test",
    "strategyId": strategy_id,
    "signalToken": webhook_secret,  # Send the secret as the token
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
}

payload_str = json.dumps(payload_dict, separators=(',', ':'))

url = f"http://localhost/api/listener/webhook/{strategy_id}"
headers = {
    "Content-Type": "application/json"
}

print(f"Sending payload: {payload_str}")

response = requests.post(url, headers=headers, data=payload_str)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
