# Prompt 3b-1: Pure Bracket Exit Calculator — Self-Test Output

## Module created
`signal-engine/app/exits.py` — pure feed-agnostic `BracketState` class with `update(high, low, price, rsi)`.

## Self-test run
```
docker compose exec signal-engine python -m app.exits
```

## Output (all cases passed)

```
Case 1: TP1 at 100.5
  [PASS] legs  EXPECT [{'exit_reason': 'tp1', 'size_pct': 50.0}]  GOT [{'exit_reason': 'tp1', 'size_pct': 50.0}]
  [PASS] protective_stop after TP1  EXPECT 100.1  GOT 100.1
Case 2: TP2 at 101.0 (continuing case 1)
  [PASS] legs  EXPECT [{'exit_reason': 'tp2', 'size_pct': 50.0}]  GOT [{'exit_reason': 'tp2', 'size_pct': 50.0}]
  [PASS] closed  EXPECT True  GOT True
Case 3: stop at 99.3
  [PASS] legs  EXPECT [{'exit_reason': 'stop', 'size_pct': 100.0}]  GOT [{'exit_reason': 'stop', 'size_pct': 100.0}]
Case 4: TP1 then be_stop
  [PASS] update-1 legs  EXPECT [{'exit_reason': 'tp1', 'size_pct': 50.0}]  GOT [{'exit_reason': 'tp1', 'size_pct': 50.0}]
  [PASS] update-2 legs  EXPECT [{'exit_reason': 'be_stop', 'size_pct': 50.0}]  GOT [{'exit_reason': 'be_stop', 'size_pct': 50.0}]
  [PASS] closed  EXPECT True  GOT True
Case 5: arm then trail 100%
  [PASS] update-1 (arm only)  EXPECT []  GOT []
  [PASS] update-2 legs  EXPECT [{'exit_reason': 'trail', 'size_pct': 100.0}]  GOT [{'exit_reason': 'trail', 'size_pct': 100.0}]
Case 6: RSI tighten then stop
  [PASS] update-1 (tighten only)  EXPECT []  GOT []
  [PASS] protective after tighten  EXPECT 99.65  GOT 99.65
  [PASS] update-2 legs  EXPECT [{'exit_reason': 'stop', 'size_pct': 100.0}]  GOT [{'exit_reason': 'stop', 'size_pct': 100.0}]
Case 7: stop-before-target
  [PASS] legs  EXPECT [{'exit_reason': 'stop', 'size_pct': 100.0}]  GOT [{'exit_reason': 'stop', 'size_pct': 100.0}]
Case 8: SHORT TP1 at 99.5
  [PASS] legs  EXPECT [{'exit_reason': 'tp1', 'size_pct': 50.0}]  GOT [{'exit_reason': 'tp1', 'size_pct': 50.0}]
  [PASS] protective_stop (be) after TP1  EXPECT 99.9  GOT 99.9

ALL CASES PASSED
```

## Key design notes

**Case 4 (be_stop naming):** After TP1 fires, peak=100.5 → trailing stop=100.2985, which is above be=100.1. The trailing level is binding, but since the price (low=100.05) also breached the protective stop (be=100.1), the `protective_breached` flag triggers, and `tp1_done=True` → reason = `"be_stop"`. Rule: if price falls through the protective stop level, it's always `stop`/`be_stop`; `trail` only fires when the trailing stop catches the price above the protective level.

**Case 7 (stop-before-target):** high=100.6 arms the trail and clears TP1, but low=99.3 also breaches the protective stop (99.3). Stop check runs before TP1 check; protective_breached → `"stop"`. TP1 never fires.

**Case 5 (trail 100%):** peak=101 → trailing=100.697; low=100.69 < 100.697 but > protective (99.3). Trail catches it above protective → `"trail"`, full 100% since TP1 never hit.
