"""
Pure bracket-exit calculator. No Redis, no DB, no engine imports.

Usage:
    state = BracketState(bracket_spec, entry_price)
    legs  = state.update(high, low, price, rsi)  # list[dict] of fired exits
"""
from __future__ import annotations


def _levels(spec: dict, entry: float) -> dict:
    d = int(spec["direction"])
    return {
        "d":           d,
        "tp1":         entry * (1 + d * spec["tp1_offset_pct"]  / 100),
        "tp2":         entry * (1 + d * spec["tp2_offset_pct"]  / 100),
        "be":          entry * (1 + d * spec["be_offset_pct"]   / 100),
        "trail_arm":   entry * (1 + d * spec["trail_arm_pct"]   / 100),
        "stop0":       entry * (1 - d * spec["stop_offset_pct"] / 100),
        # tighten = half the stop offset, so it's closer to entry than the full stop
        "tighten":     entry * (1 - d * spec["stop_offset_pct"] / 200),
        "tp1_size":    float(spec["tp1_size_pct"]),
        "trail_pct":   spec["trail_pct"] / 100,
        "rsi_long":    spec.get("condition_modify_rsi_long",  75),
        "rsi_short":   spec.get("condition_modify_rsi_short", 25),
    }


class BracketState:
    """
    One open bracket position.

    Construct once per fill; call update() for each price bar.
    update() returns a list of exit legs fired on that bar, each:
        {"exit_reason": str, "size_pct": float}
    exit_reason values: "tp1", "tp2", "stop", "be_stop", "trail"
    """

    def __init__(self, spec: dict, entry: float):
        lv = _levels(spec, entry)
        self._d          = lv["d"]
        self._tp1        = lv["tp1"]
        self._tp2        = lv["tp2"]
        self._be         = lv["be"]
        self._trail_arm  = lv["trail_arm"]
        self._trail_pct  = lv["trail_pct"]
        self._tp1_size   = lv["tp1_size"]
        self._tighten    = lv["tighten"]
        self._rsi_long   = lv["rsi_long"]
        self._rsi_short  = lv["rsi_short"]

        self._protective = lv["stop0"]
        self._peak       = entry
        self._remaining  = 100.0

        self._armed     = False
        self._tp1_done  = False
        self._tightened = False
        self._closed    = False

    @property
    def protective_stop(self) -> float:
        return self._protective

    @property
    def closed(self) -> bool:
        return self._closed

    def update(
        self,
        high: float,
        low: float,
        price: float | None = None,
        rsi: float | None = None,
    ) -> list[dict]:
        if self._closed:
            return []

        d    = self._d
        legs: list[dict] = []

        # 2. Update peak (highest high for long / lowest low for short)
        if d == 1:
            self._peak = max(self._peak, high)
        else:
            self._peak = min(self._peak, low)

        # 3. Condition-modify: tighten stop on RSI extreme (only before TP1)
        if rsi is not None and not self._tp1_done and not self._tightened:
            if (d == 1 and rsi > self._rsi_long) or (d == -1 and rsi < self._rsi_short):
                self._protective = self._tighten
                self._tightened  = True

        # 4. Arm trailing stop (one-way latch)
        if (d == 1 and high >= self._trail_arm) or (d == -1 and low <= self._trail_arm):
            self._armed = True

        # 5. Effective stop
        trail_level = self._peak * (1 - d * self._trail_pct)
        if self._armed:
            if d == 1:
                effective     = max(self._protective, trail_level)
                trail_binding = trail_level > self._protective
            else:
                effective     = min(self._protective, trail_level)
                trail_binding = trail_level < self._protective
        else:
            effective     = self._protective
            trail_binding = False

        # 6. Stop check — before targets
        stop_hit = (d == 1 and low <= effective) or (d == -1 and high >= effective)
        if stop_hit:
            # Reason: "trail" only when the trailing stop is MORE aggressive AND price
            # didn't breach the protective level itself.
            protective_breached = (d == 1 and low <= self._protective) or \
                                  (d == -1 and high >= self._protective)
            if protective_breached:
                reason = "be_stop" if self._tp1_done else "stop"
            else:
                reason = "trail"
            legs.append({"exit_reason": reason, "size_pct": self._remaining})
            self._closed = True
            return legs

        # 7. TP1
        tp1_hit = (d == 1 and high >= self._tp1) or (d == -1 and low <= self._tp1)
        if not self._tp1_done and tp1_hit:
            legs.append({"exit_reason": "tp1", "size_pct": self._tp1_size})
            self._remaining -= self._tp1_size
            self._tp1_done   = True
            self._protective = self._be

        # 8. TP2 (may fire same update as TP1 if high clears both)
        tp2_hit = (d == 1 and high >= self._tp2) or (d == -1 and low <= self._tp2)
        if self._tp1_done and tp2_hit:
            legs.append({"exit_reason": "tp2", "size_pct": self._remaining})
            self._closed = True

        return legs


