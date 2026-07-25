# `llm_failed` — fixes 1 and 5

**Date:** 2026-07-25
**Branch:** main
**Status:** DONE — both deployed and verified live

Implements items 1 and 5 from `.gemini/reports/2026-07-25_llm_failed_analysis.md`.

---

## 1. Non-chat models filtered out of the fallback chain

**Problem.** `llm_chain._build_chain()`'s cold-cache path builds the fallback chain from
`models_registry.raw_models()`, which returned the provider's entire model list with no
capability filter. For groq that includes speech-to-text and text-to-speech models; for Google it
also includes image, music, robotics and computer-use models. With `_MAX_FALLBACK_ATTEMPTS = 3`
they consumed the whole chain and returned 400 every time — 468 guaranteed-dead attempts over 14
days, and chains containing one failed **85.6%** vs 49.6% without.

**Change.** `models_registry.is_chat_capable()` — a deliberately conservative substring denylist
(`whisper`, `orpheus`, `-tts`, `speech`, `voice`, `audio`, `embed`, `rerank`, `guard`,
`moderation`, `image`, `imagen`, `banana`, `diffusion`, `lyria`, `veo`, `sora`, `video`,
`robotics`, `computer-use`), applied in:

- `raw_models()` — the cold-cache path that caused the bug.
- `probe_all_models()` — probing a Whisper model spends provider quota to learn what its id
  already tells us.

`get_available_models()` (the UI picker) is deliberately **untouched**: its docstring states "No
model is ever hidden", and that contract is not mine to change without asking.

The code comment records why the existing justification fails: *"an unverified attempt beats a
dead cycle"* is true for an unprobed **chat** model, and false for a speech model — which turns a
recoverable cycle into a dead one by consuming a slot.

**Verification — no working model is filtered.** Against every model that has ever produced a
decision (`proposed_action IS NOT NULL AND gate_rejection_reason IS DISTINCT FROM 'llm_failed'`):

```
models that have PRODUCED A DECISION: 23
  KEEP  bytedance-seed/seed-2.0-mini      KEEP  glm-4.5
  KEEP  claude-haiku-4-5-20251001         KEEP  glm-4.5-air
  KEEP  claude-sonnet-4-5-20250929        KEEP  glm-4.6
  KEEP  claude-sonnet-4-6                 KEEP  gpt-oss-120b
  KEEP  gemini-2.0-flash                  KEEP  llama-3.1-8b-instant
  KEEP  gemini-2.5-flash                  KEEP  llama-3.3-70b-versatile
  KEEP  gemini-2.5-flash-lite             KEEP  openai/gpt-oss-20b:free
  KEEP  gemini-2.5-pro                    KEEP  poolside/laguna-xs-2.1:free
  KEEP  gemini-flash-latest               KEEP  qwen/qwen3-32b
  KEEP  gemini-flash-lite-latest          KEEP  tencent/hy3
  KEEP  gemma-4-31b                       KEEP  tencent/hy3:free
                                          KEEP  zai-glm-4.7

false positives: NONE — no working model is filtered
```

And the offenders are all dropped:

```
  DROP  whisper-large-v3                DROP  lyria-3-pro-preview
  DROP  whisper-large-v3-turbo          DROP  nano-banana-pro-preview
  DROP  canopylabs/orpheus-v1-english   DROP  gemini-robotics-er-1.6-preview
  DROP  canopylabs/orpheus-arabic-saudi DROP  gemini-2.5-computer-use-preview-10-2025
  DROP  gemini-2.5-flash-preview-tts    DROP  text-embedding-004
  DROP  gemini-2.5-flash-image          DROP  llama-guard-4-12b
```

---

## 5. Alert on `llm_failed` share per strategy

**Problem.** An `llm_failed` cycle produces no decision: `gate_passed` stays false, no webhook
fires, nothing surfaces it. A strategy losing 30% of its cycles is externally indistinguishable
from one that chose to hold.

