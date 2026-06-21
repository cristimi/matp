# Report: claude-md-workflow-policy

## STEP 0 — Branch bootstrap output

```
NOTE: created chore/claude-md-workflow-policy from origin/main
On branch: chore/claude-md-workflow-policy
BOOTSTRAP OK
```

## STEP 1 — Pre-flight check

```
If `curl http://localhost/` returns the new asset hash but the device shows the old UI, it's
the **browser's** HTTP cache holding a stale `index.html` from before the no-store header
applied. One-time fix per device: clear Chrome "Cached images and files". It will not recur,
because `index.html` is served `no-store`. There is no service worker, so no offline cache.
---
0
```

`grep -c "## Branch policy" CLAUDE.md` → `0` (section absent, safe to append).

## STEP 3 — Verification output

```
87:## Branch policy
123:## Reports — write last, then push
1
CLAUDE.md OK — sections present, fences balanced
```

Both `grep -n` lines hit their expected line numbers. Python check confirms:
- "Snapshots are tags, never branches" present ✓
- "Never push before the report exists" present ✓
- Code fences balanced (even count) ✓

## STEP 2 — Appended diff (`git diff HEAD -- CLAUDE.md`)

```diff
diff --git a/CLAUDE.md b/CLAUDE.md
index 2aaf72e..4348ad4 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -83,3 +83,55 @@ If `curl http://localhost/` returns the new asset hash but the device shows the
 the **browser's** HTTP cache holding a stale `index.html` from before the no-store header
 applied. One-time fix per device: clear Chrome "Cached images and files". It will not recur,
 because `index.html` is served `no-store`. There is no service worker, so no offline cache.
+
+## Branch policy
+
+`main` is the only long-lived branch — trunk-based. Keep the branch list tiny.
+
+- One `feat/<name>` per *active* roadmap workstream. Merge to `main` and **delete** it the
+  moment that item ships — don't keep merged branches "just in case".
+- `fix/<name>` and `chore/<name>` are ephemeral — deleted on merge. The GitHub repo setting
+  *Automatically delete head branches* is ON; leave it on.
+- **Snapshots are tags, never branches.** Use `git tag archive/<name> <sha>` then
+  `git push origin archive/<name>` — never a `backup/*` branch. Tags stay out of the branch
+  list and are immutable by convention.
+- Every executor prompt declares its target up front and runs the bootstrap block below as
+  its first step. **Do not check out or create branches by hand** — the prompt does it.
+
+### Standard branch bootstrap (Step 0 of every prompt)
+
+Each prompt carries a header `TARGET_BRANCH: <branch>` / `BASE_BRANCH: main`, then runs this
+verbatim before any work. It lands on the target branch, creating it from base if needed,
+stashing stray work first so nothing is lost:
+
+```bash
+set -e
+TARGET="<branch>"; BASE="main"
+git fetch -q origin
+if ! (git diff --quiet && git diff --cached --quiet); then
+  git stash push -u -m "auto-bootstrap $(date -u +%FT%TZ)" >/dev/null
+  echo "NOTE: stashed uncommitted changes (recover: git stash list / git stash pop)"
+fi
+if git show-ref --verify --quiet "refs/heads/$TARGET"; then git checkout -q "$TARGET"
+elif git ls-remote --exit-code --heads origin "$TARGET" >/dev/null 2>&1; then git checkout -q -t "origin/$TARGET"
+else git checkout -q -B "$TARGET" "origin/$BASE"; echo "NOTE: created $TARGET from origin/$BASE"; fi
+CUR=$(git branch --show-current); echo "On branch: $CUR"
+[ "$CUR" = "$TARGET" ] || { echo "ABORT: failed to land on $TARGET"; exit 1; }
+echo "BOOTSTRAP OK"
+```
+
+## Reports — write last, then push
+
+Every prompt ends by writing a report to `.gemini/reports/<NAME>.md` containing the actual
+pasted command output that proves the work (builds, greps, verifications) — not a summary.
+
+**The report is the final action, and you must push after writing it.** Order matters:
+
+1. Do the work and verify it.
+2. Write the report file.
+3. `git add -A && git commit` — the report and the change land in the same commit.
+4. `git push`.
+
+Never push before the report exists. The reviewer verifies by re-cloning `origin` and
+reading the committed report; an unpushed report is invisible and forces a manual re-push.
+If you already pushed earlier in the session, push again after committing the report.
```

## Final commit + push (tail appended after push)

```
4bba30f docs(claude.md): add branch policy + report-publish rule
## chore/claude-md-workflow-policy...origin/chore/claude-md-workflow-policy
```
