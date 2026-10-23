# CLAUDE.md — FixPilot

## What this project is

FixPilot is an issue-to-PR coding agent built **from scratch** (no agent frameworks). Given a GitHub issue, it explores the repo inside a Docker sandbox, edits code, and produces a patch (benchmark mode) or a pull request (GitHub mode). It is measured on **SWE-bench** with the **official harness**.

It is a portfolio project for Applied GenAI / LLM-engineer internship applications. The point is to show a well-engineered, honestly measured **agent harness**: the effort goes into the loop, tools, sandbox, budgets and evaluation, not into the model.

**End state (deliverables):**
- Headline number: resolve rate on 50 held-out tasks, with a 95% confidence interval, cost per task, cost per resolved task, median steps.
- Ablation table: each upgrade measured one at a time on a 20-task dev set.
- 2–3 real pull requests opened by FixPilot on the owner's own repos (GitHub mode).
- Static trace-viewer page that replays trajectories.
- README with architecture diagram and results table, a 90-second Loom, and CI.

## Who you are working with

- Owner: **Divyanshu** (GitHub `Divyanshu4501`), 3rd-year B.Tech AI/ML student, **Python developer**. He will study and rewrite this code after you write it, so clarity and explanation matter as much as working code.
- **After every task, explain what you did and why** in plain, beginner-friendly language: what each new file/function does, what each non-obvious line does, and which design decision it implements. Explain any technical term in one or two lines.
- **Keep a learning record:** for each milestone, write `docs/notes/M<n>.md` covering what was built, how it works, the decisions made (with alternatives considered), and what surprised you. Append every design decision to `docs/DECISIONS.md` (decision, alternatives, reason, evidence).
- **Work one milestone at a time. At the end of each milestone, stop**, summarise results (numbers, failures, cost spent), and wait for his go-ahead.
- **Small, separate commits** with clear messages (code and results in separate commits). Do **not** push unless asked. Never use the `gh` CLI.
- Run everything with `uv run ...` from the repo root.
- Python only. Type hints, docstrings, small functions. No LangChain, LiteLLM, agent SDKs or other frameworks: the harness is deliberately hand-built.

## Environment

- Laptop: Ubuntu, x86_64, 12 cores, 30 GB RAM, **no NVIDIA GPU**, ~115 GB free disk (check `df -h /` before big runs).
- Repo: `~/Desktop/internship/FixPilot` → `github.com/Divyanshu4501/fixpilot` (branch `main`).
- Python 3.11 (`.python-version`), managed by **uv**; venv prompt name `fixpilot`.
- Key libraries: `swebench==5.0.2`, `datasets`, `docker` (Python SDK), `openai`, `python-dotenv`.
- Docker works without sudo. SWE-bench images are pulled from Docker Hub (`swebench/sweb.eval.x86_64.<id with __ → _1776_>:latest`), 1–4 GB each.
- `.env` (gitignored, chmod 600): `OPENAI_API_KEY`, `FIXPILOT_MODEL=gpt-6-luna`. **Never print, cat, log or commit secrets. Never pass them into a container.**

## Current state (2026-10-23)

- **Done:** M0 setup, M1 sandbox, M2 scorer verified (gold patch for `django__django-11099` → resolved in 21 s), M3 minimal agent end to end (11099 resolved, 6 steps, $0.0009).
- **In progress:** M4 (harness core + baseline on SWE-bench Verified).
- `fixpilot/sandbox.py` — **working and tested.** Starts a container (`sleep infinity`, `network_mode="none"`), runs commands with `timeout -k 5 <t> bash -c` (exit 124 = timeout), activates the `testbed` conda env per command, records `git rev-parse HEAD` as base commit, returns `git -c core.fileMode=false diff <base_commit>`, cleans up via context manager.
- `fixpilot/agent.py` — v0 loop on the **Responses API** (reasoning on, `store=False`, full-history replay). Self-test: `uv run python -m fixpilot.agent` (runs `django__django-11099`).
- `scratch/` — Divyanshu's own practice versions of files. Gitignored. **Do not edit or import from it.**
- Gitignored: `.venv/`, `.env`, `__pycache__/`, `logs/`, `/gold.*.json`, `test.ipynb`, `.ipynb_checkpoints/`, `scratch/`.
- Check `git status` first and commit in small pieces.

## Non-negotiable rules

