# Manual short opened for an X-only Astronomer post (2026-08-18)

## Why

The trader posted a new BTC short on X at ~64,200 about an hour before this session.
The post was **never mirrored to the Telegram channel `AstronomerZero`**, which is the
listener's only input, so the listener never saw it and nothing appeared in the social log.

Investigation first confirmed the listener itself was healthy, not stuck:

```
social-listener-1  | 2026-08-18 00:27:57,577 INFO telethon...Closing current connection to begin reconnect...
social-listener-1  | 2026-08-18 00:28:01,711 INFO telethon...Connection to 149.154.167.92:443/TcpFull complete!
```

- Container up 5 days, Telegram connected, no errors, no `catchup loop error`,
  no `extraction unavailable` lines.
- The catchup loop was observably alive — the listener's DB connection was re-used
  every ~30s throughout the check:

```
08:32:34 last_used=5s
08:32:53 last_used=18s
08:33:04 last_used=5s
```

- Last ingested post was msg 9832 on 2026-08-12 (the flip to long), and that long was
  already reconciled away on 2026-08-17 (`RECONCILE BTC LONG: no open position`).
  Recorded state was therefore FLAT, matching the exchange.

Conclusion: no bug. Silent input, not a broken pipeline.

## What was done

A one-off script inside the `social-listener` container drove the listener's **own**
emitter, so the order went out through the normal path (order-listener webhook,
`signal_source=social_listener`, `source=telegram:AstronomerZero`) and the resulting
position is owned and managed by the social strategy exactly as an ingested post
would leave it. No exchange call was made from the script itself.

Dry run first:

```
recorded legs: FLAT
exchange-side open position: None
mark=64287.5 size=0.00466654 margin=15 lev=20
DRY RUN — nothing sent. re-run with --go
```

Live:

```
recorded legs: FLAT
exchange-side open position: None
mark=64287.4 size=0.00466654 margin=15 lev=20
emit ok= True open_short->5e541922-a3ee-4ced-91e7-df7008edbb67
recorded legs now: SHORT
```

## Verification

Order filled, with order-listener's guaranteed stop injected as usual:

```
id                | 5e541922-a3ee-4ced-91e7-df7008edbb67
symbol            | BTC-USDT
side              | sell
signal            | open_short
size              | 0.004700000000000000000
status            | filled
actual_fill_price | 64289.6
sl_price          | 67215.2
signal_source     | social_listener
signal_metadata   | {"source": "telegram:AstronomerZero", "sl_source": "liquidation_safe", ...}
strategy_id       | social-btc-astro
account_id        | blofin-blofin-demo-v5vr
```

Position open under the strategy:

```
 e590614d-a4d0-433f-98fe-fe2db876c6fd | social-btc-astro | BTC-USDT | short | 0.0047 | 64289.6 | open | 2026-08-18 08:37:31.080016+00
```

Listener state, log row and audit row:

```
 telegram:AstronomerZero | BTC | SHORT | OPEN | -20260818 | 2026-08-18 08:37:27.087133+00

 channel_msg_id | posted_at              | action_type | asset | direction | reference_price | model  | extractor_version
 -20260818      | 2026-08-18 07:30:00+00 | OPEN        | BTC   | SHORT     | 64200           | manual | manual

 channel_msg_id | phase | asset | intended_signal | decision | reason       | mode
 -20260818      | live  | BTC   | open_short      | acted    | manual_entry | live
```

## Choices worth knowing

- **`channel_msg_id = -20260818` (negative, synthetic).** No Telegram message exists.
  A positive id would have been fatal: `max_channel_msg_id()` drives the catchup loop,
  so a fake 9833 would make the listener skip the *real* msg 9833 when the mirror
  resumes. Negative keeps the watermark at 9832 and cannot collide with a real id.
- **Rows are labelled `manual`**, not dressed up as an LLM extraction — `model` and
  `extractor_version` are both `manual`, the reason is `manual_entry`, and the raw text
  states plainly that the operator entered it from an X post that Telegram never carried.
  The *position* is genuinely the listener's; the *provenance* is honest.
- **Entered at market (64,289.6) rather than at the quoted 64,200** — 0.14% away, and
  in the short's favour. The normal staleness gate (1%) would have allowed it too.
- The listener now records a SHORT leg, so a later Telegram TRIM/CLOSE post will act on
  this position normally, and the reconcile sweep will clear the leg if the stop fires.

## Script used (one-off, not committed to the image)

Ran as `/app/manual_open.py` inside the container and deleted afterwards. It loads the
execution strategy, refuses unless both the recorded legs and the exchange are flat,
calls `emitter.emit("open_short", ...)`, then writes `social_signal_log`,
`social_shadow_orders` and `open_leg`. Same sequence `main.handle()` performs.
