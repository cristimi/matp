# Social Listener Phase 1 — Live Bring-Up Report

**Date:** 2026-06-25  
**Branch:** feat/social-listener  
**Channel:** @AstronomerZero  
**Executor:** claude-sonnet-4-6

---

## Step 2 — Migration 025 result

```
NOTICE:  relation "social_signal_log" already exists, skipping
CREATE TABLE
NOTICE:  relation "uq_social_signal_source_msg" already exists, skipping
CREATE INDEX
NOTICE:  relation "ix_social_signal_actionable" already exists, skipping
CREATE INDEX
Migration 025 verified OK
DO
```

_(NOTICEs indicate idempotent re-run; no ERRORs.)_

---

## Step 4 — Container status

```
NAME                     IMAGE                  COMMAND                SERVICE           CREATED          STATUS        PORTS
matp-social-listener-1   matp-social-listener   "python -m app.main"   social-listener   15 seconds ago   Up 1 second
```

---

## Step 5 — Connection / backfill log

```
social-listener-1  | 2026-06-25 11:07:06,694 INFO social-listener Telegram connected as 8833405539
social-listener-1  | 2026-06-25 11:08:53,544 INFO social-listener msg 9542 [ACTIONABLE] FLIP BTC ref=67000.0 conf=0.90
social-listener-1  | 2026-06-25 11:09:02,441 INFO social-listener msg 9544 [ACTIONABLE] FLIP BTC ref=67000.0 conf=0.72
social-listener-1  | 2026-06-25 11:09:31,774 INFO social-listener msg 9552 [ACTIONABLE] OPEN BTC ref=62600.0 conf=0.72
social-listener-1  | 2026-06-25 11:10:07,977 INFO social-listener msg 9560 [ACTIONABLE] OPEN - ref=None conf=0.45
social-listener-1  | 2026-06-25 11:10:15,504 INFO social-listener msg 9562 [ACTIONABLE] OPEN BTC ref=None conf=0.80
social-listener-1  | 2026-06-25 11:10:27,389 INFO social-listener Backfill complete (50 messages)
```

---

## Step 5 — Actionable vs noise

```
 is_actionable | count 
---------------+-------
 f             |    45
 t             |     5
(2 rows)
```

---

## Step 5 — Parsed actionable rows

```
 channel_msg_id | action_type | asset | direction | reference_price | confidence |                           snippet                            
----------------+-------------+-------+-----------+-----------------+------------+--------------------------------------------------------------
           9562 | OPEN        | BTC   | LONG      |                 |       0.80 | Astronomer (@astronomer_zero) on X / $btc / Green zone reached
           9560 | OPEN        |       | LONG      |                 |       0.45 | But I longed again, with more confluences present, a better 
           9552 | OPEN        | BTC   | LONG      |           62600 |       0.72 | ➡️Entry 62.6k / ➡️Risk off the trade: feel free to thank me
           9544 | FLIP        | BTC   | LONG      |           67000 |       0.72 | Astronomer (@astronomer_zero) on X / $btc longs / Good reversal
           9542 | FLIP        | BTC   | LONG      |           67000 |       0.90 | Astronomer (@astronomer_zero) on X / $btc / Back on the bright
(5 rows)
```

---

## Summary

The backfill processed 50 messages from @AstronomerZero, yielding **5 actionable / 45 noise** (10% actionable rate). All 5 actionable signals are BTC LONG calls, which is consistent with the channel's recent bias: msgs 9542 and 9544 are FLIP signals targeting the $67 000 reversal zone (conf 0.90 and 0.72), msg 9552 is an OPEN with a clean entry at 62.6k and a reference price (conf 0.72), and msgs 9560 and 9562 are OPEN signals extracted from X-link previews with direction but no reference price. One likely mis-classification: **msg 9560** ("But I longed again, with more confluences present…") is flagged actionable at conf 0.45 but carries no asset field — it reads more like a commentary post on a prior trade than a new signal call, and would be filtered out downstream by any whitelist or confidence threshold above 0.5. No obviously missed actionable signals were visible in the noise set.
