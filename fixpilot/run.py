"""
fixpilot/run.py

Runs the agent on many tasks in parallel, then scores them with the official harness.

    uv run python -m fixpilot.run --run-name baseline --ids-file data/dev_ids.txt

What it does, batch by batch (default 5 tasks per batch):
  1. check free disk, pull the batch's Docker images
  2. run the agent on each task (ThreadPoolExecutor, --workers at a time)
  3. rebuild runs/<run>/predictions.jsonl from all trajectories
  4. score the batch with the harness -> results/<run>/instances.jsonl
  5. remove the images this run pulled (unless --keep-images)

Resumable: run the same command again and finished tasks are skipped. A task is
"finished" when its trajectory exists with an exit reason other than "error" (errors
are infrastructure problems, so they are retried). Scored tasks are not re-scored.

Output layout:
  runs/<run>/meta.json                 model, effort, git commit, config, versions
  runs/<run>/trajectories/<id>.json    one per task (the AgentResult)
  runs/<run>/predictions.jsonl         harness input
  results/<run>/instances.jsonl        harness outcome per task (from score.py)
  results/<run>/<model>.<run_id>.json  the harness's own summary report per batch
"""

import argparse
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import docker
from dotenv import load_dotenv
from openai import OpenAI

from fixpilot.agent import PRICES, AgentConfig, run_agent
from fixpilot.images import ensure_image, free_gb, remove_image
from fixpilot.sandbox import Sandbox
from fixpilot.score import load_statuses, score_ids
from fixpilot.tasks import DEFAULT_DATASET, load_tasks, read_ids

FINISHED = ("submitted", "step_limit", "cost_limit")


# ---------- small file helpers ----------

def write_json(path: Path, data) -> None:
    """Write JSON atomically: a crash mid-write never leaves a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def git_state() -> dict:
    """Current commit, and whether the code has uncommitted changes."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "fixpilot", "pyproject.toml", "uv.lock"],
                           capture_output=True, text=True).stdout.strip()
    return {"git_commit": commit or "unknown", "git_dirty": bool(dirty)}


def load_trajectories(traj_dir: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(traj_dir.glob("*.json"))}


def write_predictions(run_dir: Path, trajectories: dict[str, dict], model_name: str) -> Path:
    """predictions.jsonl from all trajectories (rebuilt every time, so it's always complete)."""
    path = run_dir / "predictions.jsonl"
    lines = [json.dumps({"instance_id": iid, "model_name_or_path": model_name, "model_patch": t["patch"]})
             for iid, t in sorted(trajectories.items())]
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ---------- run metadata ----------

def run_settings(args, config: AgentConfig) -> dict:
    """Everything that defines *what* this run measures. Resuming must not change it."""
    agent = {k: v for k, v in asdict(config).items() if k != "verbose"}
    return {"dataset": args.dataset, "ids_file": args.ids_file, "agent": agent}


def prepare_meta(run_dir: Path, args, config: AgentConfig, ids: list[str]) -> dict:
    """Create meta.json, or check that a resumed run has identical settings."""
    meta_path = run_dir / "meta.json"
    settings = run_settings(args, config)
    git = git_state()
    if git["git_dirty"] and not args.allow_dirty:
        raise SystemExit("Uncommitted changes in fixpilot/ or dependencies. Commit first, so meta.json's "
                         "git_commit is the code that actually ran (or pass --allow-dirty for a throwaway run).")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta["settings"] != settings:
            raise SystemExit(f"{run_dir} was started with different settings:\n{meta['settings']}\n"
                             f"now:\n{settings}\nUse a new --run-name.")
        if meta["git_commit"] != git["git_commit"]:
            meta.setdefault("resumed_at_commits", []).append(git["git_commit"])
    else:
        meta = {"run_name": run_dir.name, "settings": settings, "ids": ids, **git,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "versions": {p: version(p) for p in ("openai", "swebench", "docker")}}
    write_json(meta_path, meta)
    return meta


# ---------- one task ----------

def run_one(task: dict, config: AgentConfig, client: OpenAI, traj_dir: Path) -> dict:
    """Start a sandbox, run the agent, save the trajectory. Never raises."""
    started = time.monotonic()
    try:
        with Sandbox(task["image"], default_timeout=config.command_timeout) as sb:
            result = run_agent(task, sb, config, client=client).to_dict()
    except Exception as e:  # sandbox failed to start, Docker trouble, ...
        result = {"instance_id": task["instance_id"], "model": config.model,
                  "reasoning_effort": config.reasoning_effort, "exit_reason": "error", "steps": 0,
                  "cost_usd": 0.0, "duration_s": round(time.monotonic() - started, 1),
                  "tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "tokens_reasoning": 0,
                  "patch": "", "error": f"infra: {type(e).__name__}: {e}", "trajectory": []}
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(traj_dir / f"{task['instance_id']}.json", result)
    return result


