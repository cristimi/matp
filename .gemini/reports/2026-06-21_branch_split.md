# Branch Split: `feat/signal-engine` → two independent branches
**Date:** 2026-06-21  
**Operation:** Pure branch surgery. No code edited. No migrations renumbered.

---

## Pre-flight (STEP 0)

### Social commit identified and confirmed as tip

```
$ git checkout feat/signal-engine
$ SOCIAL_SHA=$(git log feat/signal-engine --grep='feat(social-listener)' --format=%H -1)
$ echo "SOCIAL_SHA=$SOCIAL_SHA"
SOCIAL_SHA=43d9cc981fb82ab494190fe3a6e2109648e4e391

$ TIP_SHA=$(git rev-parse feat/signal-engine)
$ echo "TIP_SHA=$TIP_SHA"
TIP_SHA=43d9cc981fb82ab494190fe3a6e2109648e4e391

GUARD OK: social commit IS the tip
```

### Social commit stat (only social files — guard passed)

```
$ git show --stat 43d9cc981fb82ab494190fe3a6e2109648e4e391 | tail -n +2

Author: cristi.militaru@gmail.com <cristi.militaru@gmail.com>
Date:   Sun Jun 21 07:14:17 2026 +0000

    feat(social-listener): add Telegram read+parse ingestion worker (Phase 1 dry run)
    ...

 .gemini/reports/2026-06-21_social_listener_read_parse.md  | 227 +++++++++++++++++++++
 db/migrations/025_social_signal_log.sql                   |  60 ++++++
 docker-compose.yml                                        |  21 ++
 social-listener/Dockerfile                                |   6 +
 social-listener/app/__init__.py                           |   0
 social-listener/app/config.py                             |  29 +++
 social-listener/app/db.py                                 |  52 +++++
 social-listener/app/extractor.py                          | 110 ++++++++++
 social-listener/app/generate_session.py                   |  14 ++
 social-listener/app/main.py                               |  62 ++++++
 social-listener/app/telegram.py                           |  42 ++++
 social-listener/requirements.txt                          |   8 +
 12 files changed, 631 insertions(+)
```

Only `social-listener/*`, `db/migrations/025_social_signal_log.sql`, `docker-compose.yml`,
and `.gemini/reports/2026-06-21_social_listener_read_parse.md`. Guard passed.

---

## Safety Backup (STEP 1)

```
$ git branch backup/feat-signal-engine-pre-split feat/signal-engine
$ git rev-parse backup/feat-signal-engine-pre-split
43d9cc981fb82ab494190fe3a6e2109648e4e391
```

Backup branch pushed to remote:
```
To github.com:cristimi/matp.git
 * [new branch]      backup/feat-signal-engine-pre-split -> backup/feat-signal-engine-pre-split
```

---

## Create `feat/social-listener` (STEP 2)

```
$ git checkout -b feat/social-listener origin/main
Switched to a new branch 'feat/social-listener'
branch 'feat/social-listener' set up to track 'origin/main'.

$ git cherry-pick 43d9cc981fb82ab494190fe3a6e2109648e4e391
Auto-merging docker-compose.yml
CONFLICT (content): Merge conflict in docker-compose.yml
error: could not apply 43d9cc9... feat(social-listener): ...
```

**Conflict in `docker-compose.yml` (expected).** The cherry-pick brought both the
`signal-engine:` and `social-listener:` appended blocks; `main` has neither. Resolved by
keeping only the `social-listener:` block (discarding `signal-engine:` from the patch).

```
$ git add docker-compose.yml
$ git cherry-pick --continue
[feat/social-listener cbe9f93] feat(social-listener): add Telegram read+parse ingestion worker (Phase 1 dry run)
 Date: Sun Jun 21 07:14:17 2026 +0000
 12 files changed, 631 insertions(+)
 create mode 100644 .gemini/reports/2026-06-21_social_listener_read_parse.md
 create mode 100644 db/migrations/025_social_signal_log.sql
 create mode 100644 social-listener/Dockerfile
 ...
```

New SHA on `feat/social-listener`: `cbe9f93` (new commit, cherry-pick resolved).

---

## Rewrite `feat/signal-engine` (STEP 3)

