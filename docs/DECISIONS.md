# Design decisions

One entry per decision: what was decided, what else was considered, why, and the evidence.
Decisions made before M3 are summarised in the table in `CLAUDE.md`.

---

## M3 — Minimal agent end to end (2026-10-19)

### D1. Use the Responses API instead of Chat Completions
- **Decision:** `client.responses.create(...)` with `reasoning={"effort": cfg.reasoning_effort}`.
- **Alternatives:** Chat Completions with `reasoning_effort="none"`.
- **Reason:** `gpt-6-luna` rejects function tools on Chat Completions unless reasoning is off, and `gpt-6.1-sol` needs the Responses API for tool calls. Turning reasoning off would also hide a knob we want to ablate.
- **Evidence:** the first self-test (2026-10-13) failed at step 1 with `400: Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions`.

### D2. `store=False`; replay the encrypted reasoning items (no `include` flag)
- **Decision:** nothing is stored server-side. Reasoning items come back with an `encrypted_content` field, and we send them back unchanged in the history.
- **Alternatives:** (a) `store=True` + `previous_response_id`: less data sent per call, but the trajectory would live on OpenAI's servers, and we would not own the full history; (b) drop the reasoning items: the model would lose its train of thought between tool calls.
- **Reason:** with `store=False` the API cannot look reasoning items up by ID, so they must be passed back as encrypted content.
- **Correction (2026-10-23):** M3 also sent `include=["reasoning.encrypted_content"]`, and this entry claimed it was required. That was wrong. OpenAI's reasoning guide says that in stateless mode (`store=false` or ZDR) reasoning items include `encrypted_content` by default: "The API still accepts the legacy `reasoning.encrypted_content` value in `include` for compatibility, but doesn't require it." The flag was removed from `agent.py`. The M3 result is unaffected, because the flag changed nothing.
- **Evidence:** self-test ran 6 steps with reasoning items replayed and no API errors. A probe on `gpt-6-luna` with `store=False` returned a reasoning item with `encrypted_content` both with and without `include`.

### D3. Replay output items with `model_dump(exclude_none=True)`
- **Decision:** `history.extend(item.model_dump(exclude_none=True) for item in response.output)`.
- **Alternatives:** plain `model_dump()`.
- **Reason:** SDK models contain optional fields set to `None`. Sending them back as JSON `null` risks a 400 for fields the input schema doesn't accept. Leaving them out is always valid.
- **Evidence:** 6 consecutive calls accepted.

### D4. Every function call in the history gets an output, even unexecuted ones
- **Decision:** if a response is `incomplete` (cut off), any function calls in it are answered with "Not executed…" before the nudge. Extra calls beyond the first are answered with "Ignored…".
- **Alternatives:** leave them out of the history.
- **Reason:** the API rejects a history in which a `function_call` has no matching `function_call_output`. History stays append-only, which also keeps the cached prefix.

### D5. Non-interactive env vars set per command (`exec_run(environment=...)`)
- **Decision:** `PAGER=cat GIT_PAGER=cat MANPAGER=cat LESS=-R PIP_PROGRESS_BAR=off TQDM_DISABLE=1` passed on each `exec_run`.
- **Alternatives:** set them once on the container (`containers.run(environment=...)`), or prepend `export ...` to every script.
- **Reason:** they sit next to the other per-command settings (timeout, conda activation) in `Sandbox.run`, and nothing reaches the model's prompt. The container-level option would work equally well.
- **Evidence:** sandbox self-test step 3b prints `PAGER=cat GIT_PAGER=cat`, and `git log` returns without hanging.

### D6. Run output layout
- **Decision:** `runs/<run>/predictions.jsonl` (harness input), `runs/<run>/trajectories/<instance_id>.json`, `runs/<run>/meta.json` (dataset, git commit, full config, finish time). Harness reports go to `results/<run>/`.
- **Reason:** `run.py` (M4) can keep exactly this layout, and resuming becomes "skip IDs that already have a trajectory".
- **Note:** the agent code was committed *before* the run so `meta.json`'s `git_commit` is the exact code that ran.

### D7. OpenAI client timeout 120 s → 300 s
- **Reason:** reasoning calls can take longer than plain completions. A request timeout would be retried (`max_retries=5`) and waste time, or end the task as `error`. The budgets (steps, cost) still bound the run.
