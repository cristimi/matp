# MATP Action Plan: TradingView to Blofin Signal Bots

**Date:** 2026-05-19
**Status:** Active Implementation

## Objective
Enable seamless reception of TradingView signals and execution on Blofin as automated signal bots, with full observability in the MATP Dashboard.

## 1. Immediate Technical Fixes
- [ ] **Blofin API Authorization:** Diagnose and fix the "Unauthorized" error from Blofin.
    - Verify `BLOFIN_API_KEY` and `BLOFIN_API_SECRET` in `.env`.
    - Confirm the signature logic in `blofin.py` matches Blofin's latest API documentation.
- [ ] **Blofin Adapter Completion:** 
    - Implement a robust `close_position` method that uses Blofin's `reduceOnly` or `close` parameters.
    - Ensure `size` calculations are correct for Blofin's minimum requirements.
- [ ] **DNS Stability:** 
    - Monitor `webhooks.bbs15.duckdns.org` propagation.
    - If instability persists, fallback to the verified `trading.bbs15.duckdns.org` domain for webhook ingestion.

## 2. TradingView Alert Integration
- [ ] **Finalize Alert Template:**
    - Standardize the JSON message format to include `signal_source: "tradingview"`, `token`, and `indicator_price`.
    - Document the template in `docs/tradingview.md`.
- [ ] **Live Test:**
    - Trigger a manual alert from TV.
    - Verify receipt in `order-listener` logs.
    - Verify record creation in PostgreSQL with correct `signal_source` and `indicator_price`.

## 3. Dashboard Observability
- [ ] **UI Updates:**
    - Add "Origin" (Source) and "Ind. Price" columns to the Orders page table.
    - Implement badges for different signal sources (TV icon for TradingView, Gear icon for Internal).
- [ ] **Real-time Feed:**
    - Ensure the live feed highlights the source of the signal.

## 4. Automation & Robustness
- [ ] **Internal Scheduler Sync:**
    - Update `order-generator` to include `signal_source: "internal"` in its generated signals.
- [ ] **Error Handling:**
    - Improve `route_failed` logging to capture the exact reason from Blofin (e.g., "Insufficient Margin").

## Timeline
- **Today:** Fix Blofin Auth and DNS.
- **Tomorrow:** Finalize TV Alert format and perform end-to-end live testing.
- **Day after:** Update UI for enhanced observability.
