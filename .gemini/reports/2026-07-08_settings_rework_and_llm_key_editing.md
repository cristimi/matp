# Dashboard UI — Tree/Nav Rework, Settings Page Rebuild, LLM Key Editing

**Date:** 2026-07-08 / 2026-07-09
**Branch:** main
**Status:** DONE — deployed and verified end-to-end across all 5 services

---

## Scope

This session covered a chain of UI/UX requests that built on each other:

1. Tree page: `+` add-strategy button, grouped Filters/Sort dropdowns, "Last Change" tiered sort
   (open positions → active → inactive, each newest-first), AI pill, higher-contrast symbol pill.
2. Fixed a dropdown-clipping bug (`overflowX: auto` was implicitly clipping vertical overflow,
   hiding the Filters/Sort panels underneath the page) and a bar-item sizing/alignment bug (mixed
   heights between the `+` button and the chip-style buttons).
3. Add/Edit strategy now navigate back to `/tree` on success (previously stranded the user on the
   no-longer-in-nav Strategies page).
4. AI Signal Log filters redone to match the Tree page's grouped-dropdown pattern.
5. **Settings page rebuilt**: dropped the stale "Active Platform" toggle and "Exchange Credentials"
   display (both superseded by the Accounts page / no longer reflect how routing actually works —
   see `docs/ROADMAP.md` Deferred Backlog for the dead-config investigation this surfaced), rebuilt
   "System Information" as a live service-health grid, re-scoped the Webhook Endpoint copy to
   TradingView strategies specifically, and added a new **LLM Provider Keys** section.
6. **LLM API keys are now editable from the UI**, encrypted at rest, decrypted by the consuming
   Python services at their own startup.

---

## LLM key architecture (the substantial new piece)

