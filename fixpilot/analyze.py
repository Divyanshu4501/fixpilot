"""
fixpilot/analyze.py

Turns a finished run (trajectories + harness statuses) into numbers:

    uv run python -m fixpilot.analyze --run-name baseline --ablation "baseline (bash only)"

Writes results/<run>/summary.json and results/<run>/summary.md and, with --ablation,
adds or replaces that run's row in results/ablation.md.

Honest accounting: the denominator is EVERY task the run was meant to do. Tasks that
crashed, were never scored, or hit a limit count as not resolved.
"""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from statsmodels.stats.proportion import proportion_confint

from fixpilot.score import load_statuses

# A task's failure category, checked in this order (first match wins).
CATEGORIES = [
    "resolved",
    "infra_error",       # the agent crashed (API/Docker) with no patch, or the harness failed on our side
    "not_run",           # no trajectory (e.g. run budget reached)
    "not_scored",        # trajectory but no harness result yet
    "empty_patch",       # the agent changed nothing that git tracks
    "apply_failed",      # the patch didn't apply cleanly in the harness
    "broke_tests",       # hidden PASS_TO_PASS tests now fail (the fix broke something)
    "wrong_fix",         # applied, nothing broke, but the hidden FAIL_TO_PASS tests still fail
]


def wilson_ci(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n.

    Better than "p ± 1.96·sqrt(p(1-p)/n)" for small n or p near 0/1: it never goes below 0
    or above 1, and doesn't collapse to zero width when k = 0.
    """
    if n == 0:
        return 0.0, 0.0
    low, high = proportion_confint(k, n, alpha=0.05, method="wilson")
    return float(low), float(high)


def patch_files(patch: str) -> list[str]:
    """Files a unified diff touches (from its `diff --git a/X b/X` lines)."""
    return [line.split(" b/", 1)[1] for line in patch.splitlines()
            if line.startswith("diff --git ") and " b/" in line]


def is_test_file(path: str) -> bool:
    """Heuristic: inside a tests/ or testing/ folder, or named test_*.py, *_test.py, conftest.py.

    (Not "test/" on purpose: django/test/ is library code, not tests.)
    """
    *folders, name = path.split("/")
    return (any(f in ("tests", "testing") for f in folders)
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def touches_tests(patch: str) -> bool:
    return any(is_test_file(f) for f in patch_files(patch))


def categorize(traj: dict | None, status: dict | None) -> str:
    """One failure category per task (see CATEGORIES)."""
    if status and status["status"] == "resolved":
        return "resolved"
    if traj is None:
        return "not_run"
    if traj["exit_reason"] == "error" and not traj["patch"].strip():
        return "infra_error"  # crashed before producing anything (with a patch, the harness decides)
    if status is None:
        return "not_scored"
    s = status["status"]
    if s == "harness_error" or status.get("infra_failure"):
        return "infra_error"
    if s in ("empty_patch", "apply_failed"):
        return s
    if status.get("p2p_fail", 0) > 0:
        return "broke_tests"
    return "wrong_fix"


def task_rows(ids: list[str], trajectories: dict, statuses: dict) -> list[dict]:
    rows = []
    for iid in ids:
        t, s = trajectories.get(iid), statuses.get(iid)
        rows.append({
            "instance_id": iid,
            "category": categorize(t, s),
            "exit_reason": t["exit_reason"] if t else "not_run",
            "steps": t["steps"] if t else 0,
            "cost_usd": t["cost_usd"] if t else 0.0,
            "duration_s": t["duration_s"] if t else 0.0,
            "tokens_reasoning": t.get("tokens_reasoning", 0) if t else 0,
            "patch_lines": len(t["patch"].splitlines()) if t else 0,
            "touches_tests": touches_tests(t["patch"]) if t else False,
            "error": (t or {}).get("error"),
        })
    return rows


def summarize(rows: list[dict], statuses: dict) -> dict:
    """The headline metrics for a list of task rows."""
    n = len(rows)
    resolved = sum(r["category"] == "resolved" for r in rows)
    low, high = wilson_ci(resolved, n)
    cost = sum(r["cost_usd"] for r in rows)
    nonempty = [r for r in rows if r["patch_lines"] > 0]
    scored_nonempty = [r for r in nonempty if r["instance_id"] in statuses
                       and statuses[r["instance_id"]]["status"] != "harness_error"]
    applied = [r for r in scored_nonempty if statuses[r["instance_id"]]["status"] in ("resolved", "unresolved")]
    return {
        "n_tasks": n,
        "resolved": resolved,
        "resolve_rate": resolved / n if n else 0.0,
        "ci95_wilson": [low, high],
        "total_cost_usd": round(cost, 4),
        "cost_per_task_usd": round(cost / n, 4) if n else 0.0,
        "cost_per_resolved_usd": round(cost / resolved, 4) if resolved else None,
        "median_steps": statistics.median(r["steps"] for r in rows) if rows else 0,
        "median_duration_s": statistics.median(r["duration_s"] for r in rows) if rows else 0,
        "empty_patch_rate": (n - len(nonempty)) / n if n else 0.0,
        "patch_applied_rate": len(applied) / len(scored_nonempty) if scored_nonempty else None,
        "infra_failures": sum(r["category"] == "infra_error" for r in rows),
        "not_run": sum(r["category"] == "not_run" for r in rows),
        "not_scored": sum(r["category"] == "not_scored" for r in rows),
        "touches_tests": sum(r["touches_tests"] for r in rows),
        "categories": {c: sum(r["category"] == c for r in rows) for c in CATEGORIES},
        "exit_reasons": dict(Counter(r["exit_reason"] for r in rows).most_common()),
        "total_reasoning_tokens": sum(r["tokens_reasoning"] for r in rows),
    }


# ---------- output ----------

def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def summary_markdown(run_name: str, meta: dict, s: dict, rows: list[dict]) -> str:
    agent = meta["settings"]["agent"]
    low, high = s["ci95_wilson"]
    lines = [
        f"# {run_name}", "",
        f"- dataset: `{meta['settings']['dataset']}` · tasks: `{meta['settings']['ids_file']}` ({s['n_tasks']})",
        f"- model: `{agent['model']}` · effort: `{agent['reasoning_effort']}` · git: `{meta['git_commit'][:10]}`",
        f"- budgets: {agent['max_steps']} steps · ${agent['max_cost_usd']}/task · {agent['command_timeout']} s/command",
        "",
        f"**Resolved {s['resolved']}/{s['n_tasks']} = {pct(s['resolve_rate'])}** "
        f"(95% Wilson CI {pct(low)}–{pct(high)})", "",
        "| metric | value |", "|---|---|",
        f"| total cost | ${s['total_cost_usd']:.4f} |",
        f"| cost per task | ${s['cost_per_task_usd']:.4f} |",
        f"| cost per resolved | {'n/a' if s['cost_per_resolved_usd'] is None else '$%.4f' % s['cost_per_resolved_usd']} |",
        f"| median steps | {s['median_steps']} |",
        f"| median time per task | {s['median_duration_s']:.0f} s |",
        f"| empty-patch rate | {pct(s['empty_patch_rate'])} |",
        f"| patch-applied rate (non-empty patches) | {pct(s['patch_applied_rate'])} |",
        f"| infra failures | {s['infra_failures']} |",
        f"| patches touching test files | {s['touches_tests']} |",
        f"| exit reasons | {', '.join(f'{k} {v}' for k, v in s['exit_reasons'].items())} |",
        "", "## Failure categories", "", "| category | tasks |", "|---|---|",
        *[f"| {c} | {k} |" for c, k in s["categories"].items() if k],
        "", "## Per task", "",
        "| task | category | exit | steps | cost | patch lines | edits tests |", "|---|---|---|---|---|---|---|",
        *[f"| {r['instance_id']} | {r['category']} | {r['exit_reason']} | {r['steps']} | ${r['cost_usd']:.4f} "
          f"| {r['patch_lines']} | {'yes' if r['touches_tests'] else ''} |" for r in rows],
    ]
    return "\n".join(lines) + "\n"


ABLATION_HEADER = [
    "# Ablation table (dev set)", "",
    "Each row is one configuration on the same dev tasks. Resolved = official SWE-bench harness.", "",
    "| config | run | model · effort | resolved | rate (95% CI) | $/task | $/resolved | median steps | empty patch | applied | git |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
]


def ablation_row(label: str, run_name: str, meta: dict, s: dict) -> str:
    agent = meta["settings"]["agent"]
    low, high = s["ci95_wilson"]
    per_resolved = "n/a" if s["cost_per_resolved_usd"] is None else f"${s['cost_per_resolved_usd']:.3f}"
    return (f"| {label} | {run_name} | {agent['model']} · {agent['reasoning_effort']} | {s['resolved']}/{s['n_tasks']} "
            f"| {pct(s['resolve_rate'])} ({pct(low)}–{pct(high)}) | ${s['cost_per_task_usd']:.4f} | {per_resolved} "
            f"| {s['median_steps']} | {pct(s['empty_patch_rate'])} | {pct(s['patch_applied_rate'])} "
            f"| {meta['git_commit'][:7]} |")


def upsert_ablation(path: Path, run_name: str, row: str) -> None:
    """Add this run's row to the ablation table, replacing an older row for the same run."""
    lines = path.read_text().splitlines() if path.exists() else list(ABLATION_HEADER)
    lines = [l for l in lines if not l.startswith("| ") or f"| {run_name} |" not in l]
    lines.append(row)
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Metrics for a finished FixPilot run.")
    p.add_argument("--run-name", required=True)
    p.add_argument("--ablation", metavar="LABEL", help="also write this run's row to results/ablation.md")
    args = p.parse_args()

    run_dir, report_dir = Path("runs") / args.run_name, Path("results") / args.run_name
    meta = json.loads((run_dir / "meta.json").read_text())
    trajectories = {f.stem: json.loads(f.read_text()) for f in (run_dir / "trajectories").glob("*.json")}
    statuses = load_statuses(report_dir)
    rows = task_rows(meta["ids"], trajectories, statuses)
    s = summarize(rows, statuses)

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps({"summary": s, "tasks": rows}, indent=2) + "\n")
    md = summary_markdown(args.run_name, meta, s, rows)
    (report_dir / "summary.md").write_text(md)
    print(md)
    if args.ablation:
        upsert_ablation(Path("results/ablation.md"), args.run_name, ablation_row(args.ablation, args.run_name, meta, s))
        print("updated results/ablation.md")


if __name__ == "__main__":
    main()
