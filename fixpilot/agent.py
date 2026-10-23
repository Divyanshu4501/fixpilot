"""
fixpilot/agent.py

The agent loop: the LLM picks ONE action at a time (run a bash command, or submit),
we run it in the sandbox, show it the result, and repeat until it submits or hits a limit.

v0 = bash-only baseline. Later upgrades (file viewer, edit tool, search) plug in here.

It uses the OpenAI Responses API with reasoning on. We keep the whole conversation
ourselves (`history`) and send all of it on every call (`store=False`: nothing is
saved on OpenAI's side). That history is also what becomes the trajectory.
"""

import json
import time
from dataclasses import asdict, dataclass, field

from openai import OpenAI

from fixpilot.sandbox import CommandResult, Sandbox
from fixpilot.tasks import assert_visible_only, visible_task

# USD per 1M tokens: (uncached input, cached input, output). Checked Oct 2026.
# Uncached input is priced at the cache-WRITE rate to be conservative.
# Reasoning tokens are billed as output tokens.
PRICES = {
    "gpt-6-luna": (0.125, 0.01, 0.50),
    "gpt-6.1-sol": (2.50, 0.10, 10.00),
}

SYSTEM_PROMPT = """You are an autonomous software engineer. You fix a GitHub issue in a Python repository by running shell commands in a sandbox.

Environment:
- The repository is checked out at /testbed. Every command runs in a fresh bash shell that starts in /testbed, so `cd` and environment variables do not carry over between commands. Chain commands with && when needed (for example: cd django && ls).
- The repository's Python environment is already active. There is no internet access.
- Commands must be non-interactive: no editors (vim, nano), no pagers (less), nothing that waits for input. Commands that run too long are killed.
- Long outputs are cut down to their start and end. Prefer targeted commands: grep -n, head, tail, sed -n '10,40p' FILE.

How to work:
1. Find the code related to the issue.
2. Write a small script that reproduces the bug, and run it to see it fail.
3. Edit the source code to fix the root cause. Edit files with commands such as sed, or with a short Python script that rewrites the file.
4. Run your reproduction script again to confirm the fix, and run related existing tests if you can.
5. Call the submit tool.

Rules:
- Make exactly one tool call per turn.
- Do not modify existing test files.
- Keep the fix minimal and focused on the issue.
- Your submission is the set of changes you made to files already tracked by git. New files you create (like a reproduction script) are not included, so the fix itself must be in existing source files."""

USER_TEMPLATE = """Here is the GitHub issue to resolve:

<issue>
{issue}
</issue>

The repository is at /testbed. Start by exploring it."""

NUDGE = "You did not call a tool. Reply with exactly one tool call: bash to run a command, or submit when you are done."
INCOMPLETE = "Not executed: your response was cut off before it finished."
IGNORED = "Ignored: only one tool call per turn is allowed. Only your first tool call was executed."

# Responses API tool format: flat (no nested "function" key). `strict: True` makes the
# API guarantee the arguments match the schema; strict mode needs every property listed
# in "required" and "additionalProperties": False.
TOOLS = [
    {
        "type": "function",
        "name": "bash",
        "description": "Run one shell command in the repository sandbox. Each call starts a fresh shell in /testbed.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The shell command to run."}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "submit",
        "description": "Call this when you have finished fixing the issue. Your edits to the repository are the submission.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
]


@dataclass
class AgentConfig:
    model: str = "gpt-6-luna"
    reasoning_effort: str = "medium"  # how hard the model thinks before each action
    max_steps: int = 30               # stop after this many LLM turns
    max_cost_usd: float = 0.50        # stop once one task has cost this much
    command_timeout: int = 180        # seconds before a single command is killed
    max_output_chars: int = 10_000    # longer command output is cut to its first + last 5,000 chars
    verbose: bool = True              # print each step live


@dataclass
class AgentResult:
    instance_id: str
    model: str
    reasoning_effort: str
    exit_reason: str                  # submitted | step_limit | cost_limit | error
    steps: int
    cost_usd: float
    duration_s: float
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    tokens_reasoning: int             # part of tokens_out
    patch: str
    error: str | None = None
    trajectory: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StepUsage:
    """Tokens and dollars for one API call."""
    cost_usd: float
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    tokens_reasoning: int


