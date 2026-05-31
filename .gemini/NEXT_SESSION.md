# Next Session Context

## Audit & Correction Summary
- **db/migrations/001_exchange_accounts.sql**: CORRECTED (Trigger name updated to `update_exchange_accounts_modtime`, added `DO` block for idempotency).
- **order-executor/Dockerfile**: MATCHES (Verified Python 3.12-slim, EXPOSE 8004, and CMD).
- **order-executor/requirements.txt**: MATCHES (Verified all required packages present).
- **order-executor/app/models.py**: CORRECTED (Aligned `OrderRequest` and `OrderResult` fields exactly with spec. Added `AccountRecord`. Restored `Position` to avoid breaking existing code).
- **order-executor/app/credentials.py**: CORRECTED (Added `_get_key` and `encrypt`. Aligned `decrypt` with strict error handling and length checks).
- **order-executor/app/adapters/base.py**: CORRECTED (Aligned method signatures, changed `get_open_positions` return type to `list[dict]`).
- **order-executor/app/database.py**: CORRECTED (Refactored to module-level `_pool`, added pool size constraints, aligned `fetch_account` with spec).

## Verification Results
- All four Python verification commands (Imports, AESGCM Round-trip, Placeholder Detection) PASSED.
- Build passes and health check returns correctly via `docker compose exec`.

## What Session 7 must do
- Implement the Hyperliquid adapter in the `order-executor` service.
- This requires full ECDSA signing implementation.
- **WARNING**: Session 7 must **NOT** run on Flash Lite due to the complexity of the signing implementation and logic.
- **IMPORTANT**: Read `order-executor/app/credentials.py` and `order-executor/app/adapters/base.py` first as they have been updated to the required structural specification.

## Files Session 7 must read first
- `order-executor/app/adapters/hyperliquid.py` (current stub)
- `order-executor/app/adapters/blofin.py` (for structural reference)
- `order-executor/app/adapters/base.py` (updated in Audit)
- `order-executor/app/models.py` (updated in Audit)
- `order-executor/app/credentials.py` (updated in Audit)