1. **No leakage.** The agent may only receive `instance_id`, `repo`, `base_commit`, `problem_statement`, `image`. Never put `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `hints_text`, or `eval_script` into any prompt, tool output, or sandbox. Enforce this in the task loader and test it.
2. **Only the official SWE-bench harness decides "resolved".** Never self-grade.
3. **One change at a time.** Each upgrade is a config flag, measured on the same dev set against the previous version.
4. **The 50-task test set is run once**, on the final configuration only. Never tune on it.
5. **Every run has budgets:** max steps, max cost, per-command timeout.
6. **Sandbox has no network** in benchmark mode. Secrets never enter containers.
7. **Real PRs only on Divyanshu's own repos or forks.** FixPilot never merges.
8. **Ask before** any run expected to cost more than **$2**, pull more than ~**10 GB** of images, or delete anything outside this repo. Never run `docker system prune -a` (other projects' images live on this machine); remove SWE-bench images individually.
9. Reproducibility: pin versions (`uv.lock`), commit task-ID lists, save predictions/reports/trajectories, record model + reasoning effort + git commit for every run.

## Known gotchas (already hit — don't rediscover)

- **swebench 5.0.2 needs the `SWE-bench/...` datasets** (`SWE-bench/SWE-bench_Lite`, `SWE-bench/SWE-bench_Verified`; they have an `image` field). `princeton-nlp/SWE-bench_Lite` fails with `KeyError: 'image'`. The `--namespace` flag no longer exists. In this dataset `FAIL_TO_PASS`/`PASS_TO_PASS` are real lists.
- **The harness caches results by `run_id`.** Every new attempt needs a new `run_id`.
- **Container `HEAD` ≠ dataset `base_commit`** (the image adds its own commit). Expected. Always diff against the HEAD recorded at container start.
- With `store=False`, reasoning items include `encrypted_content` **by default**; `include=["reasoning.encrypted_content"]` is a legacy no-op (verified 2026-10-23). Don't add it back.
- `load_dotenv()` with no path searches from the *calling file's* directory, not the cwd. Scripts outside the repo need an explicit path.
- **`gpt-6-luna` on Chat Completions rejects function tools unless `reasoning_effort="none"`.** Decision: **use the Responses API with reasoning on.** `gpt-6.1-sol` requires the Responses API for tool calling and does not support `none`/`minimal` effort.
- Each sandbox command is a **fresh shell**: `cd` and env vars don't persist (the system prompt tells the model this).
- `git diff` excludes **untracked files** (on purpose: scratch repro scripts stay out of the patch).
- Some `FAIL_TO_PASS` lists in Lite contain unrelated tests (dataset noise; e.g. 11099 includes `test_help_text (UserAttributeSimilarityValidatorTest)`).
- `uv` warns that `soupsieve 3.0.1` is yanked: harmless on Python 3.11.14.

## Architecture

```
dataset row ─► tasks.py (visible fields only) ─► runner (parallel tasks)
                                                   │
                     ┌──────────── agent loop (laptop) ────────────┐
                     │ LLM client (Responses API) → one tool call  │
                     │ tools: bash, submit (+ upgrades later)      │
                     │ budgets · cost tracking · trajectory        │
                     └───────────────┬─────────────────────────────┘
                                     │ command / output
                              sandbox.py ─► Docker container (/testbed, no network)
                                     │
                         git diff = patch ─► runs/<run>/predictions.jsonl
                                     │
                  official harness ─► results/<run>/ report ─► metrics, ablation table
```

Planned layout:
```
fixpilot/
  sandbox.py      # container lifecycle, run, diff  (done)
  agent.py        # loop, tools, prompts, budgets, cost, trajectory
  tasks.py        # load tasks (visible fields only), dev/test selection
  run.py          # CLI: run many tasks in parallel → predictions + trajectories, resumable
  analyze.py      # aggregate harness reports + trajectories → metrics, CIs, failure categories
  github_mode.py  # issue URL → sandbox → agent → branch + PR (M7)
