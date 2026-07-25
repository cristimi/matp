# `llm_failed` — analysis (14 days)

**Date:** 2026-07-25
**Scope:** `ai_signal_log`, 2026-07-11 → 2026-07-25
**Status:** Analysis only — no code changed.

---

## Headline

821 cycles ended in `llm_failed` over 14 days — **~19% of all cycles**. It is the second most
common gate outcome after `hold_or_adjust`.

**This is not a model-quality problem.** Classifying all 2725 fallback attempts:

| Cause | Attempts | Share |
|---|---:|---:|
| quota / rate limit | 1752 | 64% |
| **CONFIG: model structurally incapable** | **470** | **17%** |
| timeout | 161 | 6% |
| missing credentials | 82 | 3% |
| structured-output parse | 49 | 2% |
| billing (402) | 34 | 1% |
| other | 177 | 6% |

94% is quota, billing, or configuration. Only ~2% is a model actually failing to produce a
valid answer.

Daily rate is volatile — 0% to 32.7%, currently ~10%:

```
 2026-07-25 |    166 |     16 |  9.6      2026-07-18 |    268 |     31 | 11.6
 2026-07-24 |    247 |     26 | 10.5      2026-07-17 |    267 |     32 | 12.0
 2026-07-23 |    269 |     41 | 15.2      2026-07-16 |    267 |     50 | 18.7
 2026-07-22 |    254 |     59 | 23.2      2026-07-15 |    269 |     88 | 32.7
 2026-07-21 |    241 |     74 | 30.7      2026-07-14 |    224 |     42 | 18.8
 2026-07-20 |    244 |     68 | 27.9      2026-07-13 |    191 |      0 |  0.0
 2026-07-19 |    284 |     29 | 10.2      2026-07-12 |    205 |     47 | 22.9
```

---

## Finding 1 — the fallback chain calls speech models (fixable in code)

**`ai-signal-generator/app/graph/llm_chain.py:234-242`**, the cold-cache path:

```python
if not chain:
    raw = await models_registry.raw_models(primary_provider)
    chain = [(primary_provider, m['id']) for m in raw if m['id'] != primary_model]
```

`_raw_groq()` (`models_registry.py:120`) returns **every active model** groq lists, with no
capability filter. That includes speech-to-text and text-to-speech models. With
`_MAX_FALLBACK_ATTEMPTS = 3`, they consume the entire chain:

| provider | model | wasted attempts | error |
|---|---|---:|---|
| groq | `canopylabs/orpheus-arabic-saudi` | 134 | 400 requires terms acceptance |
| groq | `canopylabs/orpheus-v1-english` | 134 | 400 requires terms acceptance |
| groq | `whisper-large-v3-turbo` | 108 | 400 does not support chat completions |
| groq | `whisper-large-v3` | 92 | 400 does not support chat completions |
| groq | `llama-3.1-8b-instant` | 2 | 400 tool schema: missing `size_pct`, `stop_loss_pct`, `take_profit_pct` |

**Impact is measurable and large:**

```
 chain_had_speech_models | cycles | failed | fail_pct
-------------------------+--------+--------+----------
 f                       |    820 |    407 |     49.6
 t                       |    174 |    149 |     85.6
```

A chain containing a speech model fails **85.6%** of the time versus 49.6% without.

The code comment justifies this path as *"an unverified attempt beats a dead cycle."* That
reasoning holds for an unprobed **chat** model. It does not hold for a Whisper model, which
cannot serve a chat completion under any circumstances — it converts a recoverable cycle into a
guaranteed dead one by consuming a slot.

**This is not a startup-only artifact.** It happens 14–18 times *every day*, steadily — so the
probe cache is expiring routinely, not just cold at boot:

```
 2026-07-25 | 14 junk chains / 55 total     2026-07-21 | 18 / 88
 2026-07-24 | 18 / 103                      2026-07-20 | 17 / 84
 2026-07-23 | 18 / 103                      2026-07-19 | 15 / 88
 2026-07-22 | 17 / 100                      2026-07-18 | 17 / 61
```

### Suggested fix

Filter at the source, in `models_registry._raw_groq()` (and the other `_raw_*` functions), so no
consumer — chain, UI model picker, or probe scheduler — ever sees a non-chat model. An id-pattern
denylist (`whisper`, `orpheus`, `tts`, `embed`, `guard`, `speech`) is crude but catches all 468
observed cases. Keep the probe as the strong filter; this only stops the *cold-cache* path from
serving models that are impossible on their face.

