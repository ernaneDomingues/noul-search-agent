# Golden sets

Hand-written, **synthetic** title/abstract pairs (not copied from real papers) labelled with the *true* answer
according to the project's taxonomy — not with whatever the classifier currently returns. Known misses are
therefore expected and are part of the recorded baseline.

| File | Classifier | Labels |
|---|---|---|
| `evidence_design.jsonl` | `noul_core.classify_evidence(title, abstract, pub_types)` | `meta-analysis`, `systematic-review`, `rct`, `review`, `cohort` (any observational or quasi-experimental design), `empirical` (experiments/benchmarks outside clinical trials), `preclinical` (animal or in vitro), `other` (theory, editorials, protocols without results, position papers) |
| `domain.jsonl` | `noul.providers.domain_classifier.resolve_domain(...)` | `computer_science`, `biomedical`, `economics`, `general_stem` |

`baseline.json` stores the metrics measured when a set was last accepted. `tests/test_golden_sets.py` fails when
accuracy or macro-F1 drops below it. After an intentional improvement, refresh it with:

```bash
python tests/test_golden_sets.py --update-baseline
```

Adding cases: append one JSON object per line, keep the label honest, then refresh the baseline in the same PR
and explain the delta in the PR description.
