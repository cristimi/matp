# Desktop UI — Tree page pilot (feat/desktop-ui)

## Goal

Existing dashboard-ui is mobile-first (fixed `maxWidth: 460` single-column card
layout) and should keep being served to phones as-is. Pilot a desktop-optimized
variant of the same UI for larger screens (laptop/monitor widths), starting with
the Tree page (`/tree`, the landing page), before extending to the rest of the app.

## Approach

Single codebase, single build (no separate app/route tree). Added a CSS
container-query-driven card grid:

- `dashboard-ui/src/App.tsx`: `<main>` now has `[container-type:inline-size]`,
  so layout responds to the actual content-area width (not raw window width —
  correctly adapts whether the sidebar is expanded or collapsed).
- `dashboard-ui/src/index.css`: new `.card-flow` / `.col-break-avoid` /
  `.col-span-all` utilities. `.card-flow` is `columns: 1` by default (identical
  to current mobile behavior), stepping to 2 columns at a 900px container width
  and 3 columns at 1400px, via `@container` queries.
- `dashboard-ui/src/pages/StrategyTree.tsx`: outer wrapper `maxWidth` raised from
  460 to 1720 (was hard-capping the page to a phone-width column even on a
  monitor); cards container uses `.card-flow`; each `StrategyCard` root gets
  `.col-break-avoid` so an expanded card is never split across a column break;
  loading/error/empty states get `.col-span-all` so they don't get stuck in one
  narrow column.

At container widths under 900px (phones, narrow windows) the page is visually
unchanged — one column, same padding, same cards. Nothing about the mobile
BottomNav/Sidebar breakpoint logic changed.

## Verification

Deployed via `./scripts/redeploy.sh dashboard-ui`:

```
Container matp-dashboard-ui-1 Recreated
Container matp-dashboard-ui-1 Started
▶ Verifying …
NAME                  IMAGE               COMMAND                  SERVICE        CREATED          STATUS         PORTS
matp-dashboard-ui-1   matp-dashboard-ui   "/docker-entrypoint.…"   dashboard-ui   10 seconds ago   Up 3 seconds   80/tcp, 3000/tcp
   live dashboard-ui asset: index-BI6G1i5O.js
✓ dashboard-ui redeployed.
```

Confirmed the new rules actually shipped in the live served bundle (not just the
source tree):

```
$ docker compose exec -T dashboard-ui grep -rl 'card-flow' /usr/share/nginx/html
/usr/share/nginx/html/assets/index-BI6G1i5O.js
/usr/share/nginx/html/assets/index-DALV-NSa.css

$ curl -s http://localhost/assets/index-DALV-NSa.css | grep -oE '\.card-flow\{[^}]*\}|@container[^{]*\{[^}]*\.card-flow[^}]*\}|\.col-break-avoid\{[^}]*\}|\.col-span-all\{[^}]*\}'
.card-flow{-moz-columns:1;columns:1;-moz-column-gap:14px;column-gap:14px}
@container (min-width: 900px){.card-flow{-moz-columns:2;columns:2}
@container (min-width: 1400px){.card-flow{-moz-columns:3;columns:3}
.col-break-avoid{-moz-column-break-inside:avoid;break-inside:avoid}
.col-span-all{-moz-column-span:all;column-span:all}

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-BI6G1i5O.js
```

**Not done in this session:** a real-browser screenshot pass. The host is missing
several shared libs needed by the local headless Chromium/Firefox builds
(`libnspr4`, `libnss3`, `libgtk-3-0`, etc.). Installing them is a persistent
change to this host's system packages outside the repo, so I asked before doing
it — user chose to skip the apt install and verify visually themselves rather
than have me modify host packages. So: CSS/DOM-level proof above confirms the
build shipped correctly and the container-query math is sound, but nobody has
visually confirmed the multi-column reflow in an actual browser window yet.

## Next

- User to eyeball `/tree` in a real browser at phone width (unchanged) and a
  wide window (900px+ / 1400px+ content width) to confirm the reflow looks
  right, then green-light extending the same `.card-flow` pattern to the
  remaining pages (Accounts, Signals, AI Log, Settings, etc.) on this branch.
- This work stays on `feat/desktop-ui` until the desktop layout is complete and
  verified across pages, then folds into `main` per CLAUDE.md's branch
  convention (alongside `feat/signal-engine` / `feat/social-listener`).
