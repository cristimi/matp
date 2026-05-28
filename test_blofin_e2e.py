import hmac
import hashlib
import json
import os
from datetime import datetime, timezone
import requests
import time

# Use values from database
strategy_id = "manual-test"
webhook_secret = "manual-test-secret-12345"
url = f"http://localhost/api/listener/webhook/{strategy_id}"

def send_signal(side, symbol="BTCUSDT"):
    payload_dict = {
        "symbol": symbol,
        "side": side,
        "signal": f"test_{side}",
        "orderType": "market",
        "size": "0.1", # Smallest size for Blofin Demo BTC-USDT often 0.1 contracts
        "leverage": 10,
        "marginMode": "cross",
        "platform": "blofin", # Explicitly target blofin
        "strategyId": strategy_id,
        "signalToken": webhook_secret,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    payload_str = json.dumps(payload_dict, separators=(',', ':'))
    headers = {"Content-Type": "application/json"}

    print(f"\n--- Sending {side.upper()} Signal ---")
    print(f"Payload: {payload_str}")

    try:
        response = requests.post(url, headers=headers, data=payload_str)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.json()
    except Exception as e:
        print(f"Error: {e}")
        return None

# Send 2 signals: Buy then Sell
buy_res = send_signal("buy")
time.sleep(2) # Brief pause
sell_res = send_signal("sell")

print("\n--- Test Finished ---")
