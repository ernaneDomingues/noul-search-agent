# Golden sets

Hand-written, **synthetic** title/abstract pairs (not copied from real papers) labelled with the *true* answer
according to the project's taxonomy — not with whatever the classifier currently returns. Known misses are
therefore expected and are part of the recorded baseline.

| File | Classifier | Labels |
|---|---|---|
| `evidence_design.jsonl` | `noul_core.classify_evidence(title, abstract, pub_types)` | `meta-analysis`, `systematic-review`, `rct`, `review`, `cohort` (any observational or quasi-experimental design), `empirical` (experiments/benchmarks outside clinical trials), `preclinical` (animal or in vitro), `other` (theory, editorials, protocols without results, position papers) |
| `domain.jsonl` | `noul.providers.domain_classifier.resolve_domain(...)` | `computer_science`, `biomedical`, `economics`, `general_stem` |
| `evidence_signals.jsonl` + `papers/*.txt` | the analytic tools on a full text (`analyze_ablation_tables`, `scan_paper_artifacts`, `extract_benchmark_tables`, `detect_failure_modes`, `extract_prompts_and_decoding`, `verify_paper_claims`, `get_fulltext`) | per paper: ablation yes/no, code yes/no, number of result tables, no captions reported as failure modes, hyperparameters found, where key claims are located (section/page), and the passage answering each question (recall@1) |

`baseline.json` stores the metrics measured when a set was last accepted. `tests/test_golden_sets.py` fails when
accuracy or macro-F1 drops below it. After an intentional improvement, refresh it with:

```bash
python tests/test_golden_sets.py --update-baseline
```

Adding cases: append one JSON object per line, keep the label honest, then refresh the baseline in the same PR
and explain the delta in the PR description.

## Evidence signals (Phase 3b)

`papers/*.txt` are page texts in the layout a PDF extractor returns (pages separated by form feeds), **written for
these tests**: they reproduce the structure of real papers that broke the heuristics (e.g. `latticeformer.txt`
mimics "Attention Is All You Need": a "Variations" table with `(A)` group lines, three-line headers, FLOPs whose
superscript exponent was lost (`2.3 · 1019`), URLs and words split across lines, a prose line starting with
"Table 2 ...", and abstract/body numbers that disagree), without copying their text. Metrics:
`signals_accuracy`, `hyperparameters_f1`, `claims_located` and `retrieval_recall_at_1`.

`scripts/check_live_papers.py` runs the same checks on the real PDFs (network, manual, not in CI).
