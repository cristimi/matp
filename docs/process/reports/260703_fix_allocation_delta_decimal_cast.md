# Fix: allocation_delta rejects decimal deposits/withdrawals

## Problem

`PUT /strategies/:id` in `dashboard-api/src/routes/strategies.ts` rejected any
non-integer `allocation_delta` (e.g. a $2.50 deposit or withdrawal) with:

```
invalid input syntax for type integer: "-2.5"
```

Root cause: in the UPDATE query, `allocation_delta` was applied via
`COALESCE($10, 0)` to three `numeric` columns (`capital_allocation`,
`initial_allocation`, `allocation_peak`). The bare literal `0` is an integer,
so Postgres inferred parameter `$10` as `integer` and rejected any decimal
value at the bind stage.

## Fix

Cast the parameter explicitly to `numeric` in all three occurrences:
`COALESCE($10, 0)` → `COALESCE($10::numeric, 0)`.

## Diff

```diff
--- a/dashboard-api/src/routes/strategies.ts
+++ b/dashboard-api/src/routes/strategies.ts
@@ -700,9 +700,9 @@ router.put('/:id', async (req: Request, res: Response) => {
          allow_quote_variants       = COALESCE($6, allow_quote_variants),
          allow_cross_charting       = COALESCE($7, allow_cross_charting),
          max_leverage               = COALESCE($8, max_leverage),
-         capital_allocation         = capital_allocation + COALESCE($10, 0),
-         initial_allocation         = initial_allocation + COALESCE($10, 0),
-         allocation_peak            = allocation_peak    + COALESCE($10, 0),
+         capital_allocation         = capital_allocation + COALESCE($10::numeric, 0),
+         initial_allocation         = initial_allocation + COALESCE($10::numeric, 0),
+         allocation_peak            = allocation_peak    + COALESCE($10::numeric, 0),
          margin_per_trade           = COALESCE($11, margin_per_trade),
          max_drawdown_pct           = COALESCE($12, max_drawdown_pct),
          account_id                 = COALESCE($13, account_id),
```

Single-file change, three lines. No DB migration required — columns were
already `numeric`.

## Push confirmation

```
$ git add dashboard-api/src/routes/strategies.ts
$ git commit -m "fix(strategies): cast allocation_delta param to numeric so decimal deposits/withdrawals work"
[main 1ce261c] fix(strategies): cast allocation_delta param to numeric so decimal deposits/withdrawals work
 1 file changed, 3 insertions(+), 3 deletions(-)
$ git push origin main
To github.com:cristimi/matp.git
   52fcf61..1ce261c  main -> main

$ git log --oneline -1 origin/main
1ce261c fix(strategies): cast allocation_delta param to numeric so decimal deposits/withdrawals work

$ git status
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
```

## Deploy confirmation

```
$ ./scripts/redeploy.sh dashboard-api
...
▶ Verifying …
NAME                   IMAGE                COMMAND                  SERVICE         CREATED          STATUS                            PORTS
matp-dashboard-api-1   matp-dashboard-api   "docker-entrypoint.s…"   dashboard-api   18 seconds ago   Up 4 seconds (health: starting)   8003/tcp
✓ dashboard-api redeployed.
```

Compiled bundle inside the running container contains the cast:

```
$ docker exec matp-dashboard-api-1 grep -n "10::numeric" /app/dist/routes/strategies.js
621:         capital_allocation         = capital_allocation + COALESCE($10::numeric, 0),
622:         initial_allocation         = initial_allocation + COALESCE($10::numeric, 0),
623:         allocation_peak            = allocation_peak    + COALESCE($10::numeric, 0),
```

Health check:

```
$ curl -sf http://localhost:8003/health
{"status":"ok","service":"dashboard-api"}
```

## Functional verification

### Withdrawal: -2.5 on `matp-test-harness-fe19` (allocation 500, margin_per_trade 300)

Before:

