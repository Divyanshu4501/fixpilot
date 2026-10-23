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

---

## M4 — Harness core + baseline (2026-10-23)

### D8. SWE-bench Verified for both dev and test (Divyanshu's decision)
- **Decision:** `SWE-bench/SWE-bench_Verified`, test split, is the default dataset (`tasks.DEFAULT_DATASET`). `--dataset` switches it, so Lite still works.
- **Alternatives:** Lite (300 tasks, not human-checked).
- **Reason:** human-checked tasks (a failure is the agent's, not the dataset's), comparable with the bash-only leaderboard, and difficulty labels.
- **Evidence:** loaded 2026-10-23: 500 rows, 0 without `image`, and every `image` follows `swebench/sweb.eval.x86_64.<id with __→_1776_>:latest`.

### D9. Dev/test selection: proportional by repo, largest-remainder, fixed seed
- **Decision:** dev (20) and test (50) are each split across repos in proportion to repo size, using the largest-remainder method: everyone gets the floor, and leftover slots go to the biggest fractional parts, ties broken by name. Within each repo the IDs are sorted, shuffled with `random.Random(20261023)`, the first ones go to test and the next ones to dev. `django__django-11099` is excluded (it was used while developing in M3). `tasks.py` refuses to overwrite existing ID files.
- **Alternatives:** (a) plain random sample: small repos might be missing or overrepresented; (b) also stratify by difficulty: a reasonable choice, but not what was asked; (c) one allocation of 70, split afterwards: dev and test could end up with different mixes.
- **Reason:** deterministic, order-independent (sorting before shuffling), and both sets have the dataset's repo mix.
- **Known quirk:** by chance the dev set has 0 tasks above "15 min – 1 hour" (pool: 9%; test: 5/50). P(0 of 20) ≈ 0.91^20 ≈ 15%. **Not re-drawn**: picking a seed after seeing the result is tuning the split. Consequence: dev resolve rates will likely be a bit higher than test rates; compare configurations on dev, and report the test number.
- **Evidence:** `data/split_info.json`. Unit tests check sizes, no overlap, the repo mix, determinism and exclusion.

### D10. Leakage defence = allowlist + guard + tests
- **Decision:** `tasks.visible_task` copies only the 5 `VISIBLE_FIELDS` (allowlist). `run_agent` calls `assert_visible_only(task)` and refuses anything else. `tests/test_leakage.py` plants `SECRET_*` markers in every hidden field and checks that none appear in any request sent to the (fake) model or in any sandbox command.
- **Alternatives:** a blocklist that removes known hidden fields.
- **Reason:** a blocklist silently leaks fields that get added later. Verified added `difficulty`, which isn't on CLAUDE.md's list. An allowlist can't leak a field it doesn't know about.
- **Evidence:** the leakage tests fail when the guard in `run_agent` is removed (checked by deliberately breaking it).

### D11. Images: pull per batch, score per batch, remove only what we pulled
- **Decision:** `run.py` works in batches (default 5): pull → agent → harness → `docker rmi` of the images *this run pulled*. Images already on disk are never removed, and nothing is pruned. Before each pull, the run stops if less than `--min-free-gb` (30) is free. `images.py` can estimate the download from Docker Hub manifests without pulling.
- **Alternatives:** (a) pull everything first: 25 GB+ on disk at once for dev, more for test; (b) score once at the end: the images would have to stay or be pulled twice.
- **Trade-off:** layers shared between batches are downloaded again. `--keep-images` turns removal off (worth it for the dev set, which is run again for every M5 ablation).
- **Evidence:** dev estimate: 25.0 GB summed / 12.1 GB after shared layers (compressed).

### D12. Resumable runs and locked settings
- **Decision:** one trajectory file per task is the "done" marker. Tasks with `exit_reason == "error"` are retried on resume (infra problems); scored tasks aren't re-scored. `meta.json` stores the settings (dataset, ids file, agent config), and a resume with different settings is refused. All writes are atomic (`tmp` + `os.replace`). `predictions.jsonl` is rebuilt from the trajectories each batch.
- **Reason:** a crash or Ctrl-C can't corrupt a run or mix two configurations in one result.

### D13. Refuse to run with uncommitted code
- **Decision:** `run.py` stops if `fixpilot/`, `pyproject.toml` or `uv.lock` have uncommitted changes, unless `--allow-dirty` is passed.
- **Reason:** rule 9. The `git_commit` in `meta.json` must be the code that actually ran.

### D14. Run-level budget (`--max-total-cost`, default $2)
- **Decision:** no new task starts once the run has spent the cap; already-running tasks finish. It can therefore overshoot by up to `workers × $0.50` (per-task cap). Resumed runs count earlier spending.
- **Reason:** rule 8 ($2 needs approval) enforced in code, not just by intention.

### D15. Scoring wrapper (`score.py`)
- **Decision:** call the official harness as a subprocess with a unique `run_id` per batch (`<run>.b<k>.<timestamp>`), then save per-instance status to `results/<run>/instances.jsonl`: `resolved | unresolved | empty_patch | apply_failed | harness_error`, infra flag, and pass/fail **counts** of hidden tests. Empty patches aren't sent to the harness: it can't run them, and they're recorded as `empty_patch`.
- **Reason:** harness logs are gitignored and a failed patch apply leaves no `report.json`. Reading `>>>>> Patch Apply Failed` from `run_instance.log` at scoring time keeps that information in a committed file. "Resolved" still comes only from the harness's `report.json`.

### D16. Honest denominator
- **Decision:** resolve rate = resolved / **all planned tasks**. Infra failures, unscored, never-run and limit-hit tasks all count as not resolved, and are also reported separately.
- **Alternatives:** drop infra failures from the denominator (makes the number look better).
- **Reason:** a reader of the headline number shouldn't have to guess what was left out.

### D17. Failure categories (automatic, first match wins)
`resolved → not_run → infra_error → not_scored → empty_patch → apply_failed → broke_tests (PASS_TO_PASS failures) → wrong_fix`. Plus flags: exit reason, and "patch touches test files" (a heuristic: inside `tests/`/`testing/`, or `test_*.py`, `*_test.py`, `conftest.py`; not `test/`, because `django/test/` is library code). Manual reading of trajectories comes on top in the notes.

### D18. Tests without Docker or API by default
- **Decision:** fake client + fake sandbox for all unit tests. Docker tests are marked `integration` and deselected by `addopts = "-m 'not integration'"`. Run them with `uv run pytest -m integration`.
- **Reason:** CI (M8) has no Docker images or API key. Tests must be fast and free.
