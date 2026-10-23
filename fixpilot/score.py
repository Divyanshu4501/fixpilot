"""
fixpilot/score.py

Runs the OFFICIAL SWE-bench harness on a run's predictions and records, per instance,
what happened. We never grade patches ourselves: "resolved" comes only from the
harness's own report.json.

Why this wrapper exists: the harness's detailed logs go to logs/ (gitignored), and a
patch that fails to apply leaves no report.json at all (it just becomes an "error").
So right after scoring we read those logs and save a small status per instance to
results/<run>/instances.jsonl, which is committed and is what analyze.py reads.
"""

import json
import subprocess
import sys
from pathlib import Path

HARNESS_LOGS = Path("logs/run_evaluation")
APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"  # marker the harness writes in run_instance.log


def run_harness(predictions: Path, ids: list[str], dataset: str, run_id: str,
                report_dir: Path, max_workers: int = 4) -> Path:
    """Call the harness as a subprocess. Returns the path of its summary report."""
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path("logs/fixpilot") / f"{run_id}.harness.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "swebench.harness.run_evaluation",
           "--dataset_name", dataset, "--predictions_path", str(predictions),
           "--instance_ids", *ids, "--max_workers", str(max_workers),
           "--run_id", run_id, "--report_dir", str(report_dir)]
    with log_file.open("w") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"harness exited with {proc.returncode}; see {log_file}")
    model = json.loads(predictions.read_text().splitlines()[0])["model_name_or_path"]
    return report_dir / f"{model.replace('/', '__')}.{run_id}.json"


def _test_counts(tests_status: dict) -> dict:
    """Numbers of passing/failing hidden tests (counts only, no test names)."""
    f2p, p2p = tests_status.get("FAIL_TO_PASS", {}), tests_status.get("PASS_TO_PASS", {})
    return {"f2p_pass": len(f2p.get("success", [])), "f2p_fail": len(f2p.get("failure", [])),
            "p2p_pass": len(p2p.get("success", [])), "p2p_fail": len(p2p.get("failure", []))}


def instance_status(instance_id: str, model: str, run_id: str, summary: dict) -> dict:
    """One instance's outcome, from the harness summary plus its per-instance logs.

    status is one of: resolved | unresolved | empty_patch | apply_failed | harness_error
    """
    log_dir = HARNESS_LOGS / run_id / model.replace("/", "__") / instance_id
    record = {"instance_id": instance_id, "run_id": run_id,
              "infra_failure": instance_id in summary.get("infra_failure_ids", []),
              "infra_reason": summary.get("failure_reasons", {}).get(instance_id)}
    if instance_id in summary.get("empty_patch_ids", []):
        return {**record, "status": "empty_patch"}
    if instance_id in summary.get("resolved_ids", []) or instance_id in summary.get("unresolved_ids", []):
        report = json.loads((log_dir / "report.json").read_text())[instance_id]
        status = "resolved" if report["resolved"] else "unresolved"
        return {**record, "status": status, **_test_counts(report.get("tests_status", {}))}
    instance_log = log_dir / "run_instance.log"
    if instance_log.exists() and APPLY_PATCH_FAIL in instance_log.read_text(errors="replace"):
        return {**record, "status": "apply_failed"}
    return {**record, "status": "harness_error"}


def score_ids(predictions: Path, ids: list[str], dataset: str, run_id: str,
              report_dir: Path, max_workers: int = 4) -> list[dict]:
    """Score some instances, append their statuses to report_dir/instances.jsonl, return them.

    Empty patches are not sent to the harness (there is nothing to apply, and a batch of
    only empty patches gives the harness nothing to run); they are recorded as empty_patch.
    """
    preds = {p["instance_id"]: p for p in map(json.loads, predictions.read_text().splitlines())}
    model = next(iter(preds.values()))["model_name_or_path"]
    to_run = [i for i in ids if preds[i]["model_patch"].strip()]
    summary = {"empty_patch_ids": [i for i in ids if i not in to_run]}
    if to_run:
        summary_path = run_harness(predictions, to_run, dataset, run_id, report_dir, max_workers)
        summary = json.loads(summary_path.read_text())
        summary["empty_patch_ids"] = sorted(set(summary.get("empty_patch_ids", [])) | set(ids) - set(to_run))
    records = [instance_status(i, model, run_id, summary) for i in ids]
    with (report_dir / "instances.jsonl").open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return records


def load_statuses(report_dir: Path) -> dict[str, dict]:
    """Latest recorded status per instance (later lines win, e.g. after a re-score)."""
    path = report_dir / "instances.jsonl"
    if not path.exists():
        return {}
    statuses = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            statuses[r["instance_id"]] = r
    return statuses