# ---------------------------------------------------------------------------
# Deterministic self-test — run with: python -m app.exits
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    LONG_SPEC = {
        "tp1_offset_pct": 0.5,  "tp1_size_pct": 50,
        "tp2_offset_pct": 1.0,  "tp2_size_pct": 50,
        "stop_offset_pct": 0.7,
        "trail_arm_pct":   0.4,  "trail_pct":    0.3,
        "be_offset_pct":   0.1,
        "condition_modify_rsi_long":  75,
        "condition_modify_rsi_short": 25,
        "direction": 1,
    }
    SHORT_SPEC = {**LONG_SPEC, "direction": -1}

    ENTRY = 100.0
    failures = 0

    def check(case: str, got, expect, label=""):
        global failures
        ok = got == expect
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {label}  EXPECT {expect!r}  GOT {got!r}")
        if not ok:
            failures += 1

    def check_val(case: str, got, expect, label=""):
        global failures
        ok = abs(got - expect) < 1e-9
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {label}  EXPECT {expect}  GOT {got}")
        if not ok:
            failures += 1

    # ------------------------------------------------------------------
    # Case 1: TP1 fires; protective stop moves to BE (100.1)
    # ------------------------------------------------------------------
    print("Case 1: TP1 at 100.5")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=100.5, low=100.5)
    check("1", got, [{"exit_reason": "tp1", "size_pct": 50.0}], "legs")
    check_val("1", s.protective_stop, 100.1, "protective_stop after TP1")

    # ------------------------------------------------------------------
    # Case 2: continuing from case 1 — TP2 at 101.0
    # ------------------------------------------------------------------
    print("Case 2: TP2 at 101.0 (continuing case 1)")
    got = s.update(high=101.0, low=101.0)
    check("2", got, [{"exit_reason": "tp2", "size_pct": 50.0}], "legs")
    check("2", s.closed, True, "closed")

    # ------------------------------------------------------------------
    # Case 3: fresh long — stop fires at 99.3
    # ------------------------------------------------------------------
    print("Case 3: stop at 99.3")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=99.3, low=99.3)
    check("3", got, [{"exit_reason": "stop", "size_pct": 100.0}], "legs")

    # ------------------------------------------------------------------
    # Case 4: TP1, then be_stop
    # ------------------------------------------------------------------
    print("Case 4: TP1 then be_stop")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=100.5, low=100.5)
    check("4", got, [{"exit_reason": "tp1", "size_pct": 50.0}], "update-1 legs")
    got = s.update(high=100.05, low=100.05)
    check("4", got, [{"exit_reason": "be_stop", "size_pct": 50.0}], "update-2 legs")
    check("4", s.closed, True, "closed")

    # ------------------------------------------------------------------
    # Case 5: arm, then trail fires 100% (no TP1 ever hit)
    # peak=101 → trailing=101*0.997=100.697; low=100.69 ≤ 100.697 → trail
    # ------------------------------------------------------------------
    print("Case 5: arm then trail 100%")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=100.4, low=100.4)
    check("5", got, [], "update-1 (arm only)")
    got = s.update(high=101.0, low=100.69)
    check("5", got, [{"exit_reason": "trail", "size_pct": 100.0}], "update-2 legs")

    # ------------------------------------------------------------------
    # Case 6: RSI tighten to 99.65, then stop
    # ------------------------------------------------------------------
    print("Case 6: RSI tighten then stop")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=100.0, low=100.0, rsi=78.0)
    check("6", got, [], "update-1 (tighten only)")
    check_val("6", s.protective_stop, 99.65, "protective after tighten")
    got = s.update(high=99.6, low=99.6)
    check("6", got, [{"exit_reason": "stop", "size_pct": 100.0}], "update-2 legs")

    # ------------------------------------------------------------------
    # Case 7: stop-before-target — high=100.6 clears TP1 but low=99.3 hits stop
    # ------------------------------------------------------------------
    print("Case 7: stop-before-target")
    s = BracketState(LONG_SPEC, ENTRY)
    got = s.update(high=100.6, low=99.3)
    check("7", got, [{"exit_reason": "stop", "size_pct": 100.0}], "legs")

    # ------------------------------------------------------------------
    # Case 8: SHORT — TP1 at 99.5; BE moves to 99.9
    # ------------------------------------------------------------------
    print("Case 8: SHORT TP1 at 99.5")
    s = BracketState(SHORT_SPEC, ENTRY)
    got = s.update(high=99.5, low=99.5)
    check("8", got, [{"exit_reason": "tp1", "size_pct": 50.0}], "legs")
    check_val("8", s.protective_stop, 99.9, "protective_stop (be) after TP1")

    # ------------------------------------------------------------------
    print()
    if failures == 0:
        print("ALL CASES PASSED")
    else:
        print(f"{failures} CASE(S) FAILED")
        sys.exit(1)