prompts/          # system prompt(s) as files if they grow
data/             # dev_ids.txt, test_ids.txt (committed)
runs/             # trajectories + predictions (large files gitignored; small summaries ok)
results/          # harness reports + ablation table (committed)
viewer/           # static trace viewer (M8)
tests/            # pytest
docs/notes/, docs/DECISIONS.md
```

## Design decisions already made (don't change without asking)

| Decision | Why |
|---|---|
| Docker container per task, `network_mode="none"` | Isolation; stops the agent from downloading the real fix |
| Fresh shell per command (`exec_run`), not a persistent session | Persistent shells are flaky (end-of-command detection, dead sessions); same choice as mini-swe-agent |
| Timeout inside the container via coreutils `timeout` | Kills hung commands reliably; exit code 124 |
| Diff against base commit recorded at container start | Catches staged/committed changes too |
| Tool calling with two tools: `bash(command)`, `submit()` | Structured actions, no text parsing; bash-only baseline first |
| **Responses API**, `store=False`, manual full-history replay | Reasoning on; we own the full history (it *is* the trajectory); nothing stored server-side |
| Pass back **all** `response.output` items each step (incl. encrypted reasoning) | Required for reasoning models to keep their train of thought across tool calls |
| `instructions=SYSTEM_PROMPT` on every call; append-only history | Stable prefix → prompt caching |
| Flat function tools with `strict: True`; `parallel_tool_calls=False` | Schema-valid arguments; exactly zero or one tool call per turn |
| `reasoning={"effort": cfg.reasoning_effort}`, default `"medium"` | Model default for agentic coding; effort is an ablation knob |
| Model name from `.env` (`FIXPILOT_MODEL`); price table in code; **refuse unknown models** | Cost cap must never silently treat a model as free |
| Budgets: 30 steps, $0.50/task, 180 s/command; output trimmed to first+last 5,000 chars | Bounded cost and context |
| Patch taken even after a limit is hit; exit reasons `submitted / step_limit / cost_limit / error` | Partial work can still resolve; clean failure accounting |
| One trajectory JSON per task | Debugging, failure analysis, trace viewer |

**Prices (USD per 1M tokens, checked Oct 2026, developers.openai.com/api/docs/pricing):** `gpt-6-luna` input 0.10 / cached 0.01 / cache write 0.125 / output 0.50; `gpt-6.1-sol` input 2.00 / cached 0.10 / cache write 2.50 / output 10.00. Price uncached input at the cache-write rate (conservative). Reasoning tokens are billed as output.

**Responses API details to follow:**
- Call: `client.responses.create(model=..., instructions=SYSTEM_PROMPT, input=history, tools=TOOLS, parallel_tool_calls=False, store=False, reasoning={"effort": ...})`.
- `history` starts as `[{"role": "user", "content": <issue prompt>}]`. After each response: `history.extend(item.model_dump() for item in response.output)` (if the API rejects null fields, use `model_dump(exclude_none=True)`).
- For each `function_call` item (`name`, `arguments`, `call_id`): execute, then append `{"type": "function_call_output", "call_id": ..., "output": observation}`.
- No function call, or `response.status == "incomplete"` → append a nudge user message; counts as a step.
- Usage: `usage.input_tokens`, `usage.input_tokens_details.cached_tokens`, `usage.output_tokens`, `usage.output_tokens_details.reasoning_tokens`. Log reasoning tokens per step.

## Decided (2026-10-23)

- **Dataset: SWE-bench Verified** (`SWE-bench/SWE-bench_Verified`, test split, 500 rows; every row has an `image` field matching the Docker Hub naming rule). Used for **both** the 20-task dev set and the 50-task test set: fixed seed, proportional mix of repos, no overlap, IDs saved in `data/`. The dataset name is a setting (default Verified), so Lite still works (the M3 self-test used Lite). Verified rows also have a `difficulty` field: selection may use it, the agent must never see it.
- **Baseline reasoning effort: `medium`.** `low` and `high` are M5 ablation candidates.

## Open decisions — ask Divyanshu, don't decide

- Number of parallel workers (start at 4; limited by RAM, disk and API rate limits).
- Order of M5 upgrades, and which becomes the "non-obvious decision".
- GitHub-mode target repo (cudagrad Python-side issues with CPU-only tests, or a small purpose-built repo).

## Milestones (work in order, stop after each)

**M3 — Minimal agent end to end**
- Migrate `agent.py` to the Responses API as specified above. Add `reasoning_tokens` to step records and totals.
- Add non-interactive env vars to the sandbox (`PAGER=cat`, `GIT_PAGER=cat`, `MANPAGER=cat`, `LESS=-R`, `PIP_PROGRESS_BAR=off`, `TQDM_DISABLE=1`).
- Run the self-test on `django__django-11099`, write `runs/<run>/predictions.jsonl` (`instance_id`, `model_name_or_path`, `model_patch`), score it with the harness.
- *Done when:* a patch is produced, scored, and the trajectory file shows every step.

**M4 — Harness core + baseline**
- `tasks.py`: load visible fields only; deterministic dev (20) / test (50) selection from the chosen dataset, fixed seed, proportional mix of repos, no overlap; save to `data/`.
- `run.py`: `ThreadPoolExecutor`, resumable (skip finished tasks), per-task trajectory + one predictions file per run, run metadata (model, effort, git commit, config).
- Image management: pull per batch, remove after scoring.
- `analyze.py`: resolve rate with **Wilson 95% CI** (`statsmodels.stats.proportion.proportion_confint(method="wilson")`), cost per task, cost per resolved, median steps, patch-applied rate, empty-patch rate, infra failures, failure categories.
- `tests/`: pytest unit tests that need no Docker or API (fake client/sandbox): output trimming, cost calculation, step/cost limits, nudge on no tool call, **leakage test** (prompt never contains gold patch or test names), task loader returns visible fields only. Mark Docker tests as integration (skipped in CI).
- *Done when:* the 20-task dev set runs unattended and the baseline row exists in `results/ablation.md`.

**M5 — Upgrades, one at a time** (each a config flag, each a new ablation row on the same dev set)
Candidates: reasoning effort `low` and `high` (vs baseline `medium`), file viewer (line-numbered windows), edit tool with Python syntax check that rejects broken edits, code search / repo map, reproduce-first rule, test feedback before submit, submit checks (non-empty, no test-file edits, applies cleanly), reviewer agent (second model reviews issue + diff + test output), best-of-N with self-selection. Also: same best config with `gpt-6.1-sol` on dev to compare harness gains vs model gains.
- *Done when:* 2–4 upgrades are measured and the ablation table is filled.

**M6 — Final evaluation**: best config on the 50-task test set, once. Headline numbers with CI, failure breakdown, written up in `results/final.md`.

**M7 — GitHub mode**: issue URL → read issue (PyGithub) → sandbox from the target repo's Dockerfile → agent → repo tests → branch `fixpilot/issue-<n>` → PR with summary, tests run, steps, cost, trajectory link. Fine-grained token in `.env`; **the token stays outside the sandbox** (our code does commit/push). Never merge.

**M8 — Ship**: GitHub Actions CI with uv + pytest; static trace viewer in `viewer/`; README (architecture diagram, results table with CIs, decisions, how to run); notes for the Loom.

## Commands

```bash
uv run python -m fixpilot.sandbox                 # sandbox self-test
uv run python -m fixpilot.agent                   # agent self-test on django__django-11099
uv run pytest                                     # unit tests (no Docker / API)
uv run pytest -m integration                      # Docker tests (needs the 11099 image)

