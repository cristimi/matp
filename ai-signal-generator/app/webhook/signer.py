import json
import hmac
import hashlib


def sign_payload(payload: dict, secret: str) -> str:
    payload_bytes = json.dumps(
        payload, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')
    return hmac.new(
        secret.encode('utf-8'), payload_bytes, hashlib.sha256
    ).hexdigest()
