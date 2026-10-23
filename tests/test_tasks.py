"""Dev/test selection: sizes, no overlap, proportional repo mix, determinism."""

from collections import Counter

import pytest

from fixpilot.tasks import proportional_counts, read_ids, select_splits


def fake_rows() -> list[dict]:
    sizes = {"big/big": 230, "mid/mid": 75, "small/small": 40, "tiny/tiny": 3, "one/one": 1}
    return [{"instance_id": f"{repo.split('/')[0]}__{repo.split('/')[0]}-{i}", "repo": repo}
            for repo, n in sizes.items() for i in range(n)]


def test_proportional_counts_example_from_docstring():
    assert proportional_counts({"a": 6, "b": 3, "c": 1}, 5) == {"a": 3, "b": 2, "c": 0}


def test_proportional_counts_always_sum_to_n():
    sizes = Counter(r["repo"] for r in fake_rows())
    for n in (1, 7, 20, 50, 100):
        assert sum(proportional_counts(sizes, n).values()) == n


def test_splits_have_right_sizes_and_no_overlap():
    dev, test = select_splits(fake_rows(), n_dev=20, n_test=50, seed=1, excluded=frozenset())
    assert len(dev) == 20 and len(test) == 50
    assert not set(dev) & set(test)
    assert len(set(dev)) == 20 and len(set(test)) == 50


def test_splits_follow_the_repo_mix():
    rows = fake_rows()
    dev, test = select_splits(rows, n_dev=20, n_test=50, seed=1, excluded=frozenset())
    repo_of = {r["instance_id"]: r["repo"] for r in rows}
    assert Counter(repo_of[i] for i in test) == Counter(proportional_counts(Counter(repo_of.values()), 50))


def test_splits_are_deterministic_and_order_independent():
    rows = fake_rows()
    a = select_splits(rows, seed=7, excluded=frozenset())
    b = select_splits(list(reversed(rows)), seed=7, excluded=frozenset())
    c = select_splits(rows, seed=8, excluded=frozenset())
    assert a == b and a != c


def test_excluded_ids_are_never_selected():
    rows = fake_rows()
    excluded = frozenset(r["instance_id"] for r in rows if r["repo"] == "big/big")
    dev, test = select_splits(rows, n_dev=5, n_test=10, seed=1, excluded=excluded)
    assert not (set(dev) | set(test)) & excluded


def test_a_repo_too_small_for_its_share_is_an_error():
    rows = [{"instance_id": f"a__a-{i}", "repo": "a/a"} for i in range(3)]
    with pytest.raises(ValueError):
        select_splits(rows, n_dev=2, n_test=2, seed=1, excluded=frozenset())


def test_read_ids_skips_blanks_and_comments(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("# header\na__a-1\n\n  b__b-2  \n")
    assert read_ids(f) == ["a__a-1", "b__b-2"]


def test_committed_id_files_are_disjoint_and_sized():
    dev, test = read_ids("data/dev_ids.txt"), read_ids("data/test_ids.txt")
    assert len(dev) == 20 and len(test) == 50 and not set(dev) & set(test)
    assert "django__django-11099" not in dev + test
