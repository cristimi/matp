# MATP Project Context

Self-hosted automated crypto trading platform. Docker Compose on a homelab (Proxmox/LXC).
Services: `nginx` (reverse proxy), `postgres`, `redis`, `order-listener`, `order-generator`,
`order-executor`, `dashboard-api`, `dashboard-ui`, `ai-signal-generator`, `strategy-tester`, `tester-ui`.

## Golden rules

- Always use `docker compose` (never `docker-compose`).
- Deferred work and design decisions live in `docs/ROADMAP.md` (see its "Deferred Backlog" and "Open Design Questions"). Check there before starting new feature work.
- DB: PostgreSQL, user=`matp`, database=`matp`. Inspect with
  `docker compose exec postgres psql -U matp -d matp -c "..."`.
- Never print private keys, credentials, or secrets.
- Always read the relevant files before changing anything. Verify changes against the
  running container, not the host build output (see "Verifying a deploy").

## Deploying code changes — use the script

After ANY source change to a service, redeploy it with:

```bash
./scripts/redeploy.sh <service>          # fast: layer-cached rebuild + force-recreate
./scripts/redeploy.sh <service> --clean  # full --no-cache rebuild (use if cache looks stale)
./scripts/redeploy.sh all                # rebuild + recreate everything
```

The script runs `docker compose build` → `up -d --force-recreate <service>` → prunes the old
image → prints the live UI asset hash. Do this instead of the manual dance.

- **Never** use `docker compose restart <service>` to pick up code — restart reuses the
  running container and the existing image, so source changes are ignored.
- `--force-recreate` is what guarantees the new image replaces the old container. A plain
  `up -d` after a rebuild can leave the previous container running ("stale container").
- Plain (cached) build is correct: Docker invalidates the `COPY . .` layer on any source
  change, so the bundle rebuilds. Reach for `--clean` (`--no-cache`) only when you suspect
  a corrupted layer — it re-runs `npm ci`/`pip install` and is much slower.

## UI builds — single source of truth is the image

`dashboard-ui` and `tester-ui` bake the built bundle into the image
(`Dockerfile`: `COPY --from=builder /app/dist /usr/share/nginx/html`). There is **no**
bind-mount over the served directory — so the **only** way to update the live UI is to
rebuild the image (`./scripts/redeploy.sh dashboard-ui`). A host-side `npm run build` does
**not** affect what's served and should not be relied on for deploys.

> If you ever re-add a `volumes: - ./dashboard-ui/dist:/usr/share/nginx/html` mount, you
> reintroduce the split-brain bug: `docker compose build` becomes a no-op for what nginx
> serves, and the container serves a stale host `dist/`. Don't.

For fast local iteration, run the Vite dev server (`npm --prefix dashboard-ui run dev`)
outside Docker; use the image build only for deploying to the stack.

## nginx config changes

`nginx/nginx.conf` is bind-mounted read-only into the `nginx` container. Editing it does
**not** auto-reload. Apply with:

```bash
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload
```

Caching is already correct: hashed `/assets/` are `immutable, 1y`; `index.html` is
`no-store, no-cache, must-revalidate`, and the outer proxy forwards those headers untouched.
So a new deploy is picked up on the next page load without users clearing anything.

## Verifying a deploy (don't trust host build output)

```bash
docker compose ps <service>                      # state = running, not restarting
# UI: confirm the served bundle is the new one (string survives minification):
docker compose exec -T dashboard-ui grep -rl '<a-string-from-your-change>' /usr/share/nginx/html
curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'   # live asset hash changed
# API: hit the health endpoint
curl -sf http://localhost:8003/health
```

Health endpoints: order-listener `:8001`, order-executor `:8004`, dashboard-api `:8003`,
ai-signal-generator `:8005`, strategy-tester `:8006`.

## "Old UI still showing" after a correct deploy

If `curl http://localhost/` returns the new asset hash but the device shows the old UI, it's
the **browser's** HTTP cache holding a stale `index.html` from before the no-store header
applied. One-time fix per device: clear Chrome "Cached images and files". It will not recur,
because `index.html` is served `no-store`. There is no service worker, so no offline cache.

## Branch policy

`main` is the only long-lived branch — trunk-based. Keep the branch list tiny.

- One `feat/<name>` per *active* roadmap workstream. Merge to `main` and **delete** it the
  moment that item ships — don't keep merged branches "just in case".
- `fix/<name>` and `chore/<name>` are ephemeral — deleted on merge. The GitHub repo setting
  *Automatically delete head branches* is ON; leave it on.
- **Snapshots are tags, never branches.** Use `git tag archive/<name> <sha>` then
  `git push origin archive/<name>` — never a `backup/*` branch. Tags stay out of the branch
  list and are immutable by convention.
- Every executor prompt declares its target up front and runs the bootstrap block below as
  its first step. **Do not check out or create branches by hand** — the prompt does it.

### Standard branch bootstrap (Step 0 of every prompt)

Each prompt carries a header `TARGET_BRANCH: <branch>` / `BASE_BRANCH: main`, then runs this
verbatim before any work. It lands on the target branch, creating it from base if needed,
stashing stray work first so nothing is lost:

```bash
set -e
TARGET="<branch>"; BASE="main"
git fetch -q origin
if ! (git diff --quiet && git diff --cached --quiet); then
  git stash push -u -m "auto-bootstrap $(date -u +%FT%TZ)" >/dev/null
  echo "NOTE: stashed uncommitted changes (recover: git stash list / git stash pop)"
fi
if git show-ref --verify --quiet "refs/heads/$TARGET"; then git checkout -q "$TARGET"
elif git ls-remote --exit-code --heads origin "$TARGET" >/dev/null 2>&1; then git checkout -q -t "origin/$TARGET"
else git checkout -q -B "$TARGET" "origin/$BASE"; echo "NOTE: created $TARGET from origin/$BASE"; fi
CUR=$(git branch --show-current); echo "On branch: $CUR"
[ "$CUR" = "$TARGET" ] || { echo "ABORT: failed to land on $TARGET"; exit 1; }
echo "BOOTSTRAP OK"
```

## Reports — write last, then push

Every prompt ends by writing a report to `.gemini/reports/<NAME>.md` containing the actual
pasted command output that proves the work (builds, greps, verifications) — not a summary.

**The report is the final action, and you must push after writing it.** Order matters:

1. Do the work and verify it.
2. Write the report file.
3. `git add -A && git commit` — the report and the change land in the same commit.
4. `git push`.

Never push before the report exists. The reviewer verifies by re-cloning `origin` and
reading the committed report; an unpushed report is invisible and forces a manual re-push.
If you already pushed earlier in the session, push again after committing the report.
