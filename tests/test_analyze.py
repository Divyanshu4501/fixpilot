"""Metrics: Wilson CI, failure categories, test-file detection, summary numbers."""

import pytest

from fixpilot.analyze import categorize, is_test_file, summarize, task_rows, upsert_ablation, wilson_ci


def traj(exit_reason="submitted", patch="diff --git a/src/x.py b/src/x.py\n+fix", steps=10, cost=0.01):
    return {"exit_reason": exit_reason, "patch": patch, "steps": steps, "cost_usd": cost, "duration_s": 60.0}


def test_wilson_ci_known_value():
    low, high = wilson_ci(10, 20)  # p = 0.5, n = 20
    assert low == pytest.approx(0.2993, abs=1e-3) and high == pytest.approx(0.7007, abs=1e-3)


def test_wilson_ci_stays_inside_0_1():
    assert wilson_ci(0, 20)[0] == 0.0 and wilson_ci(0, 20)[1] > 0
    assert wilson_ci(20, 20)[1] == pytest.approx(1.0) and wilson_ci(20, 20)[0] < 1
    assert wilson_ci(0, 0) == (0.0, 0.0)


@pytest.mark.parametrize("t, s, expected", [
    (traj(), {"status": "resolved"}, "resolved"),
    (None, None, "not_run"),
    (traj("error", patch=""), None, "infra_error"),
    (traj(), None, "not_scored"),
    (traj(), {"status": "harness_error"}, "infra_error"),
    (traj(), {"status": "unresolved", "infra_failure": True}, "infra_error"),
    (traj(patch=""), {"status": "empty_patch"}, "empty_patch"),
    (traj(), {"status": "apply_failed"}, "apply_failed"),
    (traj(), {"status": "unresolved", "p2p_fail": 2, "f2p_fail": 0}, "broke_tests"),
    (traj(), {"status": "unresolved", "p2p_fail": 0, "f2p_fail": 1}, "wrong_fix"),
    (traj("error"), {"status": "resolved"}, "resolved"),  # partial work after a crash can still count
])
def test_categorize(t, s, expected):
    assert categorize(t, s) == expected


@pytest.mark.parametrize("path, expected", [
    ("tests/auth_tests/test_validators.py", True),
    ("sympy/core/tests/test_basic.py", True),
    ("testing/python/approx.py", True),
    ("src/conftest.py", True),
    ("django/test/utils.py", False),   # library code, not a test
    ("src/_pytest/python_api.py", False),
    ("django/contrib/auth/validators.py", False),
])
def test_is_test_file(path, expected):
    assert is_test_file(path) == expected


def test_summary_counts_every_planned_task_in_the_denominator():
    ids = ["a", "b", "c", "d"]
    trajectories = {"a": traj(cost=0.02), "b": traj(patch="", cost=0.01), "c": traj("step_limit", cost=0.03)}
    statuses = {"a": {"status": "resolved"}, "b": {"status": "empty_patch"},
                "c": {"status": "unresolved", "p2p_fail": 0, "f2p_fail": 1}}
    s = summarize(task_rows(ids, trajectories, statuses), statuses)
    assert s["n_tasks"] == 4 and s["resolved"] == 1 and s["resolve_rate"] == 0.25
    assert s["total_cost_usd"] == pytest.approx(0.06) and s["cost_per_resolved_usd"] == pytest.approx(0.06)
    assert s["empty_patch_rate"] == 0.5  # b (empty) and d (never ran)
    assert s["patch_applied_rate"] == 1.0  # a and c applied
    assert s["categories"]["not_run"] == 1 and s["exit_reasons"]["step_limit"] == 1


def test_upsert_ablation_replaces_the_same_run(tmp_path):
    path = tmp_path / "ablation.md"
    upsert_ablation(path, "base", "| baseline | base | old |")
    upsert_ablation(path, "other", "| x | other | y |")
    upsert_ablation(path, "base", "| baseline | base | new |")
    text = path.read_text()
    assert "old" not in text and text.count("| base |") == 1 and "| other |" in text
    assert text.splitlines()[0].startswith("# Ablation table")
