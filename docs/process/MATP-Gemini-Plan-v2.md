# MATP — Gemini CLI Implementation Plan
**Version:** 2.0  **Date:** 2026-05-31
**Sessions:** 2 × 1.5h/day  **Format:** Each session = paste-ready prompt (delivered as .md file)

---

## How to Use This Document

1. Before each session, find the session number and note the files Gemini must read
2. Claude delivers each prompt as a downloadable `.md` file — paste the full contents into Gemini CLI
3. After the session, paste `.gemini/REPORT_FOR_HUMAN.md` back to Claude
4. Claude produces the next adjusted prompt

---

## Changelog from Plan v1.0

- Added Session 7b (webhook payload cleanup + `symbol_validator.py` stub) between Sessions 7 and 8
- Session 15 now implements Symbol Coupling logic (was: risk management only)
- Session 16 now implements `target_position = "flat"` handler (was: risk management drawdown)
- Sessions 17–18 shifted accordingly (risk management moved to 17–18)
- Sessions 19–20 unchanged (testing + cleanup)
- SDD reference updated to v4.0 throughout

---

## Current State (as of Session 6 complete)

**Built and working:**
- Full Docker Compose stack (8 services including order-executor)
- `order-listener`: FastAPI, HMAC auth, webhook routing, executor_client, deprecated router.py
- `order-executor`: AccountRegistry, BlofinAdapter, HyperliquidAdapter stub, retry logic
- `dashboard-api`: Express, orders/stats/config/strategies/positions/accounts routes, WebSocket feed
- `dashboard-ui`: React, full nav, Orders/Positions/Strategies/Settings pages
- `db/migrations/001_exchange_accounts.sql`: applied and verified
- Sessions 1–3 audit: all modules corrected and verified

**Not yet built:**
- `symbol_validator.py` in order-listener
- Webhook payload still uses `symbol` (not `base_asset`/`quote_asset`)
- Hyperliquid adapter full ECDSA implementation (Session 7)
- `db/migrations/002_symbol_coupling.sql`
- Symbol Coupling logic in webhook_handler
- UI redesign to v0.37 spec
- Accounts page in UI
- Risk management guards
- End-to-end test suite

---

## Phasing Overview

| Phase | Sessions | Goal |
|-------|----------|------|
| **A** | 1–2 | ✅ Session infrastructure + DB migration |
| **B** | 3–5 | ✅ order-executor internals + listener wiring |
| **C** | 6 | ✅ Dashboard API accounts endpoints |
| **D** | 7 | Hyperliquid full ECDSA implementation |
| **D+** | 7b | Webhook payload cleanup + symbol_validator stub |
| **E** | 8–9 | Dashboard API: positions, orders account filter, credential flow |
| **F** | 10–14 | Dashboard UI: Accounts page + full v0.37 redesign |
| **G** | 15–16 | Symbol Coupling logic + target_position flat handler |
| **H** | 17–18 | Risk management: max size, leverage guard, drawdown stop |
| **I** | 19–20 | End-to-end testing + cleanup |

---

## SESSION 7

**Phase D — Hyperliquid full ECDSA implementation**
See `MATP-Session-07-Prompt.md` (already delivered).
**⚠️ Do NOT run on Flash Lite.**

---

## SESSION 7b
**Phase D+ — Webhook payload cleanup + symbol_validator stub**
**Goal:** Refactor webhook payload to `base_asset`/`quote_asset`; create `symbol_validator.py` stub; write migration 002
**Estimated Gemini work time:** 65 min
**Flash Lite safe:** Yes — mechanical refactor with exact specs provided
**Prerequisite:** Session 7 complete and report received

> Claude delivers this as `MATP-Session-07b-Prompt.md` after Session 7 report is received.

**What this session does:**
1. Creates `db/migrations/002_symbol_coupling.sql` adding `allow_quote_variants` and `allow_cross_charting` to strategies table
2. Applies the migration to the running database
3. Creates `order-listener/app/symbol_validator.py` as a complete implementation (strict + quote_variants + cross_charting modes, price stripping)
4. Refactors `WebhookPayload` in `order-listener/app/models.py` from `symbol` to `base_asset` + `quote_asset`, removes legacy fields, adds `target_position`
5. Updates `webhook_handler.py` to call `symbol_validator.resolve_symbol()` and pass resolved execution symbol to executor
6. Updates `dashboard-api/src/routes/strategies.ts` `PUT /:id` to accept `allow_quote_variants` and `allow_cross_charting`
7. Smoke test: send a webhook with mismatched quote asset to confirm `422` when flags are off, `200` when `allow_quote_variants` is on

---

