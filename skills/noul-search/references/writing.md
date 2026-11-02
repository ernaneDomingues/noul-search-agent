# Writing support

Read this when the user is writing: planning a manuscript, adding citations, drafting a related-work section,
auditing a draft or cleaning a bibliography. Keep the author's voice: propose edits and explain them; do not
rewrite whole sections unless asked.

## Tools
- **Structure**: `generate_manuscript_blueprint(topic, venue, hypothesis, key_findings)` gives an IMRaD outline with
  paragraph prompts and venue-mandatory sections. Fill it only with the user's content and retrieved literature.
- **Citations**: `recommend_citations(draft_text)` finds claims that need support (`[CITATION NEEDED]`, `\cite{?}`,
  unsupported empirical statements) and proposes 2-3 **candidate** papers per claim, each with a justification that
  quotes its abstract. Read each justification and keep a candidate only if the abstract really supports the claim
  as written; otherwise say the claim needs a different source or softer wording. Cite only papers it or
  `search_papers` returned.
- **Related work**: `synthesize_related_work(paper_ids, proposal)` clusters the papers by approach and drafts a
  comparative section. Check every sentence that states a result against the papers (use `query_fulltexts`), and
  never attribute findings a paper does not report.
- **Draft audit**: `audit_manuscript_draft(draft_input, venue)` returns `signals` (weak or overstated claims,
  notation inconsistencies, reproducibility omissions) quoting the draft, with suggested rewrites, plus a
  checklist. A `detected: unclear` item means the heuristics found nothing, not that it is fine. Prioritise the
  findings and adapt the suggested rewrites to the author's text; do not paste generic example numbers from the
  suggestions into the paper.
- **Bibliography**: `sanitize_bibtex(bibtex_input)` upgrades preprints to their published versions, adds DOIs,
  fixes escapes and duplicates. Report the changes; writing a file needs `output_path` inside the workspace.

## Answer
List the concrete changes you propose (with the original sentence and the revision), the citations you recommend
with the reason each one fits, and what still needs the author's input (results, numbers, their own claims).
