# MATP Session Resume

You are resuming as the autonomous development assistant for MATP (Modular Automated Trading Platform). A disconnect occurred and you have no memory of the previous session. Your job is to reconstruct the full context from the codebase and environment, then produce a status report and wait for instructions.

Do not start any work. Do not fix anything. Reconstruct first, report second, wait third.

---

## STEP 1 — RECONSTRUCT GIT STATE

Run these in order:

```bash
# Where we are
git branch --show-current
git status

# What was committed recently
git log --oneline -10

# What is uncommitted (staged or unstaged)
git diff --stat HEAD
git diff HEAD
```

From this, determine:
- Were there recent commits since the last doc update?
- Are there uncommitted changes? If yes, which files and how significant?
- Is there a work-in-progress that was interrupted?

---

## STEP 2 — INFER INTENT FROM UNCOMMITTED CHANGES

If `git diff HEAD` shows uncommitted changes:

1. Read the last entry in `CHANGELOG.md` to understand what was being worked on
2. Read the changed files in full
3. Cross-reference with `ACTION_PLAN.md` — find the task most likely in progress
4. Form a hypothesis: **"It looks like work was in progress on [X], specifically [Y]"**

If there are no uncommitted changes:
- Note the last commit message and timestamp
- Check if it looks like a clean stopping point or an interrupted mid-commit

---

## STEP 3 — CHECK CONTAINER STATE

```bash
# Overall status
docker compose ps

# Volume state (confirms DB data is persisted)
docker volume ls | grep matp

# Quick health checks
docker compose exec postgres pg_isready -U matp
docker compose exec redis redis-cli ping
curl -sf http://localhost:8001/health
curl -sf http://localhost:8003/health

# Last 20 lines of logs for any unhealthy service
docker compose logs --tail=20 <any service showing non-Up status>
```

Classify each service as: ✅ healthy / ⚠️ degraded / ❌ down

---

## STEP 4 — CHECK FOR INCOMPLETE OPERATIONS

Look for signs of an interrupted task:

```bash
# Any services that exited unexpectedly
docker compose ps --filter status=exited

# Recent build artifacts or temp files
find . -name "*.tmp" -o -name "*.bak" -not -path "*/.git/*" -not -path "*/node_modules/*"

# Any lock files that shouldn't be there
find . -name "package-lock.json.lock" -o -name "*.pyc" -newer CHANGELOG.md -not -path "*/.git/*"

# Check if a previous build was left mid-way
docker compose logs --tail=10 --no-log-prefix 2>&1 | grep -i "build\|error\|exit"
```

---

## STEP 5 — PRODUCE STATUS REPORT

Output exactly this block, filled in:

```
MATP Session Resume — [date + time]
═══════════════════════════════════════════════════════

GIT STATE
  Branch          : [branch name]
  Last commit     : [hash + message + how long ago]
  Uncommitted     : [N files changed — or "clean"]
  Inferred intent : [what was likely in progress — or "none, clean stop"]

CONTAINERS
  nginx           : ✅ / ⚠️ / ❌
  order-listener  : ✅ / ⚠️ / ❌
  order-generator : ✅ / ⚠️ / ❌
  dashboard-api   : ✅ / ⚠️ / ❌
  dashboard-ui    : ✅ / ⚠️ / ❌
  postgres        : ✅ / ⚠️ / ❌
  redis           : ✅ / ⚠️ / ❌

INCOMPLETE OPERATIONS
  [describe anything mid-flight — or "none detected"]

LAST DOCUMENTED PROGRESS
  [one sentence from the last CHANGELOG.md entry]

RECOMMENDED NEXT STEP
  [one concrete action — e.g. "Rebuild order-listener and re-run webhook test"
   or "Continue implementing [X] in [file]" or "Run sync.md — session looks complete"]

⚠️  NEEDS YOUR DECISION
  [anything ambiguous that requires human input — or "none"]
```

---

## STEP 6 — WAIT

After printing the status report, stop. Do not proceed.

State: `[READY — awaiting your instructions]`

Wait for the user to confirm the inferred intent or give a new direction before touching any file or running any service command.
