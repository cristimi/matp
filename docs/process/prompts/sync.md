# MATP Documentation Sync
# Trigger phrase: "update the documentation"
# Run this after every working session to keep all docs in sync with the codebase.

You are the documentation maintainer for MATP (Modular Automated Trading Platform).
Your job is to detect what changed since the last documentation update and sync all
reference documents to reflect the current state of the codebase.

---

## STEP 1 — DETECT WHAT CHANGED

Do not rely on being told what changed. Detect it yourself by running these commands:

```bash
# Establish the baseline: last commit that touched any doc file under prompts/ or docs/
LAST_DOC_COMMIT=$(git log --all --oneline -- "*.md" | head -1 | cut -d' ' -f1)

# Verify the baseline commit exists — if empty, fall back to first commit
if [ -z "$LAST_DOC_COMMIT" ]; then
  LAST_DOC_COMMIT=$(git rev-list --max-parents=0 HEAD)
  echo "WARNING: No previous doc commit found. Using first commit as baseline."
fi

echo "Baseline commit: $LAST_DOC_COMMIT"

# What code commits happened since the baseline
git log --oneline ${LAST_DOC_COMMIT}..HEAD

# Full diff of non-doc changes since baseline
git diff ${LAST_DOC_COMMIT}..HEAD -- . ":(exclude)*.md"

# List of changed files grouped by type
git diff --name-only ${LAST_DOC_COMMIT}..HEAD

# Recent commit messages for context
git log --oneline -10

# Current branch
git branch --show-current

# Any uncommitted changes on top of that
git status --short
git diff HEAD
```

If the above still fails (no git history, detached HEAD, etc.), fall back to:

```bash
# Use CHANGELOG.md modification time as anchor
find . -newer CHANGELOG.md \
  -not -path "*/.git/*" \
  -not -path "*/node_modules/*" \
  -not -name "*.md" \
  -type f | sort
```

Build a mental summary of what actually changed before touching any file.

---

## STEP 2 — READ CURRENT DOCUMENTATION (SCOPED)

Do not read every doc file in full. Read only what is relevant to the changes detected in Step 1:

- Always read: `CHANGELOG.md` (last 50 lines only — for version and format reference)
- If architecture changed: read `docs/MATP.SDD.md` (targeted sections only, not full file)
- If tasks may be complete: read `docs/process/ACTION_PLAN.md` (current phase section only)
- If tests may have run: read `docs/TEST_PLAN.md` (relevant category sections only)
- For any other `.md` files: read only if Step 1 diff touched something they reference

```bash
# List all doc files so you know what exists
find . -name "*.md" \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" | sort

# Read last 50 lines of CHANGELOG for version reference
tail -50 CHANGELOG.md
```

This scoped approach keeps context usage low for long-running projects.

---

## STEP 3 — UPDATE CHANGELOG.md

Determine the next version by reading the current version in `CHANGELOG.md` and incrementing:

- Bug fixes + doc updates only → patch (0.1.0 → 0.1.1)
- New features or DB changes → minor (0.1.1 → 0.2.0)
- Breaking changes → major (0.2.0 → 1.0.0)

Add a new entry at the top. Use today's date. Follow the existing format exactly.
Only include sections that have content (Added / Changed / Fixed / Removed / Tested).

Rules:
- Be specific: name exact files, tables, endpoints, components changed
- "Tested" section: list any test suites run and their pass rate
- Do not pad with vague entries like "various improvements"
- If nothing significant changed, write a minimal entry: "no code changes, documentation sync only"

---

## STEP 4 — UPDATE docs/MATP.SDD.md

This is the architecture reference. Only update it when the architecture actually changed.

**Update when:**
- New database tables or columns added
- New API endpoints added or removed
- New services or components added
- Infrastructure changes (docker-compose, nginx, redis channels)
- Security model changes
- Technology stack changes

**Do not update for:**
- Bug fixes that don't change the interface
- UI styling changes
- Test results
- Prompt files added or changed

When updating:
- Update only the affected sections
- Keep present tense throughout
- Update schema blocks to match current `db/init.sql` or latest migration
- Do not add TODO items or future plans — architecture only

---

## STEP 5 — UPDATE docs/process/ACTION_PLAN.md

Mark tasks complete only when there is clear evidence in the git diff:

| Evidence | Marks complete |
|---|---|
| New migration file applied | DB schema task |
| Tests passing in logs or output files | Test tasks |
| New page/component files created | UI task |
| New API endpoint in routes | Backend task |

Also update:
- "Current Phase" header if phase has advanced
- "Current State" summary paragraph if significant progress was made

Do not mark anything complete without evidence. Do not add new tasks — that requires human decision.

---

## STEP 6 — UPDATE docs/TEST_PLAN.md

Mark tests passed only if evidence exists:
- Log files showing test output
- CI output files
- Test result files (pytest output, jest output)
- Git commit messages explicitly mentioning tests passing

For newly added features detected in Step 1:
- Add corresponding test entries in the appropriate category (Terminal / Browser / AI-Verified) if they don't already exist
- Leave them unchecked — they haven't been run yet

---

## STEP 7 — SCAN REMAINING .md FILES

For each remaining `.md` file not already updated:
- Check only whether it references schema, endpoints, or components that changed in Step 1
- If stale: make the minimum update to correct the factual error
- If current: leave untouched
- Skip `prompts/` folder files entirely — prompt files are not documentation

---

## CONSTRAINTS

- Never rewrite a document wholesale — targeted updates only
- Never invent changes — only document what git diff and file inspection confirm
- Never delete existing content
- Never mark a task or test complete without evidence
- Preserve each document's existing formatting and style
- If git shows no changes since last doc update — write a minimal CHANGELOG entry and leave all other files untouched
- Do not read files in full unnecessarily — scope reads to relevant sections

---

## OUTPUT

Print a concise sync report:

```
MATP Documentation Sync — [date]
═══════════════════════════════════════════════════════
Baseline commit : [hash + message]
Branch          : [branch name]
Uncommitted     : [N files] or "clean"

Changes detected:
  [list of changed files from git diff, grouped by service/type]

Documents updated:
  CHANGELOG.md     — added v[X.Y.Z] entry ([N] items)
  docs/MATP.SDD.md             — [what changed] OR "no changes needed"
  docs/process/ACTION_PLAN.md  — [N] tasks marked complete OR "no changes needed"
  docs/TEST_PLAN.md            — [N] tests marked complete, [N] new entries added OR "no changes needed"
  [other files]    — [what changed] OR "no changes needed"

Nothing to update:
  [files checked but left untouched]

⚠️  Manual attention needed:
  [anything ambiguous, conflicting, or that needs human decision — or "none"]
```