Worth pairing with: when falling back cross-provider, prefer a **different** provider before
another model on the same account. The Google chain shows `gemini-2.5-flash` →
`gemini-2.5-pro` → `gemini-2.0-flash` all failing identically with "prepayment credits are
depleted" — three slots spent inside one dead account.

---

## Finding 2 — several provider accounts are out of quota or credit

Quota/billing is 64% of all attempt failures. Current state by account:

| Provider | Symptom | Evidence |
|---|---|---|
| groq | daily token cap exhausted | `TPD: Limit 100000, Used 97862` on `llama-3.3-70b-versatile` |
| google | prepayment credits depleted | `RESOURCE_EXHAUSTED … prepayment credits are depleted` across all gemini models |
| zhipu | balance insufficient | `429 code 1113 — 余额不足或无可用资源包，请充值` |
| openrouter | needs credits | `402 … requires more credits` |
| anthropic | balance exhausted | (caused by today's backtest re-extraction) |

Per-model failure rates follow directly from this:

```
 openrouter | mistralai/mistral-medium-3-5 |    33 |     33 | 100.0
 openrouter | openai/gpt-oss-20b:free      |   374 |    244 |  65.2
 openrouter | tencent/hy3                  |    21 |     12 |  57.1
 openrouter | poolside/laguna-xs-2.1:free  |    53 |     20 |  37.7
 zhipu      | glm-4.5                      |   207 |     76 |  36.7
 groq       | llama-3.3-70b-versatile      |   681 |    207 |  30.4
 google     | gemini-2.5-flash             |   152 |     18 |  11.8
 cerebras   | zai-glm-4.7                  |   150 |      6 |   4.0
 cerebras   | gpt-oss-120b                 |   391 |      1 |   0.3
 zhipu      | glm-4.5-air                  |   552 |      0 |   0.0
 groq       | llama-3.1-8b-instant         |   236 |      0 |   0.0
 openrouter | tencent/hy3:free             |   334 |      0 |   0.0
```

Two things stand out:

- **`mistralai/mistral-medium-3-5` has never once succeeded** — 33/33 failures, all 402. Whatever
  strategy points at it is running blind on that model and should be repointed.
- **The paid variant fails where the free one doesn't**: `tencent/hy3` 57.1% vs `tencent/hy3:free`
  0.0%. Same for `gpt-oss-20b:free` (65.2%) vs cerebras `gpt-oss-120b` (0.3%). The healthy models
  are cerebras `gpt-oss-120b`, zhipu `glm-4.5-air`, groq `llama-3.1-8b-instant` and
  `tencent/hy3:free` — all at or near zero.

---

## Finding 3 — recovery rate, and which strategies are actually degraded

994 cycles needed a fallback; **only 44.1% recovered** (438 recovered, 556 exhausted).

Per strategy, `llm_failed` share of cycles:

```
 bnb-ai-scalper-edbb        |  1161 |    330 | 28.4
 xrp-ai-3844                |   619 |    142 | 22.9
 tao-ai-range-rotation-d257 |   369 |     61 | 16.5
 hype-breakout-da2e         |   219 |     36 | 16.4
 ai-btc-6f8c                |   344 |     40 | 11.6
 eth-ai-34d2                |   385 |      7 |  1.8
 sol-ai-6486                |   350 |      1 |  0.3
```

This tracks the **model**, not the strategy logic — the ranking is a map of which strategy points
at which model. `bnb-ai-scalper-edbb` is effectively running at ~72% duty cycle: roughly three in
ten of its cycles produce no decision at all. `eth-ai-34d2` and `sol-ai-6486` are healthy.

A silent skipped cycle is not neutral for a scalper — it is a missed decision point, and nothing
surfaces it outside this table.

---

## Recommended order

1. **Filter non-chat models out of `_raw_*`** — pure code fix, removes 470 guaranteed-dead
   attempts and should lift the 85.6% failure cohort toward the 49.6% baseline.
2. **Repoint or disable `mistralai/mistral-medium-3-5`** — 100% failure, no value at any price.
3. **Top up or drop the exhausted accounts** — google, zhipu, openrouter, anthropic. Where a
   budget isn't wanted, move those strategies onto the models measured at ~0% failure.
4. **Prefer cross-provider before same-account fallback** — stops three slots being spent inside
   one depleted account.
5. **Alert on the rate.** A 30%-failure day currently looks identical to a quiet day from the
   outside. `llm_failed` share per strategy per day is the obvious metric to surface.
