# Branch Cleanup Report — 2026-06-21

## Step 0 — Bootstrap

```
On branch: main
BOOTSTRAP OK
```

## Step 1 — Precondition guard

```
=== remote branches before cleanup ===
  origin/backup/feat-signal-engine-pre-fix
  origin/backup/feat-signal-engine-pre-split
  origin/feat/market-ingestion
  origin/feat/signal-engine
  origin/feat/social-listener
  origin/main
OK: feat/market-ingestion already in main — safe to delete
PRECONDITIONS OK
```

## Step 2 — Delete merged branch

```
To github.com:cristimi/matp.git
 - [deleted]         feat/market-ingestion
Deleted branch feat/market-ingestion (was df363ee).
deleted feat/market-ingestion
```

## Step 3 — Archive backup branches as tags, then delete

```
To github.com:cristimi/matp.git
 * [new tag]         archive/signal-engine-pre-fix -> archive/signal-engine-pre-fix
 * [new tag]         archive/signal-engine-pre-split -> archive/signal-engine-pre-split
OK archive/signal-engine-pre-fix == origin/backup/feat-signal-engine-pre-fix (58b7310c0169a62b86487b395b192a2813eb46e9)
OK archive/signal-engine-pre-split == origin/backup/feat-signal-engine-pre-split (43d9cc981fb82ab494190fe3a6e2109648e4e391)
To github.com:cristimi/matp.git
 - [deleted]         backup/feat-signal-engine-pre-fix
 - [deleted]         backup/feat-signal-engine-pre-split
backups archived as tags and branches deleted
```

## Step 4 — Final verification

```
=== remaining remote branches (EXPECT exactly: main, feat/signal-engine, feat/social-listener) ===
  origin/feat/signal-engine
  origin/feat/social-listener
  origin/main
=== archive tags (EXPECT both) ===
  archive/signal-engine-pre-fix
  archive/signal-engine-pre-fix^{}
  archive/signal-engine-pre-split
  archive/signal-engine-pre-split^{}
```

---

**Final state:** 3 remote branches (`main`, `feat/signal-engine`, `feat/social-listener`) and 2 `archive/*` annotated tags — exactly as intended. Nothing was force-pushed; `main` was never modified.

**Step 5 — PENDING (manual):** Enable **GitHub → Settings → General → "Automatically delete head branches"** so future merged `fix/`/`chore/`/`feat/` branches self-delete after PR merge.