Per user decision: keys are encrypted in the `config` table using a new `CONFIG_SECRET_KEY`
(separate from order-executor's `MASTER_KEY` — LLM provider keys are not exchange credentials and
shouldn't share that trust boundary), rather than giving dashboard-api filesystem access to `.env`.

- **`dashboard-api/src/configSecret.ts`** — AES-256-GCM encrypt only (write-only; the UI never
  gets the plaintext back). Wire format: `nonce(12) + ciphertext + authTag(16)`, base64.
- **`dashboard-api/src/routes/config.ts`** — `GET /config/llm-keys` (masked configured/not status
  per provider), `PUT /config/llm-keys/:provider` (encrypts, upserts `config.key = 'llm_key_<provider>'`).
- **`{ai-signal-generator,strategy-tester,social-listener}/app/config_secrets.py`** — identical
  decrypt helper + `apply_llm_key_overrides(pool, settings)`, called once at each service's startup
  (right after DB init, before any LLM client is built) to override `settings.*_api_key` from the
  DB row if present, else keep the env-var default. **Takes effect on next restart only** — no
  hot-reload, matching the existing "edit `.env` and restart" workflow, just moved into the UI.
- **`cryptography` added** to all three Python services' `requirements.txt`.
- **`CONFIG_SECRET_KEY`** generated and added to `.env` + wired into `docker-compose.yml` for
  dashboard-api and the 3 consuming services.

### Full round-trip verification (done live, then cleaned up)

```
$ curl -s -X PUT http://localhost/api/dashboard/config/llm-keys/anthropic \
    -H "Content-Type: application/json" -d '{"api_key":"test-roundtrip-value-12345"}'
{"provider":"anthropic","configured":true,"updated_at":"2026-07-09T04:09:04.836Z"}

$ docker compose exec -T postgres psql -U matp -d matp -c \
    "SELECT key, left(value,40) AS value_preview FROM config WHERE key='llm_key_anthropic';"
        key        |              value_preview
-------------------+------------------------------------------
 llm_key_anthropic | b87LNbC+TmDMaeP7S2Lit2VEA4OOfhzXw4EFDQR0   <- ciphertext, not plaintext

$ docker compose exec -T ai-signal-generator python3 -c "
    ... await conn.fetchrow(\"SELECT value FROM config WHERE key='llm_key_anthropic'\")
    print('decrypted:', _decrypt(row['value']))"
decrypted: test-roundtrip-value-12345          <- Node-encrypted, Python-decrypted, matches

$ docker compose exec -T postgres psql -U matp -d matp -c "DELETE FROM config WHERE key = 'llm_key_anthropic';"
DELETE 1
$ curl -s http://localhost/api/dashboard/config/llm-keys
{"anthropic":{"configured":false,...   <- back to clean state, no leftover test data
```

---

## Deploy verification — all 5 services

**dashboard-api** — `tsc` passed, container healthy, new endpoints confirmed live:
```
$ curl -s http://localhost/api/dashboard/system/health-grid
{"http":[{"name":"dashboard-api","ok":true},{"name":"order-listener","ok":true},
 {"name":"order-generator","ok":true},{"name":"order-executor","ok":true},
 {"name":"ai-signal-generator","ok":true},{"name":"strategy-tester","ok":true},
 {"name":"notification-service","ok":true}],
 "workers":["market-ingestion","signal-engine","social-listener"]}
```

**ai-signal-generator** — full cold pip reinstall (requirements.txt changed) + `cryptography`
installed cleanly. A few early healthcheck failures right after restart (uvicorn still binding,
no `start_period` configured) self-resolved: `docker inspect` confirms `Status: "healthy"`,
`FailingStreak: 0`. Model probe still passes (`google/gemini-2.5-flash → ok`). No errors from
`config_secrets.py`.

**strategy-tester** — same pattern (cold reinstall, `cryptography-49.0.0` installed), same
transient early healthcheck failures, settled to `healthy`, `RestartCount: 0`. Startup log clean:
`tester schema verified`, `Vendored checksums verified OK (5 files)`, no errors.

**social-listener** — lighter deps, faster build. Clean Telegram connect, backfill complete (50
messages, all already-seen so no-op), catchup loop wired in, `RestartCount: 0`, no errors.

**dashboard-ui** — `tsc` passed (`✓ built in 49.68s`), live asset `index-BYo56Tfl.js`. Bundle
sanity-checked for the new Settings page pieces:
```
LLM Provider Keys: 1        (section present)
health-grid: 1               (fetch call present)
llm-keys: 1                  (fetch call present)
active_platform: 1            (correctly still present — see note below)
Exchange Credentials: 0       (correctly removed)
TradingView strategies: 1    (webhook copy re-scoped)
```

**Correction to the roadmap note**: `active_platform` still appears in the bundle because
`Dashboard.tsx`/`PlatformSelector.tsx` (the old landing page, orphaned at `/dashboard` since Tree
replaced it) still has a live toggle for it — only the *Settings* page's copy was removed, not
that one. `docs/ROADMAP.md`'s Deferred Backlog entry was corrected to say this precisely rather
than claim the UI was fully removed.

---

## Roadmap additions (per explicit user request)

Two "why does this exist" bullets added to `docs/ROADMAP.md`'s Deferred Backlog, both backed by a
full-repo grep showing zero runtime reads:
- `config.max_order_size_btc` / `max_order_size_eth` — seeded, never read by any service, never
  shown in any UI.
- `active_platform` — its own read/write endpoints are live, but nothing else consults it for
  actual webhook routing; the `platform` field on strategies is explicitly commented
  `"legacy, kept for compat"` in `order-generator/app/strategies/base.py`.

Both flagged for investigation-then-drop, not dropped in this session.

## Definition of Done

- [x] Tree page: add button, grouped filters/sort, tiered "Last Change" sort, AI pill, visible
      symbol pill, dropdown-clipping fix, bar-item sizing fix.
- [x] Add/Edit strategy → navigates to `/tree` on success.
- [x] AI Signal Log filters match the Tree page's dropdown pattern.
- [x] Settings page rebuilt per agreed scope (risk guards explicitly excluded per user request).
- [x] LLM provider keys editable via UI, encrypted at rest, full round-trip verified live then
      cleaned up.
- [x] All 5 touched services (dashboard-api, dashboard-ui, ai-signal-generator, strategy-tester,
      social-listener) redeployed, healthy, zero restarts, no errors in startup logs.
- [x] Roadmap notes added for the two dead-config findings, with corrected wording after the
      `active_platform` bundle check caught an inaccuracy.
