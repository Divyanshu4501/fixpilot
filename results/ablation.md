# Ablation table (dev set)

Each row is one configuration on the same dev tasks. Resolved = official SWE-bench harness.

| config | run | model · effort | resolved | rate (95% CI) | $/task | $/resolved | median steps | empty patch | applied | git |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline (bash + submit) | baseline | gpt-6-luna · medium | 14/20 | 70% (48%–85%) | $0.0036 | $0.005 | 11.5 | 0% | 100% | 9cb99f4 |