```
 capital_allocation | initial_allocation | allocation_peak
--------------------+---------------------+-----------------
                500 |                 500 |             500
```

Request:

```
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PUT http://localhost/api/dashboard/strategies/matp-test-harness-fe19 \
    -H "Content-Type: application/json" -d '{"allocation_delta": -2.5}'
{"id":"matp-test-harness-fe19","name":"MATP Test Harness","symbol":"BTC-USDT","interval":"1h","enabled":false,
 "default_leverage":10,"margin_mode":"isolated","allow_quote_variants":false,"allow_cross_charting":false,
 "account_id":"blofin-blofin-demo-v5vr","capital_allocation":"497.5","initial_allocation":"497.5",
 "allocation_peak":"497.5","margin_per_trade":"300","max_drawdown_pct":"75","pnl_total":"0",
 "allocation_delta_applied":-2.5}
HTTP_STATUS:200
```

After:

```
 capital_allocation | initial_allocation | allocation_peak
--------------------+---------------------+-----------------
              497.5 |               497.5 |           497.5
```

All three columns dropped by exactly 2.50.

### Deposit: +2.5

`matp-test-harness-fe19` shares an account (`blofin-blofin-demo-v5vr`) with
several other strategies that already fully commit the account's available
balance, so a +2.5 deposit there hit the (unrelated, pre-existing)
insufficient-free-funds business check:

```
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PUT http://localhost/api/dashboard/strategies/matp-test-harness-fe19 \
    -H "Content-Type: application/json" -d '{"allocation_delta": 2.5}'
{"error":"Insufficient free funds on account: $500.00 committed after deposit, $319.96 available ($800.00 already allocated of $1119.96 total)."}
HTTP_STATUS:422
```

This is expected/correct behavior of the pre-existing funds check, not a
regression. To verify the positive-decimal path, the same test was run
against `tv-btc-test-hl-94e1` (separate account `hyperliquid-hyperliquid-hqdy`
with headroom):

Before:

```
                   capital_allocation                    | initial_allocation |                     allocation_peak
-----------------------------------------------------------+---------------------+-----------------------------------------------------------
 132.481960000000003496722911222605034708976745605468750 |               200 | 155.365130000000003285265393060399219393730163574218750
```

Request:

```
$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PUT http://localhost/api/dashboard/strategies/tv-btc-test-hl-94e1 \
    -H "Content-Type: application/json" -d '{"allocation_delta": 2.5}'
{"id":"tv-btc-test-hl-94e1","name":"TV BTC Test HL","symbol":"BTC-USDT","interval":"1h","enabled":true,
 "default_leverage":10,"margin_mode":"isolated","allow_quote_variants":true,"allow_cross_charting":false,
 "account_id":"hyperliquid-hyperliquid-hqdy",
 "capital_allocation":"134.981960000000003496722911222605034708976745605468750","initial_allocation":"202.5",
 "allocation_peak":"157.865130000000003285265393060399219393730163574218750","margin_per_trade":"50",
 "max_drawdown_pct":"85","pnl_total":"-67.518039999999996503277088777394965291023254394531250",
 "allocation_delta_applied":2.5}
HTTP_STATUS:200
```

After:

```
                   capital_allocation                    | initial_allocation |                     allocation_peak
-----------------------------------------------------------+---------------------+-----------------------------------------------------------
 134.981960000000003496722911222605034708976745605468750 |             202.5 | 157.865130000000003285265393060399219393730163574218750
```

All three columns rose by exactly 2.50, confirming decimals work in the
positive direction.

### Cleanup

Both test strategies were restored to their pre-test values afterward
(`tv-btc-test-hl-94e1` via a `-2.5` API call; `matp-test-harness-fe19` via a
direct SQL `UPDATE` back to 500, since the API deposit path was blocked by
the funds check described above).

## Conclusion

Fix verified end-to-end: decimal `allocation_delta` values now bind and
apply correctly in both directions. Commit `1ce261c` is on `origin/main`,
deployed, and confirmed healthy.
