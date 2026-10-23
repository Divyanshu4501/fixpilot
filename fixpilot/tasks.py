"""
fixpilot/tasks.py

Loads SWE-bench tasks for the agent, and picks the fixed dev and test sets.

The most important job here is **no leakage**: a dataset row also contains the real
fix (`patch`), the hidden tests (`test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`), hints
and more. The agent may only ever see the five VISIBLE_FIELDS. Everything else is
dropped here, before a task reaches the agent.

Pick the dev/test IDs once (refuses to overwrite them):
    uv run python -m fixpilot.tasks
"""

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"

VISIBLE_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement", "image")
# Never shown to the agent. (Anything not in VISIBLE_FIELDS is dropped anyway;
# this list exists so tests and checks can name the dangerous ones explicitly.)
FORBIDDEN_FIELDS = ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "hints_text",
                    "eval_script", "difficulty")

SEED = 20261023
N_DEV, N_TEST = 20, 50
# Tasks already used while building the agent (M3 self-test): kept out of dev and test.
EXCLUDED_IDS = frozenset({"django__django-11099"})

DATA_DIR = Path("data")
DEV_FILE, TEST_FILE, INFO_FILE = DATA_DIR / "dev_ids.txt", DATA_DIR / "test_ids.txt", DATA_DIR / "split_info.json"


# ---------- loading ----------

def visible_task(row: dict) -> dict:
    """Keep only the fields the agent is allowed to see."""
    missing = [k for k in VISIBLE_FIELDS if not row.get(k)]
    if missing:
        raise ValueError(f"{row.get('instance_id', '?')}: missing visible fields {missing}")
    return {k: row[k] for k in VISIBLE_FIELDS}


def assert_visible_only(task: dict) -> None:
    """Raise if a task dict carries anything beyond the visible fields."""
    extra = set(task) - set(VISIBLE_FIELDS)
    if extra:
        raise ValueError(f"Task {task.get('instance_id', '?')} has non-visible fields: {sorted(extra)}")


def load_rows(dataset: str = DEFAULT_DATASET):
    """The full dataset split (with hidden fields). Only for selection and scoring, never for the agent."""
    from datasets import load_dataset  # imported here: slow, and unit tests don't need it
    return load_dataset(dataset, split=SPLIT)


def load_tasks(ids: list[str], dataset: str = DEFAULT_DATASET) -> list[dict]:
    """Visible-only tasks for the given instance IDs, in the given order."""
    by_id = {row["instance_id"]: row for row in load_rows(dataset)}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise KeyError(f"Not in {dataset}: {unknown}")
    return [visible_task(by_id[i]) for i in ids]


def read_ids(path: str | Path) -> list[str]:
    """Instance IDs from a text file, one per line (blank lines and # comments ignored)."""
    lines = Path(path).read_text().splitlines()
    return [s.strip() for s in lines if s.strip() and not s.strip().startswith("#")]


# ---------- dev / test selection ----------

def proportional_counts(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Split n slots across groups in proportion to their sizes (largest-remainder method).

    Each group gets floor(n * share); the slots left over go to the groups with the
    largest fractional remainders. Ties are broken by group name, so the result is
    deterministic. Example: sizes {a: 6, b: 3, c: 1}, n=5 -> {a: 3, b: 2, c: 0}.
    """
    total = sum(sizes.values())
    exact = {g: n * s / total for g, s in sizes.items()}
    counts = {g: int(x) for g, x in exact.items()}
    leftover = n - sum(counts.values())
    by_remainder = sorted(sizes, key=lambda g: (-(exact[g] - counts[g]), g))
    for g in by_remainder[:leftover]:
        counts[g] += 1
    return counts


def select_splits(rows, n_dev: int = N_DEV, n_test: int = N_TEST, seed: int = SEED,
                  excluded: frozenset = EXCLUDED_IDS) -> tuple[list[str], list[str]]:
    """Pick dev and test instance IDs: same repo mix as the dataset, no overlap, reproducible.

    Dev and test are each allocated proportionally to repo sizes. Within each repo the
    IDs are sorted (so dataset order doesn't matter), shuffled with a seeded RNG, and
    the first ones go to test, the next ones to dev.
    """
    by_repo: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["instance_id"] not in excluded:
            by_repo[row["repo"]].append(row["instance_id"])
    sizes = {repo: len(ids) for repo, ids in by_repo.items()}
    test_counts = proportional_counts(sizes, n_test)
    dev_counts = proportional_counts(sizes, n_dev)

    rng = random.Random(seed)
    dev, test = [], []
    for repo in sorted(by_repo):
        ids = sorted(by_repo[repo])
        rng.shuffle(ids)
        t, d = test_counts[repo], dev_counts[repo]
        if t + d > len(ids):
            raise ValueError(f"{repo} has only {len(ids)} tasks, needs {t + d}")
        test += ids[:t]
        dev += ids[t:t + d]
    return sorted(dev), sorted(test)


def split_info(rows, dev: list[str], test: list[str], dataset: str, seed: int) -> dict:
    """A summary of the selection (repo and difficulty mix), saved next to the ID lists."""
    meta = {r["instance_id"]: r for r in rows}

    def mix(ids: list[str], key: str) -> dict:
        return dict(Counter(meta[i].get(key) or "n/a" for i in ids).most_common())

    return {
        "dataset": dataset, "split": SPLIT, "seed": seed, "excluded": sorted(EXCLUDED_IDS),
        "dev": {"n": len(dev), "repos": mix(dev, "repo"), "difficulty": mix(dev, "difficulty")},
        "test": {"n": len(test), "repos": mix(test, "repo"), "difficulty": mix(test, "difficulty")},
        "pool": {"n": len(rows), "repos": mix(list(meta), "repo"), "difficulty": mix(list(meta), "difficulty")},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pick and save the fixed dev/test task IDs.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    if DEV_FILE.exists() or TEST_FILE.exists():
        raise SystemExit(f"{DEV_FILE} / {TEST_FILE} already exist. The sets are fixed; delete them by hand "
                         "only if you really mean to re-draw (never after the test set has been run).")
    rows = list(load_rows(args.dataset))
    dev, test = select_splits(rows, seed=args.seed)
    DATA_DIR.mkdir(exist_ok=True)
    DEV_FILE.write_text("\n".join(dev) + "\n")
    TEST_FILE.write_text("\n".join(test) + "\n")
    info = split_info(rows, dev, test, args.dataset, args.seed)
    INFO_FILE.write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({k: info[k] for k in ("dev", "test")}, indent=2))


if __name__ == "__main__":
    main()
