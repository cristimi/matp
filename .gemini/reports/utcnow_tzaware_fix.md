# Report: fix/utcnow-tzaware — Replace deprecated `datetime.utcnow()` with tz-aware UTC

## STEP 0 — Branch bootstrap output

```
NOTE: created fix/utcnow-tzaware from origin/main
On branch: fix/utcnow-tzaware
BOOTSTRAP OK
```

`git branch --show-current` → `fix/utcnow-tzaware`

---

## STEP 2 — Diff: `ai-signal-generator/app/webhook/dispatcher.py`

```diff
-from datetime import datetime
+from datetime import datetime, timezone
```

```diff
-        'timestamp':   datetime.utcnow().isoformat() + 'Z',
+        'timestamp':   datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
```

---

## STEP 3 — Diff: `scripts/test_webhook_manual.py`

```diff
-from datetime import datetime
+from datetime import datetime, timezone
```

```diff
-    "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
+    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
```

---

## STEP 4 — Verification output

### 4a. Source greps

```
$ grep -n "utcnow" ai-signal-generator/app/webhook/dispatcher.py scripts/test_webhook_manual.py || echo "NO utcnow REMAINING — good"
NO utcnow REMAINING — good

$ grep -n "datetime.now(timezone.utc)" ai-signal-generator/app/webhook/dispatcher.py
42:        'timestamp':   datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),

$ grep -n "datetime.now(timezone.utc)" scripts/test_webhook_manual.py
18:    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
```

### 4b. Wire-format check

```
$ python3 -c "from datetime import datetime, timezone; s=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); print(s); assert s.endswith('Z') and '+00:00' not in s and 'T' in s, s; print('FORMAT OK')"
2026-06-21T13:33:38.580035Z
FORMAT OK
```

### 4c. Redeploy + in-image verification

```
$ ./scripts/redeploy.sh ai-signal-generator
▶ Building ai-signal-generator  …
[... layer cache hit on deps, rebuilt COPY app/ layer ...]
✓ ai-signal-generator redeployed.

$ docker compose exec -T ai-signal-generator python -c "import inspect, app.webhook.dispatcher as d; src=inspect.getsource(d); assert 'utcnow' not in src and 'datetime.now(timezone.utc)' in src; print('IN-IMAGE OK')"
IN-IMAGE OK
```

---

## Files modified (git status --short before commit)

```
M ai-signal-generator/app/webhook/dispatcher.py
 M scripts/test_webhook_manual.py
```

No other files were modified.

---

## Commit and push

```
$ git log --oneline -1
d09ab25 fix: tz-aware UTC in ai-engine dispatcher (preserve Z wire format)

$ git status -sb
## fix/utcnow-tzaware...origin/fix/utcnow-tzaware
```
