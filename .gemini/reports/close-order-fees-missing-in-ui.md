# Close orders on AI positions showed no fee in the UI

## Symptom

In the Strategy Tree order timeline (L3 "Key details") and the L4 "full info" panel, the
**entry** order of an AI position showed a `Fee` value, but every **close** / partial-close
order showed `—`.

## Root cause

`dashboard-api` read the fee from the wrong table.

`GET /positions/:id/orders` (`dashboard-api/src/routes/positions.ts`) selected:

```sql
oel.exchange_fee AS fee
...
LEFT JOIN order_execution_log oel
  ON oel.exchange_order_id = o.exchange_order_id
```

`order_execution_log` (OEL) rows are written at **placement**, which only happens for
opening orders. Close orders never get an OEL row, so the LEFT JOIN yielded NULL and the
UI rendered `—`. The fee was in the DB the whole time, on `orders.exchange_fee`.

Proof — the same position, entry vs. its two closes:

```
                  id                  | is_entry | order_fee  | has_oel |  oel_fee
--------------------------------------+----------+------------+---------+-----------
 9fbdb91b-62d4-4522-912c-98336bfa4af0 | t        | 0.06107616 | t       | 0.0610761...
 1140105b-5961-4cc3-a5a5-4560c9533130 | f        |  0.0033912 | f       |
 97b44b44-60fa-42f1-9853-2bdffcfe07d0 | f        |  0.0577218 | f       |
```

`has_oel = f` on both closes — the join simply had nothing to match. Across **all** close
orders in the DB, not one has an OEL row.

The UI (`dashboard-ui/src/pages/StrategyTree.tsx:1151`) was correct; it faithfully rendered
the `null` the API sent. No UI change was needed.

## Fix

Read `orders.exchange_fee` first, fall back to OEL. Two one-line query changes:

- `dashboard-api/src/routes/positions.ts` — `COALESCE(o.exchange_fee, oel.exchange_fee) AS fee`
- `dashboard-api/src/routes/orders.ts` — `COALESCE(o.exchange_fee, oel.exchange_fee) AS exchange_fee`

Comments in both routes updated to explain why closes have no OEL row.

### Why COALESCE and not just `o.exchange_fee`

Neither column is a strict superset:

```
 oel_only | order_only | both | total
----------+------------+------+-------
       69 |        126 |   85 |   460
```

The 69 OEL-only rows are all legacy **opens** predating `orders.exchange_fee` being
populated — and all 69 have `exchange_fee = 0`:

```
 exchange_fee | count
--------------+-------
            0 |    69
```

So the fallback adds no misleading values; the primary read is what fixes the bug.

The join is also safe to keep — no `exchange_order_id` appears more than once in OEL
(`GROUP BY ... HAVING count(*) > 1` returns 0 rows), so it cannot duplicate timeline rows.

## Verification (against the running container)

Redeployed with `./scripts/redeploy.sh dashboard-api`.

Shipped image contains the change:

```
$ docker compose exec -T dashboard-api grep -o "COALESCE(o.exchange_fee, oel.exchange_fee)[^,]*" \
    /app/dist/routes/positions.js /app/dist/routes/orders.js
/app/dist/routes/positions.js:COALESCE(o.exchange_fee, oel.exchange_fee) AS fee
/app/dist/routes/orders.js:COALESCE(o.exchange_fee, oel.exchange_fee) AS exchange_fee
```

Service healthy:

```
$ docker compose exec -T nginx wget -qO- http://dashboard-api:8003/health
{"status":"ok","service":"dashboard-api"}

$ docker compose ps dashboard-api
NAME                   IMAGE                SERVICE         STATUS
matp-dashboard-api-1   matp-dashboard-api   dashboard-api   Up 18 seconds (healthy)
```

L3 timeline — all three orders now carry a fee (both closes previously `null`):

```
$ docker compose exec -T nginx wget -qO- \
    http://dashboard-api:8003/positions/9fe0f6bc-b519-4f89-98d5-a6aabb303795/orders
[{"id":"9fbdb91b-...","type":"entry",        ...,"key":{"avg_fill":565.52,"realized":0,      "fee":0.06107616}},
 {"id":"1140105b-...","type":"partial-close", ...,"key":{"avg_fill":565.2, "realized":-0.0032,"fee":0.0033912}},
 {"id":"97b44b44-...","type":"close",         ...,"key":{"avg_fill":565.9, "realized":0.0646, "fee":0.0577218}}]
```

L4 detail on an AI close order:

```
$ docker compose exec -T nginx wget -qO- \
    http://dashboard-api:8003/orders/97b44b44-60fa-42f1-9853-2bdffcfe07d0/detail | grep -o '"exchange_fee":[^,]*'
"exchange_fee":0.0577218
```

Coverage across every AI close order in the DB:

```
 ai_close_orders | fee_before_fix | fee_after_fix
-----------------+----------------+---------------
              80 |              0 |            65
```

## Known remaining gap (not fixed — no data to fix it with)

15 of the 80 AI close orders still report no fee, because the fee was never captured for
them at write time; it is absent from `orders` *and* `order_execution_log`. They are all
historical:

```
 status | has_xoid | count |   oldest   |   newest
--------+----------+-------+------------+------------
 filled | f        |     4 | 2026-07-05 | 2026-07-05
 filled | t        |    11 | 2026-06-20 | 2026-07-05
```

Nothing after 2026-07-05 is affected, so the capture path is working; recovering these
would require re-fetching fills from the exchange. Not attempted.
