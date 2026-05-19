# MATP Test Plan: Webhook to Exchange Integration

**Version:** 1.0
**Scope:** Order Listener, Blofin Adapter, TradingView Integration

## 1. Unit Testing (Order Listener)
- [ ] **Payload Validation:**
    - Test `WebhookPayload` with all required fields.
    - Test `WebhookPayload` with missing optional fields (ensure defaults work).
    - Test invalid `signal` types (e.g., `buy_now`).
- [ ] **Authentication Logic:**
    - Test token in Header (`X-Webhook-Token`).
    - Test token in Body (`token` field).
    - Test rejection of invalid tokens (403).

## 2. Integration Testing (Internal Flow)
- [ ] **Database Logging:**
    - Verify `orders` record creation on webhook receipt.
    - Verify `order_events` trail (received -> routing -> filled/failed).
    - Verify `signal_source`, `signal_metadata`, and `indicator_price` are correctly saved.
- [ ] **Routing Logic:**
    - Verify routing to `blofin` when `active_platform` is set.
    - Verify `strategy_id` based platform overrides.

## 3. Exchange Adapter Testing (Blofin)
- [ ] **Connectivity:**
    - Successful HMAC signature generation.
    - Successful `get_open_positions` call.
- [ ] **Order Placement:**
    - Market Buy (Open Long).
    - Market Sell (Close Long / Open Short).
    - Verify response parsing (extracting `exchange_order_id`).
- [ ] **Failure Modes:**
    - Invalid symbol (rejected by exchange).
    - Insufficient balance.
    - Unauthorized API key.

## 4. End-to-End Testing (TradingView)
- [ ] **Network Path:**
    - TV -> Zoraxy -> Nginx -> Order Listener.
    - Verify bypass of Authelia for the `/webhook/` path.
- [ ] **Signal Transformation:**
    - TV Alert JSON -> Order Listener Model -> Blofin API Request.
- [ ] **Observability:**
    - Verify the order appears in the Dashboard UI in real-time via WebSockets.

## 5. Regression Testing
- [ ] Verify that internal `order-generator` signals still work correctly with the new schema changes.
