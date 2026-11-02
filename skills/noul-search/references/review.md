# Reviewing a paper

Read this when the user wants a critical or peer review of a specific paper, a check of its claims or numbers, its
failure modes or its reproducibility. Noul gathers the evidence; the review and the recommendation are yours.

## Workflow
1. **Identify the paper**: `get_paper(id)` with the DOI, arXiv id, PMID or OpenAlex id the user gave (arXiv ids such
   as `arxiv:1706.03762` work). Note `open_access`: without open full text, the tools see only the abstract.
2. **Collect the evidence** (call what fits the paper and the request):
   - `review_paper(paper_id)` - the domain's reviewer personas (CS/AI, biomedical, economics, general STEM) with
     `signals` (strengths, concerns, gaps), `perspectives`, `questions_for_authors`, `missing_experiments_or_controls`
     and the domain `rubric`;
   - `verify_paper_claims(paper_id, claims=[...])` - where each claim's numbers and wording appear: `best_match` and
     `other_matches` with section/page, `numbers_found` / `numbers_missing`, `hedging_cues_missing_in_text`. Without
     `claims`, it checks the headline claims of the abstract;
   - `detect_failure_modes(paper_id)` - candidate limitations, assumptions and settings where a baseline may win;
   - `get_fulltext(id, question)` / `query_fulltexts` - passages to confirm or refute a specific point;
   - for empirical CS/AI: `extract_benchmark_tables`, `analyze_ablation_tables`, `scan_paper_artifacts`,
     `extract_prompts_and_decoding` (decoding and training hyperparameters); `diff_arxiv_versions` for changes
     between versions.
3. **Check the signals before using them.** For every signal you rely on, read its excerpt:
   - a *gap* ("no ablation", "no code", "no equations") is only a pattern that was not found: look for the content
     (`get_fulltext` with a question) before you report it as missing, and drop it if the paper has it;
   - a *concern* may quote a self-comparison or a caption; keep it only if the excerpt supports it;
   - `numbers_missing` can come from a passage cut across lines: re-check with `get_fulltext`.
   Say which signals you discarded and why.
4. **Write the review** in the user's language:
   - **Summary** of the contribution (2-4 sentences);
   - **Claims checked**: a table `Claim | Where (section, page) | Supported? | Note`, where "Supported?" is your call
     from the passages; flag inconsistencies between abstract, tables and text;
   - **Assessment per rubric criterion / reviewer perspective** (theory, empirical evidence, reproducibility, or the
     domain's criteria), each point citing excerpts with section/page;
   - **Recommendation** (e.g. accept / minor / major revision / reject) with the main reasons, and the questions or
     experiments you would ask the authors for;
   - **What the evidence could not show** (abstract-only analysis, tables the tools could not parse).

## The tools never decide
They return no verdict, score or "verified" flag. Do not present a heuristic signal, a `priority_hint` or a
`detected` checklist value as the paper's quality; they are leads you confirmed or rejected.