**Change.** `ai-signal-generator/app/llm_health_monitor.py` — a periodic monitor following the
existing `funding_monitor` contract exactly (Redis-hash state, hysteresis, xadd onto
`notifications:events`, never raises from the emit path):

| Setting | Default | Meaning |
|---|---|---|
| `llm_health_window_h` | 24 | rolling window |
| `llm_health_enter_pct` | 0.20 | degraded at ≥20% of cycles failing |
| `llm_health_exit_pct` | 0.10 | recovered below 10% |
| `llm_health_min_cycles` | 20 | below this the share is noise |
| `llm_health_interval_s` | 900 | check cadence |

The alert carries the **dominant cause and model**, because the remedy differs sharply: quota
means top up or repoint, config means the model can never work, parse means it can't hold the
schema. Renderers and dedup keys added to `notification-service/app/render.py`; status endpoint at
`/internal/llm-health/status`.

**Verification — it caught a real degradation on its first cycle.**

```
$ wget -qO- http://ai-signal-generator:8005/internal/llm-health/status
{"enabled":true,"window_h":24,"enter_pct":0.2,"exit_pct":0.1,"min_cycles":20,
 "strategies":{
   "ai-btc-6f8c":{"cycles":24,"failed":2,"failed_pct":8.3,"state":"ok",...},
   "bnb-ai-scalper-edbb":{"cycles":82,"failed":6,"failed_pct":7.3,"state":"ok",...},
   "eth-ai-34d2":{"cycles":24,"failed":1,"failed_pct":4.2,"state":"ok",...},
   "sol-ai-6486":{"cycles":25,"failed":0,"failed_pct":0.0,"state":"ok",...},
   "tao-ai-range-rotation-d257":{"cycles":24,"failed":1,"failed_pct":4.2,"state":"ok",...},
   "xrp-ai-3844":{"cycles":50,"failed":12,"failed_pct":24.0,"state":"degraded",
                  "model":"llama-3.3-70b-versatile"}}}
```

Event emitted, with the cause correctly classified:

```
$ redis-cli XREVRANGE notifications:events + - COUNT 1
{"event": "llm.degraded", "strategy_id": "xrp-ai-3844", "failed_pct": 24.0,
 "cycles": 50, "failed": 12, "window_h": 24, "enter_pct": 20.0, "exit_pct": 10.0,
 "model": "llama-3.3-70b-versatile", "cause": "quota / rate limit"}

$ redis-cli HGETALL llm_health_monitor:state
xrp-ai-3844 → degraded
```

Consumed and acked by notification-service (`last-delivered-id` = the event's id, `pending 0`,
`lag 0`), and the rendered output:

```
dedup: llm-health:xrp-ai-3844:degraded
title: ⚠️ LLM failing: xrp-ai-3844 24.0% of cycles
body : 12 of 50 cycles in the last 24h ended llm_failed — those produced no decision
       at all. Dominant cause: quota / rate limit (model llama-3.3-70b-versatile).

dedup: llm-health:xrp-ai-3844:recovered
title: ✅ LLM recovered: xrp-ai-3844
body : Failure rate back to 6.0% of cycles over the last 24h (below the 10.0% clear threshold).
```

---

## Notes

- **The 24h picture differs from the 14d one.** `bnb-ai-scalper-edbb` was the worst offender over
  14 days (28.4%) but is at 7.3% over the last 24h; `xrp-ai-3844` is the current problem at 24%.
  The monitor tracks the live window, which is the point.
- **Thresholds (20%/10%) are a first guess**, chosen to fire on the observed bad days without
  tripping on the ~10% baseline. Worth re-tuning once there's a week of monitor history.
- **Pre-existing, unrelated:** notification-service logs `WebPush send errored … Invalid EC key`
  for subscription `3f8166c0-…`. That subscription is broken independently of this work — alerts
  will render but not reach that endpoint until it re-subscribes.
- Items 2, 3 and 4 from the analysis are untouched: repointing `mistral-medium-3-5` (100%
  failure), topping up the exhausted accounts, and preferring cross-provider before same-account
  fallback.
