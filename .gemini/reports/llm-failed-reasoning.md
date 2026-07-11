# ai_signal_log.reasoning was always NULL on llm_failed

## Request
On the AI signal log, `llm_failed` rows had no reasoning, unlike every other action/
rejection which carries the LLM's own text. Add the actual provider/parse failure reason.

## Root cause
`node_analyze.py` had two failure branches (structured-output parse failure, and any
exception from the provider call — timeout, network error, auth, rate limit) that both
logged the error but discarded it, returning `llm_signal: None` with no error string in
state. `node_dispatch.py` sources `reasoning` from `signal.get('reasoning')`, which is
`{}.get('reasoning')` → `None` whenever `llm_signal` is `None`.

## Fix
- `graph/state.py`: added `llm_error: Optional[str]` to `AgentState`.
- `node_analyze.py`: both failure branches now build `llm_error =
  "[{provider}/{model}] ..."` (parse-error + raw response preview, or exception
  type+message) and return it in state.
- `node_dispatch.py`: `reasoning = signal.get('reasoning') or state.get('llm_error')`.

## Verification
Live, against the running ai-signal-generator container (real DB, real graph nodes),
simulating a provider outage:
```
$ docker compose exec ai-signal-generator python3 -c "... node_analyze -> node_guard -> node_dispatch, LLM raises RuntimeError('Connection error: 503 Service Unavailable from provider') ..."
llm_signal: None
llm_error: [google/gemini-2.0-flash] RuntimeError: Connection error: 503 Service Unavailable from provider
gate_passed: False
gate_rejection_reason: llm_failed
signal_log_id: 1053

$ docker compose exec postgres psql -U matp -d matp -c "SELECT id, gate_rejection_reason, reasoning FROM ai_signal_log WHERE id=1053;"
 id  | gate_rejection_reason |                    reasoning
------+-----------------------+-------------------------------------------------------------------------------------------------
 1053 | llm_failed            | [google/gemini-2.0-flash] RuntimeError: Connection error: 503 Service Unavailable from provider
```
Test row deleted after verification. Deployed via `./scripts/redeploy.sh ai-signal-generator`
(health check green before running the test).

No existing test suite covers `node_analyze`/`node_dispatch` (grepped — none found), so no
regression risk from existing tests; verified live instead.
