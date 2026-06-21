# Report: Replace branch/PR policy with work-on-main workflow note

## STEP 0 — Get on main, latest

```
$ git fetch -q origin
$ git checkout -q main
$ git pull -q --ff-only origin main
On: main  (expect: main)
```

## STEP 1 — Confirm old sections present

```
$ grep -n "^## Branch policy" CLAUDE.md
87:## Branch policy

$ grep -n "^## Reports — write last, then push" CLAUDE.md
123:## Reports — write last, then push
```

Both old sections confirmed at lines 87 and 123.

## STEP 3 — Verify replacement

```
$ grep -n "^## How work lands" CLAUDE.md
87:## How work lands

$ grep -c "^## Branch policy" CLAUDE.md
0

$ grep -c "Standard branch bootstrap" CLAUDE.md
0

$ grep -c "pull request\|pull requests" CLAUDE.md
1
```

Note: the single remaining "pull request" occurrence is inside the new `## How work lands`
section itself, in the sentence "do **not** create side branches or pull requests" — correct
usage, not a stale policy reference.

```
$ tail -20 CLAUDE.md

Routine work goes straight to `main` — the live copy. For ordinary changes do **not**
create side branches or pull requests: make the change on `main`, verify it, save it.

Finish every job in this exact order:

1. Make the change and verify it with real command output (not "looks fine").
2. Write a report to `.gemini/reports/<NAME>.md` containing the actual pasted output that
   proves it worked.
3. Save everything online **last**: `git add -A && git commit -m "..." && git push`.
   Never push before the report is written — the report must reach `origin` so the work can
   be reviewed. If you pushed earlier in the session, push again after the report.

The only things that get their own separate branch are large features built across many
sessions — currently `feat/signal-engine` and `feat/social-listener`. Unfinished feature
code must not sit on `main`. When such a feature is finished and verified, fold it into
`main` and remove its branch.

To save a snapshot of a moment in time, use a tag (`git tag <name>`), never a `backup/...`
branch.
```

All checks pass.

## Commit

```
94a18d1 docs(claude.md): replace branch/PR policy with simple work-on-main workflow
```
