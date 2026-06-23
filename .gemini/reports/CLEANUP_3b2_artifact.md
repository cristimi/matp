# Cleanup: remove stale 3b-2 artifact exit rows

## What was deleted and why

Two exit rows were written by the 3b-2 near-tick loop when the engine recovered a short
position on restart — before 3b-3's catch-up replay existed. The near-tick loop fired
immediately at the current market price instead of the historical exit price, so `tp1`
and `tp2` landed at the restart timestamp (2026-06-23 16:21) with a stale price (62563.60).
With 3b-3 in place this will not recur: catch-up closes any recovered bracket at the real
historical minute before live near-tick monitoring begins.

---

## Step 1 — backup

```
shadow_signals_backup_20260623_203644.sql   36864 bytes
```

File retained on host; not committed to git.

---

## Step 2 — before rows (confirmed exactly 2)

```
  id  |   signal    | exit_reason | size_pct |    signal_bar_time     | bar_close_price
------+-------------+-------------+----------+------------------------+---------------------------
 2566 | close_short | tp1         |       50 | 2026-06-23 16:21:00+00 | 62563.5999999999985...
 2567 | close_short | tp2         |       50 | 2026-06-23 16:21:00+00 | 62563.5999999999985...
(2 rows)
```

Note: `bar_close_price = 62563.60` equality fails at double precision; predicate was
changed to `id IN (2566, 2567) AND strategy_id='tv_test_harness' AND exit_reason IN ('tp1','tp2')`
— the tightest possible pin, verified to match exactly those 2 rows.

---

## Step 3 — guarded delete

```sql
BEGIN;
DO $$ ... IF n <> 2 THEN RAISE EXCEPTION ... END IF; END $$;
DELETE FROM public.shadow_signals WHERE id IN (2566, 2567) ...;
COMMIT;
```

Output:
```
BEGIN
DO
DELETE 2
COMMIT
```

---

## Step 4 — after rows

```
   signal    | exit_reason | size_pct |    signal_bar_time     | bar_close_price
-------------+-------------+----------+------------------------+-----------------
 close_short | trail       |      100 | 2026-06-22 22:28:00+00 | 64038.80
(1 row)
```

Only the correct `trail` exit (recorded by catch-up at its real historical minute) remains.
Entry rows (`exit_reason IS NULL`): **91 rows — untouched**.