## SESSION 8
**Phase E — Part 1**
**Goal:** Dashboard API — positions endpoint wired to executor; orders endpoint `account_id` filter
**Estimated Gemini work time:** 60 min
**Flash Lite safe:** Yes
**Prerequisite:** Session 7b complete

> Claude delivers this as `MATP-Session-08-Prompt.md` after Session 7b report is received.

**What this session does:**
1. Creates or updates `dashboard-api/src/routes/positions.ts` to call `order-executor:8004`'s `get_open_positions` per active account and aggregate results
2. Adds `account_id` filter to `GET /orders` query
3. Adds `GET /strategies/:id` single strategy endpoint returning full config including coupling flags
4. Smoke test: `curl /api/dashboard/positions` returns array (empty is fine at this stage)

---

## SESSION 9
**Phase E — Part 2**
**Goal:** Dashboard API — stats endpoint with per-strategy and per-account breakdown; credential update flow
**Estimated Gemini work time:** 65 min
**Flash Lite safe:** Partial (Task 1 requires judgment; Tasks 2–3 mechanical)

> Claude delivers this as `MATP-Session-09-Prompt.md` after Session 8 report is received.

**What this session does:**
1. Implements `GET /stats` with `by_account` and `by_strategy` breakdown per §5.5
2. Adds `POST /accounts/:id/credentials` endpoint that accepts a `credentials_json` body, calls the executor's encrypt endpoint, stores the result — wires the credential update flow end to end
3. Adds executor `POST /credentials/encrypt` internal endpoint that encrypts a JSON string using `credentials.py` and returns the bytes as base64

---

## SESSION 10
**Phase F — Part 1**
**Goal:** Dashboard UI — Accounts page (list, add, edit, deactivate)
**Estimated Gemini work time:** 70 min
**Flash Lite safe:** Yes
**Design reference:** `docs/MATP_UI_IMPLEMENTATION_PLAN.md` — no specific page spec; follow existing UI patterns

> Claude delivers this as `MATP-Session-10-Prompt.md` after Session 9 report is received.

**What this session does:**
1. Creates `dashboard-ui/src/pages/Accounts.tsx` with: account list table, add account modal (id/exchange/mode/label), edit label modal, deactivate button
2. Adds Accounts link to navigation
3. Credential update is shown as "Update credentials" button that opens a textarea — submits to `POST /accounts/:id/credentials`
4. Mode badge: green "live", blue "demo"
5. Smoke test: add `acc_test_session10`, verify it appears in list, deactivate it

---

## SESSION 11
**Phase F — Part 2**
**Goal:** Dashboard UI — Strategies screen redesign to v0.37 spec
**Estimated Gemini work time:** 80 min
**Flash Lite safe:** NO — card anatomy is complex
**Design reference:** `strategies_added_by_Gemini.html` (v0.37) + `docs/MATP_UI_IMPLEMENTATION_PLAN.md` §3

> Claude delivers this as `MATP-Session-11-Prompt.md` after Session 10 report is received.

**What this session does:**
1. Rebuilds `dashboard-ui/src/pages/Strategies.tsx` to v0.37 card anatomy
2. Adds Symbol Coupling toggles per §4.4.2: `Allow Quote Variants` and `Allow Cross-Charting` toggles on each strategy card
3. Adds warning badge on strategy card when `allow_cross_charting` is true
4. Implements active/inactive sections with section headers
5. Action band: active → Stop Strategy; inactive → Start Strategy

---

## SESSION 12
**Phase F — Part 3 / Phase G — Part 1**
**Goal:** Dashboard UI — shared components library
**Estimated Gemini work time:** 70 min
**Flash Lite safe:** Yes
**Design reference:** `docs/MATP_UI_IMPLEMENTATION_PLAN.md` §2

> Claude delivers this as `MATP-Session-12-Prompt.md` after Session 11 report is received.

**What this session does:**
1. Creates `dashboard-ui/src/components/shared/`: `HeaderPill.tsx`, `DataGrid.tsx`, `ActionBand.tsx`, `SummaryBar.tsx`, `SectionHeader.tsx`, `TopBar.tsx`, `BottomNav.tsx`, `FilterBar.tsx`
2. Creates `dashboard-ui/src/utils/precision.ts`, `datetime.ts`, `pnl.ts`
3. Creates `dashboard-ui/src/styles/tokens.css` with exact design tokens from v0.33
4. Refactors Strategies page to use shared components

---

## SESSION 13
**Phase G — Part 2**
**Goal:** Dashboard UI — Positions screen redesign to v0.37 spec
**Estimated Gemini work time:** 80 min
**Flash Lite safe:** NO
**Design reference:** `matp-ui-v0.33.html` positions screen + `docs/MATP_UI_IMPLEMENTATION_PLAN.md` §4

