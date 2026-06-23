# Branch Split Fix: clean `feat/signal-engine`
**Date:** 2026-06-21  
**Operation:** Dropped 2 spurious commits (social-listener + split report) from `feat/signal-engine` via `git reset --hard` to exact TARGET SHA. No code edited. No migrations renumbered. `feat/social-listener` not touched.

---

## STEP 1 — TARGET derivation and guards

```
$ SOCIAL_SHA=$(git log feat/signal-engine --grep='feat(social-listener)' --format=%H -1)
$ TARGET=$(git rev-parse "${SOCIAL_SHA}^")
$ echo "SOCIAL_SHA=$SOCIAL_SHA"
SOCIAL_SHA=43d9cc981fb82ab494190fe3a6e2109648e4e391

$ echo "TARGET=$TARGET"
TARGET=d4e2e07bffe2bc11101031f28d8b00bda2bdda79

$ git show -s --format='%h %s' "$TARGET"
d4e2e07 fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source

$ git rev-list --count "${TARGET}..feat/signal-engine"
2
```

All three guards passed:
- `SOCIAL_SHA` = `43d9cc9` ✓
- TARGET subject = `fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source` ✓
- `rev-list --count` = **2** (social commit + split-report commit, nothing else) ✓

---

## STEP 2 — Safety ref

```
$ git branch backup/feat-signal-engine-pre-fix feat/signal-engine
$ git rev-parse backup/feat-signal-engine-pre-fix
58b7310c0169a62b86487b395b192a2813eb46e9
```

Backup pinned at `58b7310` (the tip before the reset, containing both spurious commits).  
Pushed to remote:
```
To github.com:cristimi/matp.git
 * [new branch]      backup/feat-signal-engine-pre-fix -> backup/feat-signal-engine-pre-fix
```

---

## STEP 3 — Reset

```
$ git reset --hard d4e2e07bffe2bc11101031f28d8b00bda2bdda79
HEAD is now at d4e2e07 fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source

$ git log --oneline -3
d4e2e07 fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source
3f297b4 feat(signal-engine): add deterministic signal engine + entry shadow-diff harness
b224252 docs: record Prompt 1 TV comparison result
```

---

## STEP 4 — Verification

### `git diff --stat origin/main...HEAD`

```
 db/migrations/024_shadow_signals.sql               |  42 +++
 docker-compose.yml                                 |  15 ++
 docs/process/reports/PROMPT_01_market_ingestion.md |   4 +-
 docs/process/reports/PROMPT_02_signal_engine.md    | 250 +++++++++++++++++
 signal-engine/Dockerfile                           |  10 +
 signal-engine/app/__init__.py                      |   0
 signal-engine/app/config.py                        |  17 ++
 signal-engine/app/database.py                      |  19 ++
 signal-engine/app/diff.py                          | 296 +++++++++++++++++++++
 signal-engine/app/engine.py                        | 119 +++++++++
 signal-engine/app/indicators.py                    |  43 +++
 signal-engine/app/main.py                          |  36 +++
 signal-engine/app/redis_reader.py                  |  94 +++++++
 signal-engine/app/shadow_store.py                  |  50 ++++
 signal-engine/app/strategies/__init__.py           |   0
 signal-engine/app/strategies/base.py               |  29 ++
 signal-engine/app/strategies/test_harness.py       | 135 ++++++++++
 signal-engine/requirements.txt                     |   8 +
 18 files changed, 1165 insertions(+), 2 deletions(-)
```

No `social-listener/`, no `025_social_signal_log.sql`, no social compose block, no branch_split report. ✓

### Presence checks

```
$ ls signal-engine 2>&1
app
Dockerfile
requirements.txt

$ ls social-listener 2>&1
ls: cannot access 'social-listener': No such file or directory

$ ls db/migrations/ | grep -E '02[45]'
024_shadow_signals.sql

$ grep -c 'social-listener:' docker-compose.yml
0

$ ls .gemini/reports/ | grep branch_split; echo "grep_exit:$?"
grep_exit:1
```

All guards passed:
- `signal-engine/` present ✓
- `social-listener/` absent ✓
- only `024_shadow_signals.sql` present (no 025) ✓
- `social-listener:` count in compose = 0 ✓
- `branch_split` report absent (grep returned no match, exit 1) ✓

---

## STEP 5 — Push

```
$ git push --force-with-lease origin feat/signal-engine
To github.com:cristimi/matp.git
 + 58b7310...d4e2e07 feat/signal-engine -> feat/signal-engine (forced update)
```

`--force-with-lease` accepted: remote was at `58b7310`, which is exactly the pre-fix state we accounted for.

---

## State summary

| Branch | Tip | Contents |
|--------|-----|----------|
| `feat/signal-engine` | `d4e2e07` | signal-engine/ + 024_shadow_signals + PROMPT_02 report only |
| `feat/social-listener` | `cbe9f93` | social-listener/ + 025_social_signal_log + compose block only — **not touched** |
| `backup/feat-signal-engine-pre-fix` | `58b7310` | pre-fix tip (social + split-report commits) |
| `backup/feat-signal-engine-pre-split` | `43d9cc9` | original combined tip from earlier run |

---

## Notes for human review

Both backup branches can be deleted once you confirm the two feature branches are good:

```bash
git push origin --delete backup/feat-signal-engine-pre-fix
git push origin --delete backup/feat-signal-engine-pre-split
```

Recommended merge order to `main`:
1. `feat/signal-engine` first → lands `024_shadow_signals`
2. `feat/social-listener` second → lands `025_social_signal_log`

---

## Definition of Done

- [x] TARGET confirmed as `d4e2e07` and exactly 2 commits were ahead of it (guards passed)
- [x] `backup/feat-signal-engine-pre-fix` created at `58b7310` before the reset
- [x] `feat/signal-engine` reset to `d4e2e07`: no `social-listener/`, no `025`, no social compose block, no branch_split report; `signal-engine/` + `024` present
- [x] `feat/signal-engine` force-pushed with `--force-with-lease`
- [x] `feat/social-listener` left untouched (tip still `cbe9f93`)
