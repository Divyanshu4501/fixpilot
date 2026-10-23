# smoke-2

- dataset: `SWE-bench/SWE-bench_Verified` · tasks: `data/dev_ids.txt` (2)
- model: `gpt-6-luna` · effort: `medium` · git: `1307589a9f`
- budgets: 30 steps · $0.5/task · 180 s/command

**Resolved 1/2 = 50%** (95% Wilson CI 9%–91%)

| metric | value |
|---|---|
| total cost | $0.0111 |
| cost per task | $0.0055 |
| cost per resolved | $0.0111 |
| median steps | 15.0 |
| median time per task | 129 s |
| empty-patch rate | 0% |
| patch-applied rate (non-empty patches) | 100% |
| infra failures | 0 |
| patches touching test files | 0 |
| exit reasons | submitted 2 |

## Failure categories

| category | tasks |
|---|---|
| resolved | 1 |
| wrong_fix | 1 |

## Per task

| task | category | exit | steps | cost | patch lines | edits tests |
|---|---|---|---|---|---|---|
| astropy__astropy-14598 | wrong_fix | submitted | 19 | $0.0071 | 17 |  |
| django__django-11206 | resolved | submitted | 11 | $0.0040 | 21 |  |