> Claude delivers this as `MATP-Session-13-Prompt.md` after Session 12 report is received.

**What this session does:**
1. Rebuilds `dashboard-ui/src/pages/Positions.tsx` to v0.33/v0.37 card anatomy
2. Live / Stale / Closed sections
3. P&L inline format: `+14.85 (−0.55)` baseline-aligned
4. Stale position: all values in `var(--failed-color)`
5. Action band: open → Close Position; stale → Refresh + Close Position; closed → closed band with reason
6. Decimal precision via `formatPrice` / `formatSize` from `precision.ts`

---

## SESSION 14
**Phase G — Part 3**
**Goal:** Dashboard UI — Orders screen redesign to v0.37 spec
**Estimated Gemini work time:** 65 min
**Flash Lite safe:** Partial
**Design reference:** `matp-ui-v0.33.html` orders screen + `docs/MATP_UI_IMPLEMENTATION_PLAN.md` §5

> Claude delivers this as `MATP-Session-14-Prompt.md` after Session 13 report is received.

**What this session does:**
1. Rebuilds `dashboard-ui/src/pages/Orders.tsx` to v0.33/v0.37 card anatomy
2. lag-fail: Delete Log only; route-fail: Retry + Delete; pending: Cancel Order; filled: no footer
3. Bottom nav dot when any order has `lag-fail` or `route-fail` status
4. Routing: `/strategies`, `/positions`, `/orders` with default redirect from `/` to `/positions`

---

## SESSION 15
**Phase G — Symbol Coupling implementation**
**Goal:** Implement full Symbol Coupling logic in order-listener
**Estimated Gemini work time:** 75 min
**Flash Lite safe:** NO — logic requires judgment
**Prerequisite:** Session 7b must be complete (symbol_validator stub exists)

> Claude delivers this as `MATP-Session-15-Prompt.md` after Session 14 report is received.

**What this session does:**
1. Replaces `symbol_validator.py` stub with full implementation:
   - `resolve_symbol(base_asset, quote_asset, strategy_symbol, allow_quote_variants, allow_cross_charting) -> ResolvedSymbol`
   - `ResolvedSymbol` carries: `execution_symbol`, `price_stripped: bool`, `coupling_used: str | None`
   - Strict mode: exact match only
   - Quote variants mode: USD/USDC/USDT/PERP interchangeable
   - Cross-charting mode: base asset match only
   - `SymbolMismatchError` raised when no mode covers the mismatch
2. Updates `webhook_handler.py` to use `resolve_symbol()`, apply price stripping, log `coupling_used` to order record
3. Adds `symbol_rejected` to order status lifecycle
4. Integration test: 4 curl tests covering strict match, quote variant match, cross-chart match, and mismatch rejection

---

## SESSION 16
**Phase G — target_position flat handler**
**Goal:** Implement `target_position = "flat"` signal handling
**Estimated Gemini work time:** 55 min
**Flash Lite safe:** Partial

> Claude delivers this as `MATP-Session-16-Prompt.md` after Session 15 report is received.

**What this session does:**
1. Updates `webhook_handler.py` to detect `target_position = "flat"` and call `executor_client.close_position()` instead of `execute()`
2. Adds `close_position` endpoint to order-executor: `POST /close-position` accepting `{ account_id, symbol, side }`
3. Adds `executor_client.close_position()` in order-listener
4. Smoke test: send `target_position: "flat"` webhook, confirm executor receives close_position call

---

## SESSION 17
**Phase H — Part 1**
**Goal:** Risk management — max order size guard and leverage limit check
**Estimated Gemini work time:** 55 min
**Flash Lite safe:** Yes

> Claude delivers this as `MATP-Session-17-Prompt.md` after Session 16 report is received.

**What this session does:**
1. Adds size validation in `webhook_handler.py` against `strategies.max_position_size`
2. Adds leverage validation against `strategies.max_leverage`
3. Both return `422` with descriptive message on violation
4. Adds `signals_today` increment on each accepted signal
5. Adds `max_daily_signals` check — returns `429` when exceeded

---

## SESSION 18
**Phase H — Part 2**
**Goal:** Risk management — daily drawdown stop per strategy
**Estimated Gemini work time:** 65 min
**Flash Lite safe:** NO

> Claude delivers this as `MATP-Session-18-Prompt.md` after Session 17 report is received.

**What this session does:**
1. Adds `pnl_today` tracking: updated on each order fill via Redis event in order-listener
2. Adds drawdown check in `webhook_handler.py`: if `pnl_today < -(allocated * max_daily_drawdown_percent / 100)`, reject new signals with `429` and auto-disable strategy
3. Adds `POST /strategies/:id/reset-daily` endpoint in dashboard-api to clear `pnl_today` and re-enable
4. Smoke test: manually set `pnl_today` to a large negative value and confirm next webhook is rejected

