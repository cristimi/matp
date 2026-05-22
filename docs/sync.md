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
# What changed in git since the last commit that touched any .md file
git log --oneline $(git log --all --oneline -- "*.md" | head -1 | cut -d' ' -f1)..HEAD

# Full diff of all non-doc changes since last doc update
git diff $(git log --all --oneline -- "*.md" | head -1 | cut -d' ' -f1)..HEAD -- . ":(exclude)*.md"

# List all files changed since last doc update
git diff --name-only $(git log --all --oneline -- "*.md" | head -1 | cut -d' ' -f1)..HEAD

# Recent commit messages for context
git log --oneline -20

# Current branch
git branch --show-current
```

If the repo has no git history or the above fails, fall back to:
```bash
# Check file modification times to find recently changed files
find . -newer CHANGELOG.md -not -path "*/.git/*" -not -path "*/node_modules/*" 
  -not -name "*.md" -type f | sort
```

Build a mental summary of what actually changed before touching any file.

---

## STEP 2 — READ ALL CURRENT DOCUMENTATION

Read every doc file in full before making any changes:
```bash
find . -name "*.md" -not -path "*/node_modules/*" -not -path "*/.git/*" | sort
```

Read each file. Understand its current state. Note any sections that are stale
relative to what you found in Step 1.

---

## STEP 3 — UPDATE CHANGELOG.md

Determine the next version number by reading the existing CHANGELOG.md and
incrementing appropriately:
- Bug fixes + doc updates only → patch (0.1.0 → 0.1.1)
- New features or DB changes → minor (0.1.1 → 0.2.0)
- Breaking changes → major (0.2.0 → 1.0.0)

Add a new entry at the top. Use today's date. Follow the existing format exactly.
Only include sections that have content (Added / Changed / Fixed / Removed / Tested).

Rules:
- Be specific: name exact files, tables, endpoints, components changed
- "Tested" section: list any test suites run and their pass rate
- Do not pad with vague entries like "various improvements"
- If nothing significant changed, write a minimal entry and note it

---

## STEP 4 — UPDATE MATP.SDD.md

This is the architecture reference. Only update it when the architecture actually changed.

Things that warrant an SDD update:
- New database tables or columns added
- New API endpoints added or removed
- New services or components added
- Infrastructure changes (docker-compose, nginx, redis channels)
- Security model changes
- Technology stack changes

Things that do NOT warrant an SDD update:
- Bug fixes that don't change the interface
- UI styling changes
- Test results
- Prompt files added

When updating:
- Update only the affected sections
- Keep present tense throughout
- Update schema blocks to match current db/init.sql or latest migration
- Do not add TODO items or future plans — architecture only

---

## STEP 5 — UPDATE ACTION_PLAN.md

Mark any tasks as complete that are evidenced by the git diff.

Evidence rules:
- A new migration file applied → mark that DB task complete
- Tests passing in logs or test output files → mark test tasks complete
- New page/component files created → mark that UI task complete
- New API endpoint in routes → mark that backend task complete

Also update:
- "Current Phase" header if phase has advanced
- "Current State" summary paragraph if significant progress was made

Do not mark anything complete unless there is clear evidence in the codebase.
Do not add new tasks — that requires human decision.

---

## STEP 6 — UPDATE TEST_PLAN.md

Mark tests as passed if evidence exists:
- Log files showing test output
- CI output files
- Test result files (pytest output, jest output)
- Git commit messages mentioning tests passing

For any newly added features detected in Step 1:
- Add corresponding test entries in the appropriate category
  (Terminal / Browser / AI-Verified) if they don't already exist
- Leave them unchecked — they haven't been run yet

---

## STEP 7 — SCAN ALL OTHER .md FILES

For each remaining .md file:
- Check if any content references schema, endpoints, or components that changed
- If stale: make the minimum update to correct the factual error
- If current: leave untouched

---

## CONSTRAINTS

- Never rewrite a document wholesale — targeted updates only
- Never invent changes — only document what git diff and file inspection confirm
- Never delete existing content
- Never mark a task or test complete without evidence
- Preserve each document's existing formatting and style
- If git shows no changes since last doc update — write a minimal CHANGELOG entry
  noting "no code changes, documentation sync only" and leave other files untouched

---

## OUTPUT

Print a concise sync report:

```
MATP Documentation Sync — [date]
═══════════════════════════════════════════════════════

Changes detected:
  [list of changed files from git diff, grouped by type]

Documents updated:
  CHANGELOG.md     — added v[X.Y.Z] entry ([N] items)
  MATP.SDD.md      — [what changed] OR "no changes needed"
  ACTION_PLAN.md   — [N] tasks marked complete OR "no changes needed"
  TEST_PLAN.md     — [N] tests marked complete, [N] new entries added OR "no changes needed"
  [other files]    — [what changed] OR "no changes needed"

Nothing to update:
  [files checked but left untouched]

⚠ Manual attention needed:
  [anything ambiguous, conflicting, or that needs human decision]
```

---

## HOW TO SAVE THIS AS A GEMINI CLI COMMAND

Save this file as `docs/sync.md` in the repository root.

To run it anytime, execute:
```bash
gemini -f docs/sync.md
```

Or add this alias to your shell profile (~/.bashrc or ~/.zshrc):
```bash
alias matp-sync='cd /path/to/matp && gemini -f docs/sync.md'
```

Then from any terminal, typing `matp-sync` runs the full documentation sync.