import hmac
import hashlib
import json
import requests
from datetime import datetime

SECRET = 'deadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678'
PAYLOAD = {
    "symbol": "BTCUSDT",
    "side": "buy",
    "signal": "test_signal",
    "orderType": "market",
    "size": "0.001",
    "leverage": 1,
    "marginMode": "isolated",
    "platform": "test",
    "strategyId": "test-strategy",
    "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
}

payload_str = json.dumps(PAYLOAD, separators=(',', ':'))
sig = hmac.new(SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()

headers = {
    "X-Webhook-Signature": sig,
    "Content-Type": "application/json"
}

url = "http://localhost/api/listener/webhook"
response = requests.post(url, data=payload_str, headers=headers)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.text}")