---

## SESSION 19
**Phase I — End-to-end testing**
**Goal:** Pytest suite for order-listener + full pipeline curl test script
**Estimated Gemini work time:** 75 min
**Flash Lite safe:** Partial (test writing is mechanical; test design requires judgment)

> Claude delivers this as `MATP-Session-19-Prompt.md` after Session 18 report is received.

**What this session does:**
1. Creates `order-listener/tests/test_webhook_handler.py` — pytest with httpx TestClient covering: HMAC auth, payload validation, symbol coupling (all 4 cases), price stripping, size/leverage guard, daily signal limit
2. Creates `order-listener/tests/test_symbol_validator.py` — unit tests for all resolve_symbol() cases
3. Creates `scripts/e2e_test.sh` — curl script testing the full pipeline with a real TradingView-format webhook
4. Runs all tests and records results in REPORT_FOR_HUMAN.md

---

## SESSION 20
**Phase I — Cleanup and finalisation**
**Goal:** Remove deprecated files, update docs, final docker compose up --build
**Estimated Gemini work time:** 45 min
**Flash Lite safe:** Yes

> Claude delivers this as `MATP-Session-20-Prompt.md` after Session 19 report is received.

**What this session does:**
1. Deletes `order-listener/app/router.py` (deprecated in Session 5)
2. Updates `.env.example` with all required variables including `MASTER_KEY` and `EXECUTOR_URL`
3. Updates `docs/tradingview.md` with new `base_asset`/`quote_asset` payload format and Symbol Coupling explanation
4. Runs `docker compose down && docker compose up --build` and verifies all 8 services healthy
5. Final smoke test: full webhook → executor → Blofin demo pipeline with a real strategy

---

## Session Recovery Protocol

If a session is **interrupted mid-task**, paste this to Claude along with `CHECKPOINT.md`:
> "Session N was interrupted. Here is the checkpoint file. Please produce a recovery prompt."

If a session **fails to build**, paste the error and `REPORT_FOR_HUMAN.md`:
> "Session N produced a build error. Here is the error and the report. Please produce a fix prompt."

If **Flash Lite was used on a session marked Flash Lite safe: NO**, paste the report:
> "Session N ran on Flash Lite. Please review the report for quality issues and produce a verification/fix prompt."

---

## File Reference Card

| What Gemini reads for context | Where it lives |
|-------------------------------|----------------|
| Architecture source of truth | `docs/MATP.SDD.md` (v4.0) |
| Current DB schema | `db/init.sql` + `db/migrations/` |
| Previous session handoff | `.gemini/NEXT_SESSION.md` |
| Current task state | `.gemini/CHECKPOINT.md` |
| Full session history | `.gemini/SESSION_LOG.md` |
| UI design spec | `docs/MATP_UI_IMPLEMENTATION_PLAN.md` |
| UI reference HTML v0.33 | `matp-ui-v0.33.html` |
| UI reference HTML v0.37 | `strategies_added_by_Gemini.html` |

| What Gemini produces for you | Where it writes |
|------------------------------|-----------------|
| Report to paste to Claude | `.gemini/REPORT_FOR_HUMAN.md` |
| Session history (append-only) | `.gemini/SESSION_LOG.md` |
| Context for next session | `.gemini/NEXT_SESSION.md` |
| Current task state | `.gemini/CHECKPOINT.md` |

---

## Flash Lite Safety Summary

| Session | Flash Lite Safe | Reason if NO |
|---------|-----------------|--------------|
| 7 | ❌ NO | ECDSA cryptographic signing |
| 7b | ✅ Yes | Mechanical refactor with exact specs |
| 8 | ✅ Yes | TypeScript API routes |
| 9 | ⚠️ Partial | Stats aggregation requires judgment |
| 10 | ✅ Yes | UI component transcription |
| 11 | ❌ NO | v0.37 card anatomy complexity |
| 12 | ✅ Yes | Shared component transcription |
| 13 | ❌ NO | Positions card anatomy + P&L logic |
| 14 | ⚠️ Partial | Orders simpler than positions |
| 15 | ❌ NO | Symbol coupling logic design |
| 16 | ⚠️ Partial | Handler logic requires judgment |
| 17 | ✅ Yes | Mechanical validation checks |
| 18 | ❌ NO | Drawdown logic requires judgment |
| 19 | ⚠️ Partial | Test design requires judgment |
| 20 | ✅ Yes | Cleanup and docs |
