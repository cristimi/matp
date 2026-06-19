# Report: Dynamic Strategy Allocation — Deferred Backlog Append

## Grep verification (verbatim output)

```
grep -n "Dynamic strategy allocation" docs/ROADMAP.md
63:### Dynamic strategy allocation (realized-PnL-compounding base)

grep -n "realized P&L only" docs/ROADMAP.md
68:that position sizing is computed against compounds with **realized P&L only**:

grep -n "Deferred Backlog" docs/ROADMAP.md
56:## Deferred Backlog
```

All three greps returned a hit. "Dynamic strategy allocation" is at line 63, which is greater than "Deferred Backlog" at line 56 — entry is inside the section. ✓

## Deferred Backlog heading

Exact heading text and level found: `## Deferred Backlog` (level 2, `##`).

## CLAUDE.md pointer

Already existed at line 10:
> Deferred work and design decisions live in `docs/ROADMAP.md` (see its "Deferred Backlog" and "Open Design Questions").

No change made to CLAUDE.md. ✓

## Files modified

```
git status --short
M docs/ROADMAP.md
```

Only `docs/ROADMAP.md` was modified. ✓