# ---------- helpers ----------

def format_observation(result: CommandResult, max_chars: int) -> str:
    """Turn a command result into the text the model sees, trimming huge outputs."""
    out = result.output
    if len(out) > max_chars:
        half = max_chars // 2
        cut = len(out) - max_chars
        out = out[:half] + f"\n[... {cut:,} characters cut ...]\n" + out[-half:]
    note = " (timed out: the command was killed)" if result.timed_out else ""
    return f"exit code: {result.exit_code}{note}\noutput:\n{out if out.strip() else '(no output)'}"


def step_cost(model: str, usage) -> StepUsage:
    """Dollar cost and token counts of one Responses API call."""
    if model not in PRICES:
        raise ValueError(f"No prices for model {model!r}. Add it to PRICES in agent.py so the cost cap works.")
    if usage is None:
        raise RuntimeError("The API returned no token usage, so cost can't be tracked.")
    p_in, p_cached, p_out = PRICES[model]
    t_in, t_out = usage.input_tokens, usage.output_tokens
    t_cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
    t_reasoning = getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0
    cost = ((t_in - t_cached) * p_in + t_cached * p_cached + t_out * p_out) / 1_000_000
    return StepUsage(cost, t_in, t_cached, t_out, t_reasoning)


def message_text(output: list) -> str | None:
    """Any plain text the model wrote this turn (besides its tool call)."""
    parts = [c.text for item in output if item.type == "message"
             for c in item.content if getattr(c, "type", None) == "output_text"]
    return "\n".join(parts) or None