def run_batch(tasks: list[dict], config: AgentConfig, client: OpenAI, traj_dir: Path,
              workers: int, spent: list[float], max_total_cost: float) -> None:
    """Run the agent on a batch of tasks in parallel, stopping new tasks once the run budget is spent."""
    lock = threading.Lock()

    def job(task: dict) -> dict | None:
        with lock:
            if spent[0] >= max_total_cost:
                return None  # run budget used up: leave the task for a later resume
        result = run_one(task, config, client, traj_dir)
        with lock:
            spent[0] += result["cost_usd"]
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, t): t["instance_id"] for t in tasks}
        for fut in as_completed(futures):
            r = fut.result()
            if r is None:
                print(f"  skip  {futures[fut]} (run budget ${max_total_cost:.2f} reached)", flush=True)
                continue
            print(f"  done  {r['instance_id']:<40} {r['exit_reason']:<11} {r['steps']:>2} steps "
                  f"${r['cost_usd']:.4f}  {r['duration_s']:>6.1f}s  patch {'yes' if r['patch'].strip() else 'EMPTY'}"
                  + (f"  [{r['error'][:80]}]" if r.get("error") else ""), flush=True)


# ---------- main ----------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run FixPilot on many SWE-bench tasks and score them.")
    p.add_argument("--run-name", required=True, help="folder name under runs/ and results/")
    p.add_argument("--ids-file", default="data/dev_ids.txt")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--limit", type=int, help="only the first N tasks (smoke tests)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=5, help="tasks per image pull / score / cleanup cycle")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-cost", type=float, default=0.50, help="USD per task")
    p.add_argument("--max-total-cost", type=float, default=2.00, help="USD for the whole run")
    p.add_argument("--command-timeout", type=int, default=180)
    p.add_argument("--min-free-gb", type=float, default=30.0, help="stop before pulling if less disk is free")
    p.add_argument("--keep-images", action="store_true", help="don't remove pulled images after scoring")
    p.add_argument("--no-score", action="store_true", help="run the agent only")
    p.add_argument("--allow-dirty", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()
    model = os.environ.get("FIXPILOT_MODEL", "gpt-6-luna")
    if model not in PRICES:
        raise SystemExit(f"No prices for {model!r}: add it to PRICES in agent.py first.")
    config = AgentConfig(model=model, reasoning_effort=args.effort, max_steps=args.max_steps,
                         max_cost_usd=args.max_cost, command_timeout=args.command_timeout, verbose=False)

    ids = read_ids(args.ids_file)[: args.limit]
    run_dir, report_dir = Path("runs") / args.run_name, Path("results") / args.run_name
    traj_dir = run_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    meta = prepare_meta(run_dir, args, config, ids)
    model_name = f"fixpilot-{model}"
    tasks = {t["instance_id"]: t for t in load_tasks(ids, args.dataset)}  # visible fields only

    client = OpenAI(max_retries=5, timeout=300)  # shared by all threads (the client is thread-safe)
    docker_client = docker.from_env()
    spent = [sum(t["cost_usd"] for t in load_trajectories(traj_dir).values())]  # resumed runs count too
    print(f"{args.run_name}: {len(ids)} tasks, {model} effort={args.effort}, workers={args.workers}, "
          f"already spent ${spent[0]:.4f} of ${args.max_total_cost:.2f}", flush=True)

    for b, batch in enumerate(chunks(ids, args.batch_size), start=1):
        trajectories = load_trajectories(traj_dir)
        statuses = load_statuses(report_dir)
        to_run = [i for i in batch if trajectories.get(i, {}).get("exit_reason") not in FINISHED]
        to_score = [i for i in batch if i not in statuses or i in to_run]
        if not to_run and (args.no_score or not to_score):
            continue
        print(f"\nbatch {b}: run {len(to_run)}, score {len(to_score)}", flush=True)

        if free_gb() < args.min_free_gb:
            raise SystemExit(f"Only {free_gb():.0f} GB free (< {args.min_free_gb}). Stopping before pulling.")
        pulled, unavailable = [], set()
        try:  # pulls are inside the try, so a crash still removes what this batch pulled
            for iid in batch:
                try:
                    if ensure_image(docker_client, tasks[iid]["image"]):
                        pulled.append(tasks[iid]["image"])
                except Exception as e:  # no image -> skip the task now; a resume retries it
                    unavailable.add(iid)
                    print(f"  skip  {iid}: image unavailable ({type(e).__name__}: {e})", flush=True)
            print(f"  pulled {len(pulled)} image(s), {free_gb():.0f} GB free", flush=True)

            run_batch([tasks[i] for i in to_run if i not in unavailable], config, client, traj_dir,
                      args.workers, spent, args.max_total_cost)
            trajectories = load_trajectories(traj_dir)
            predictions = write_predictions(run_dir, trajectories, model_name)
            ready = [i for i in to_score if i in trajectories and i not in unavailable]
            if ready and not args.no_score:
                run_id = f"{args.run_name}.b{b}.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                for r in score_ids(predictions, ready, args.dataset, run_id, report_dir, args.workers):
                    print(f"  score {r['instance_id']:<40} {r['status']}", flush=True)
        finally:
            if not args.keep_images:
                removed = sum(remove_image(docker_client, im) for im in pulled)
                print(f"  removed {removed} image(s)", flush=True)

    trajectories = load_trajectories(traj_dir)
    meta.update(finished_at=datetime.now(timezone.utc).isoformat(), n_trajectories=len(trajectories),
                total_cost_usd=round(sum(t["cost_usd"] for t in trajectories.values()), 6))
    write_json(run_dir / "meta.json", meta)
    print(f"\nfinished: {len(trajectories)}/{len(ids)} trajectories, ${meta['total_cost_usd']:.4f} total. "
          f"Next: uv run python -m fixpilot.analyze --run-name {args.run_name}", flush=True)


if __name__ == "__main__":
    main()
