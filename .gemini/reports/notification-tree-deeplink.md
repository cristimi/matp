# Notification click → Tree page deep link (strategy + position focus)

## What changed

Clicking a `position.opened`/`position.closed` browser push notification now
navigates to the Tree page, auto-expands the relevant strategy card, and
scrolls/highlights the specific position — instead of dumping the user on the
generic `/positions` page.

1. **`notification-service/app/render.py`** — `position.opened`/`position.closed`
   payloads now forward `strategy_id` and `symbol` in the notification `data`
   block (the source event already carried both; they were just being dropped
   before reaching the browser).
2. **`dashboard-ui/public/sw.js`** — `notificationclick` now builds
   `/tree?strategy=<id>&position=<id>` when both ids are present (falls back to
   `/positions` or `/` for older/other event shapes).
3. **`dashboard-ui/src/pages/StrategyTree.tsx`** — `StrategyTreePage` reads
   `strategy`/`position` from the URL once on mount (via `useSearchParams`),
   strips them from the URL immediately (`replace: true`, so refresh doesn't
   re-trigger), and passes the target down as props. The matching
   `StrategyCard` auto-expands (all positions, including closed — the target
   position could be either), bumps the closed-list page size if the target is
   paginated out of view, and scrolls itself into view. The matching
   `PositionCard` scrolls into view and gets a 3s blue glow highlight.

## Verification

TypeScript check clean:
```
$ npx tsc --noEmit -p .
(exit 0, no output)
```

`notification-service` redeployed and confirmed serving the new payload shape:
```
$ docker compose ps notification-service
NAME                          STATUS
matp-notification-service-1   Up 7 minutes (healthy)

$ docker compose exec -T notification-service grep -n "strategy_id" app/render.py
39:        strategy_id = data.get("strategy_id")
52:            "data": {"position_id": position_id, "strategy_id": strategy_id, "symbol": symbol},
57:        strategy_id = data.get("strategy_id")
75:            "data": {"position_id": position_id, "strategy_id": strategy_id, "symbol": symbol},
```

`dashboard-ui` redeployed; live bundle confirmed to contain the new code and
the served asset hash matches the built one:
```
$ docker compose ps dashboard-ui
NAME                  STATUS
matp-dashboard-ui-1   Up 11 seconds

$ docker compose exec -T dashboard-ui grep -o "strategyId" /usr/share/nginx/html/sw.js
strategyId
strategyId
strategyId

$ docker compose exec -T dashboard-ui grep -rl 'focusPositionId\|isFocusTarget' /usr/share/nginx/html/assets/
/usr/share/nginx/html/assets/index-CbBif4A6.js

$ curl -s http://localhost/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
index-CbBif4A6.js
```

Both match — the served UI is the new build.

Not tested end-to-end with a real push notification (would require sending a
live webhook-triggered position event and clicking the resulting OS
notification), but the payload shape, URL construction, and client-side
focus/highlight logic were verified by code inspection and type-checking.