def call_output(call_id: str, output: str) -> dict:
    """The history item that answers one function call."""
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def execute_call(call, sandbox: Sandbox, config: AgentConfig, record: dict) -> str:
    """Run one function call from the model, fill in the step record, return the observation."""
    record["tool"] = call.name
    if call.name == "submit":
        return "Submitted."
    if call.name != "bash":
        return f"Error: unknown tool {call.name!r}. Use bash or submit."
    try:
        command = json.loads(call.arguments or "{}")["command"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return 'Error: bash needs JSON arguments like {"command": "ls"}.'
    result = sandbox.run(command, timeout=config.command_timeout)
    record.update(command=command, exit_code=result.exit_code, duration_s=result.duration)
    return format_observation(result, config.max_output_chars)


def _print_step(r: dict) -> None:
    if r["tool"] == "bash" and r["command"] is not None:
        cmd = r["command"].replace("\n", " \\n ")
        what = f"bash [exit {r['exit_code']}]: {cmd[:100]}"
    else:
        what = r["tool"] or "(no tool call)"
    print(f"step {r['step']:>2} | ${r['cost_usd']:.4f} | think {r['tokens_reasoning']:>5} | {what}", flush=True)


# ---------- the loop ----------

def run_agent(task: dict, sandbox: Sandbox, config: AgentConfig, client: OpenAI | None = None) -> AgentResult:
    """Run the agent on one task inside an already-started sandbox."""
    assert_visible_only(task)  # leakage guard: the task may carry visible fields only
    client = client or OpenAI(max_retries=5, timeout=300)
    history: list = [{"role": "user", "content": USER_TEMPLATE.format(issue=task["problem_statement"])}]
    trajectory = []
    totals = StepUsage(0.0, 0, 0, 0, 0)
    step = 0
    exit_reason, error = "step_limit", None
    started = time.monotonic()

    try:
        while True:
            if step >= config.max_steps:
                exit_reason = "step_limit"
                break
            if totals.cost_usd >= config.max_cost_usd:
                exit_reason = "cost_limit"
                break
            step += 1

            response = client.responses.create(
                model=config.model,
                instructions=SYSTEM_PROMPT,           # same on every call -> cacheable prefix
                input=history,
                tools=TOOLS,
                parallel_tool_calls=False,            # at most one tool call per turn
                store=False,                          # nothing kept server-side; we replay history
                reasoning={"effort": config.reasoning_effort},  # store=False returns it encrypted by default
            )
            usage = step_cost(config.model, response.usage)
            for name in ("cost_usd", "tokens_in", "tokens_cached", "tokens_out", "tokens_reasoning"):
                setattr(totals, name, getattr(totals, name) + getattr(usage, name))

            # Every output item goes back into the history, including the (encrypted)
            # reasoning, so the model keeps its train of thought across tool calls.
            history.extend(item.model_dump(exclude_none=True) for item in response.output)

            record = {"step": step, "thought": message_text(response.output), "tool": None, "command": None,
                      "exit_code": None, "output": None, "duration_s": None, "status": response.status,
                      **{k: v for k, v in asdict(usage).items() if k != "cost_usd"},
                      "cost_usd": round(usage.cost_usd, 6)}

            calls = [item for item in response.output if item.type == "function_call"]
            if response.status == "incomplete" or not calls:
                # The API needs an answer for every function call in the history, even unexecuted ones.
                history.extend(call_output(c.call_id, INCOMPLETE) for c in calls)
                history.append({"role": "user", "content": NUDGE})
                record["output"] = f"(no usable tool call, status={response.status}; nudged)"
            else:
                first, *rest = calls
                observation = execute_call(first, sandbox, config, record)
                history.append(call_output(first.call_id, observation))
                history.extend(call_output(c.call_id, IGNORED) for c in rest)
                record["output"] = observation
                if first.name == "submit":
                    exit_reason = "submitted"

            trajectory.append(record)
            if config.verbose:
                _print_step(record)
            if exit_reason == "submitted":
                break

    except Exception as e:
        exit_reason = "error"
        error = f"{type(e).__name__}: {e}"

    patch = ""
    try:
        patch = sandbox.diff()  # taken even after a limit: partial work can still be a valid fix
    except Exception as e:
        error = error or f"diff failed: {type(e).__name__}: {e}"

    return AgentResult(
        instance_id=task["instance_id"], model=config.model, reasoning_effort=config.reasoning_effort,
        exit_reason=exit_reason, steps=step, cost_usd=round(totals.cost_usd, 6),
        duration_s=round(time.monotonic() - started, 1), tokens_in=totals.tokens_in,
        tokens_cached=totals.tokens_cached, tokens_out=totals.tokens_out,
        tokens_reasoning=totals.tokens_reasoning, patch=patch, error=error, trajectory=trajectory,
    )


# ---------- self-test: `uv run python -m fixpilot.agent` ----------

def _git_commit() -> str:
    import subprocess
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


if __name__ == "__main__":
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from datasets import load_dataset
    from dotenv import load_dotenv

    load_dotenv()
    INSTANCE = "django__django-11099"
    DATASET = "SWE-bench/SWE-bench_Lite"
    ds = load_dataset(DATASET, split="test")
    row = next(r for r in ds if r["instance_id"] == INSTANCE)
    task = visible_task(row)  # visible fields ONLY: no patch, tests or hints

    config = AgentConfig(model=os.environ.get("FIXPILOT_MODEL", "gpt-6-luna"))
    print(f"Running {config.model} (effort {config.reasoning_effort}) on {INSTANCE} "
          f"(max {config.max_steps} steps, max ${config.max_cost_usd})\n")

    with Sandbox(task["image"], default_timeout=config.command_timeout) as sb:
        result = run_agent(task, sb, config)

    print(f"\nexit reason: {result.exit_reason}")
    print(f"steps: {result.steps} | cost: ${result.cost_usd:.4f} | time: {result.duration_s}s "
          f"| reasoning tokens: {result.tokens_reasoning:,}")
    if result.error:
        print("error:", result.error)
    print("\n--- patch ---")
    print(result.patch or "(empty patch)")

    run_dir = Path("runs/selftest")
    (run_dir / "trajectories").mkdir(parents=True, exist_ok=True)
    (run_dir / "trajectories" / f"{INSTANCE}.json").write_text(json.dumps(result.to_dict(), indent=2))
    prediction = {"instance_id": INSTANCE, "model_name_or_path": f"fixpilot-{config.model}",
                  "model_patch": result.patch}
    (run_dir / "predictions.jsonl").write_text(json.dumps(prediction) + "\n")
    meta = {"dataset": DATASET, "git_commit": _git_commit(),
            "finished_at": datetime.now(timezone.utc).isoformat(), "config": asdict(config)}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nsaved predictions, trajectory and meta to {run_dir}/")
