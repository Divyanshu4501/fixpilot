# baseline

- dataset: `SWE-bench/SWE-bench_Verified` · tasks: `data/dev_ids.txt` (20)
- model: `gpt-6-luna` · effort: `medium` · git: `9cb99f436c`
- budgets: 30 steps · $0.5/task · 180 s/command

**Resolved 14/20 = 70%** (95% Wilson CI 48%–85%)

| metric | value |
|---|---|
| total cost | $0.0713 |
| cost per task | $0.0036 |
| cost per resolved | $0.0051 |
| median steps | 11.5 |
| median time per task | 75 s |
| empty-patch rate | 0% |
| patch-applied rate (non-empty patches) | 100% |
| infra failures | 0 |
| patches touching test files | 0 |
| exit reasons | submitted 19, step_limit 1 |

## Failure categories

| category | tasks |
|---|---|
| resolved | 14 |
| wrong_fix | 6 |

## Per task

| task | category | exit | steps | cost | patch lines | edits tests | harness note |
|---|---|---|---|---|---|---|---|
| astropy__astropy-14598 | resolved | step_limit | 30 | $0.0122 | 64 |  |  |
| django__django-11206 | resolved | submitted | 12 | $0.0037 | 33 |  |  |
| django__django-12276 | resolved | submitted | 10 | $0.0018 | 24 |  |  |
| django__django-12304 | resolved | submitted | 10 | $0.0023 | 15 |  |  |
| django__django-13363 | resolved | submitted | 10 | $0.0022 | 22 |  |  |
| django__django-13401 | wrong_fix | submitted | 14 | $0.0032 | 32 |  |  |
| django__django-14500 | resolved | submitted | 13 | $0.0038 | 12 |  |  |
| django__django-15572 | resolved | submitted | 15 | $0.0029 | 22 |  |  |
| django__django-16255 | resolved | submitted | 9 | $0.0014 | 13 |  |  |
| django__django-16595 | resolved | submitted | 11 | $0.0021 | 17 |  |  |
| matplotlib__matplotlib-20488 | resolved | submitted | 9 | $0.0024 | 13 |  |  |
| pydata__xarray-7229 | wrong_fix | submitted | 14 | $0.0041 | 35 |  |  |
| pylint-dev__pylint-4661 | wrong_fix | submitted | 11 | $0.0032 | 63 |  | missing_module |
| pytest-dev__pytest-7571 | resolved | submitted | 9 | $0.0022 | 20 |  |  |
| scikit-learn__scikit-learn-10908 | resolved | submitted | 10 | $0.0017 | 13 |  |  |
| sphinx-doc__sphinx-9591 | resolved | submitted | 15 | $0.0054 | 14 |  |  |
| sphinx-doc__sphinx-9658 | wrong_fix | submitted | 16 | $0.0044 | 14 |  |  |
| sympy__sympy-13974 | wrong_fix | submitted | 14 | $0.0045 | 16 |  |  |
| sympy__sympy-15976 | wrong_fix | submitted | 16 | $0.0053 | 27 |  |  |
| sympy__sympy-24661 | resolved | submitted | 10 | $0.0024 | 47 |  |  |
