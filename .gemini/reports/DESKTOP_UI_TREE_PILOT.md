# Desktop UI — Tree page pilot (feat/desktop-ui)

## Goal

Existing dashboard-ui is mobile-first (tap-to-expand cards) and should keep being
served to phones as-is. Pilot a desktop-optimized variant of the same UI for
larger screens (laptop/monitor widths), starting with the Tree page (`/tree`,
the landing page), before extending to the rest of the app.

## Approach (revision 2 — see below for what changed)

First attempt reflowed cards into 2-3 CSS columns on wide screens (Pinterest/
masonry style). Rejected: not what was wanted. The actual reference was
VueTorrent/qBittorrent-style WebUIs — desktop shouldn't add *columns of cards*,
it should show *more information per row by default*, no click required.

Rebuilt around that instead, single codebase/build, no separate route tree:

- `dashboard-ui/src/pages/StrategyTree.tsx`: added a `useContainerWidth` hook
  (`ResizeObserver` on the page's own wrapper div) driving `isDesktop` at a
  content-area width of 860px. Content width, not window width, because the
  sidebar can be expanded/collapsed independent of the browser window.
- Below 860px: page is byte-identical to the previous mobile behavior (fixed
  460px column, tap-to-expand cards, long-press to collapse).
- At/above 860px:
  - Outer wrapper widens to 1160px.
  - Each `StrategyCard`'s header collapses onto one line — symbol/name/type/
    account pills on the left, Allocation/Total Return/Open PnL metrics on the
    right (`justify-content: space-between`) — instead of three stacked rows.
  - **Open positions fetch and render immediately on mount, for every
    strategy, with no click.** (`doFetchOpen` fires from a `useEffect` gated on
    `isDesktop`, not on tap.) Only 10 strategies exist right now, so this is a
    handful of cheap requests, not a real load concern.
  - Closed positions are a separate, independent "Show/Hide" toggle per card
    (`closedOpenDesktop` state) rather than being tied to the mobile
    collapsed/open/all cycle — clicking it fetches `scope=all` and renders the
    closed list with the existing "Load more" pagination.
  - Open positions (left) and the closed-positions toggle+list (right) render
    side by side in a 2-column CSS grid *within* the card — this is the one
    place two columns still exist, and it's inside a single wide card, not a
    grid of separate cards.
  - No collapse button on desktop — there's nothing to collapse back to, since
    the open-positions section is always shown.
- Reverted the discarded approach cleanly: removed `.card-flow` /
  `.col-break-avoid` / `.col-span-all` from `index.css` and the
  `[container-type:inline-size]` on `<main>` in `App.tsx` (that infra is no
  longer used — the width detection now happens in JS via `ResizeObserver`,
  scoped to the one page, since the layout branches on more than just CSS this
  time: it changes what data gets fetched, not just how it's arranged).

## Verification

`npx tsc --noEmit` — clean (exit 0). Caught one real bug this way: the
`useContainerWidth` hook's return type was declared `RefObject<T | null>`,
which doesn't match what `useRef<T>(null)` actually produces
(`RefObject<T>`) — mismatch failed `ref={wrapRef}`. Fixed before it reached
`vite build` a second time (it *did* reach the Docker build once — the first
`npx tsc --noEmit` run looked clean only because its output was piped through
`| head -80`, which reports `head`'s exit code, not `tsc`'s; `docker compose
build` caught the real type error. Re-ran the typecheck unpiped afterward and
confirmed a genuine exit 0 before redeploying again.)

Deployed via `./scripts/redeploy.sh dashboard-ui`:

```
Container matp-dashboard-ui-1 Recreated
Container matp-dashboard-ui-1 Started
▶ Verifying …
NAME                  IMAGE               COMMAND                  SERVICE        CREATED          STATUS         PORTS
matp-dashboard-ui-1   matp-dashboard-ui   "/docker-entrypoint.…"   dashboard-ui   13 seconds ago   Up 4 seconds   80/tcp, 3000/tcp
   live dashboard-ui asset: index-n5zJYqp6.js
✓ dashboard-ui redeployed.
```

Confirmed the new code actually shipped in the live served bundle (not just the
source tree):

```
$ docker compose exec -T dashboard-ui grep -rl 'No open positions' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-n5zJYqp6.js
$ docker compose exec -T dashboard-ui grep -rl 'No closed positions' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-n5zJYqp6.js
$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-n5zJYqp6.js
```

**Not done in this session:** a real-browser screenshot pass. This host is
missing several shared libs needed by the local headless Chromium/Firefox
builds (`libnspr4`, `libnss3`, `libgtk-3-0`, etc.). Installing them is a
persistent change to this host's system packages outside the repo, so I asked
before doing it in the first round of this work — user chose to skip the apt
install and verify visually themselves rather than have me modify host
packages. Same tradeoff applies here: CSS/DOM-level proof above confirms the
build shipped correctly and the new code paths exist in the bundle, but nobody
has visually confirmed the wide-card layout (header line-wrap, the two-column
open/closed split inside the card, spacing) in an actual browser window yet.

## Next

- User to eyeball `/tree` in a real browser at phone width (should be
  unchanged) and a wide window (content area 860px+) to confirm the wide-card
  layout looks right — in particular whether the open/closed positions
  actually needing to auto-fetch-on-mount for every strategy row is desired
  long-term, and whether 860px is the right desktop threshold.
- Once signed off, extend the same `isDesktop` / always-show-open-positions
  pattern to the remaining pages (Accounts, Signals, AI Log, Settings, etc.) on
  this branch.
- This work stays on `feat/desktop-ui` until the desktop layout is complete and
  verified across pages, then folds into `main` per CLAUDE.md's branch
  convention (alongside `feat/signal-engine` / `feat/social-listener`).