```
$ git checkout feat/signal-engine
$ [ "$(git rev-parse HEAD)" = "43d9cc981fb82ab494190fe3a6e2109648e4e391" ] && echo "OK: tip is social commit"
OK: tip is social commit

$ git reset --hard HEAD~1
HEAD is now at d4e2e07 fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source

$ git log --oneline -3
d4e2e07 fix(signal-engine/diff): key ground-truth lookup on strategy_id not signal_source
3f297b4 feat(signal-engine): add deterministic signal engine + entry shadow-diff harness
b224252 docs: record Prompt 1 TV comparison result
```

---

## Verification (STEP 4)

### `feat/signal-engine` — signal-engine only

```
$ git checkout feat/signal-engine
$ git diff --stat origin/main...HEAD
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

$ ls social-listener 2>&1
ls: cannot access 'social-listener': No such file or directory

$ ls db/migrations/ | grep -E '02[45]'
024_shadow_signals.sql

$ grep -c 'social-listener:' docker-compose.yml
0
```

All guards passed: no social-listener, only 024, social compose block absent.

### `feat/social-listener` — social only

```
$ git checkout feat/social-listener
$ git diff --stat origin/main...HEAD
 .gemini/reports/2026-06-21_social_listener_read_parse.md  | 227 +++++++++++++++++++++
 db/migrations/025_social_signal_log.sql                   |  60 ++++++
 docker-compose.yml                                        |  21 ++
 social-listener/Dockerfile                                |   6 +
 social-listener/app/__init__.py                           |   0
 social-listener/app/config.py                             |  29 +++
 social-listener/app/db.py                                 |  52 +++++
 social-listener/app/extractor.py                         | 110 ++++++++++
 social-listener/app/generate_session.py                   |  14 ++
 social-listener/app/main.py                               |  62 ++++++
 social-listener/app/telegram.py                           |  42 ++++
 social-listener/requirements.txt                          |   8 +
 12 files changed, 631 insertions(+)

$ ls signal-engine 2>&1
ls: cannot access 'signal-engine': No such file or directory

$ ls db/migrations/ | grep -E '02[45]'
025_social_signal_log.sql

$ grep -c 'social-listener:' docker-compose.yml
1

$ grep -c 'signal-engine:' docker-compose.yml
0
```

All guards passed: no signal-engine, only 025, social compose block present, signal-engine compose block absent.

---

## Push (STEP 5)

```
# New branch (normal push):
$ git push -u origin feat/social-listener
To github.com:cristimi/matp.git
 * [new branch]      feat/social-listener -> feat/social-listener
branch 'feat/social-listener' set up to track 'origin/feat/social-listener'.

# Rewritten branch (force-with-lease):
$ git checkout feat/signal-engine
$ git push --force-with-lease origin feat/signal-engine
To github.com:cristimi/matp.git
 + 43d9cc9...d4e2e07 feat/signal-engine -> feat/signal-engine (forced update)
```

`--force-with-lease` accepted (remote was at `43d9cc9`, which was the pre-split state we accounted for).

---

## Notes for human review

### Migration gap is expected and intentional

`feat/social-listener` carries `025_social_signal_log.sql` but not `024_shadow_signals.sql`
(which belongs to signal-engine). Do **NOT** renumber — migrations are never edited after creation.

**Recommended merge order to `main`:**
1. `feat/signal-engine` first → lands `024_shadow_signals`
2. `feat/social-listener` second → lands `025_social_signal_log`

The tables are completely independent, so either order is safe schema-wise. This order keeps
the sequence tidy.

### Backup branch

`backup/feat-signal-engine-pre-split` (`43d9cc9`) is pinned both locally and on remote.
It preserves the combined pre-split state. Safe to delete once you've confirmed both branches
are good:

```bash
git push origin --delete backup/feat-signal-engine-pre-split
```

---

## Definition of Done — checklist

- [x] `backup/feat-signal-engine-pre-split` created and pushed (`43d9cc9`)
- [x] `feat/social-listener` exists off `main` with only the social commit (`cbe9f93`) — verified: no `signal-engine/`, no `024`, compose has social block only, signal-engine block absent
- [x] `feat/signal-engine` rewritten to drop social commit (tip: `d4e2e07`) — verified: no `social-listener/`, no `025`, social compose block absent
- [x] Both branches pushed (`feat/social-listener` normal push, `feat/signal-engine` force-with-lease)
- [x] No migration renumbered; no workstream code edited