uv run python -m fixpilot.images data/dev_ids.txt # estimate image download, no pull
uv run python -m fixpilot.run --run-name <name> --ids-file data/dev_ids.txt   # run + score, resumable
uv run python -m fixpilot.analyze --run-name <name> --ablation "<label>"      # metrics + ablation row

# score predictions with the official harness (new run_id every time)
uv run python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Verified \
  --predictions_path runs/<run_name>/predictions.jsonl \
  --max_workers 4 \
  --run_id <unique_run_id> \
  --report_dir results/<run_name>

docker ps -a | grep fixpilot                      # must be empty after runs
docker images | grep sweb                         # SWE-bench images on disk
df -h /                                           # check disk before big runs
```

Harness logs go to `logs/run_evaluation/<run_id>/` (gitignored).

## References

- Study guide and plan: the owner's FixPilot docs (Plan & Architecture; Study Guide by Milestone).
- minimal-agent.com (from-scratch agent tutorial) · github.com/SWE-agent/mini-swe-agent (closest reference design)
- OpenAI docs: function calling, reasoning models, conversation state (developers.openai.com/api/docs/guides/…)
- SWE-bench evaluation guide: github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md
- SWE-agent paper (arXiv 2405.15793) · Agentless (arXiv 2407.01489) · Evan Miller, "Adding Error Bars to Evals" (arXiv 2411.00640)