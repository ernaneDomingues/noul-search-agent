---
name: noul-search
description: Answer scientific and academic research questions across all disciplines (Medicine, CS/AI, Economics, Psychology, Engineering, Natural & Social Sciences) with evidence from peer-reviewed literature and preprints (Evidence Meter / Noul Meter, study hierarchy, cited synthesis, PDF download, similar/cited paper discovery, and persistent research dossiers).
---

# Noul Search

Tools: MCP server `noul` (`search_papers`, `get_paper`, `clarify_search_scope`, `detect_paper_domain`, `get_paper_references`, `scan_paper_artifacts`, `get_fulltext`, `query_fulltexts`, `citation_graph`, `related_papers`, `download_papers`, `save_research_dossier`, `list_research_dossiers`, `get_research_dossier`, `export_papers`, `evidence_meter`, `configure_noul`).
If MCP is unavailable, use the CLI: `python3 ${CLAUDE_PLUGIN_ROOT}/noul_cli.py search "<query>"` (also `clarify`, `domain`, `references`, `artifacts`, `paper`, `fulltext`, `ask`, `graph`, `related`, `download`, `export`, `dossier`, `meter`, `config`).

## Setup (once)
- If the user asks to configure Noul (`/noul-search-config`), call `configure_noul` with their email, workspace, or folders; call it without arguments to show the current configuration.
- A contact email enables Unpaywall and the OpenAlex/Crossref polite pool. Never invent one. If `email_source` is `none`, you may suggest `/noul-search-config email:you@example.com` once.
- Files (PDFs and dossiers) are written only inside the configured workspace (default: the current directory).

## Initial Research Workflow
1. **Frame** the question and identify the discipline:
   - **Broad query?** (e.g. "LLM quantization", "creatina") → call `clarify_search_scope(query)`, ask the user its 2–3 multiple-choice questions, then call it again with `answers` and search with `refined_query` + `recommended_filters`. Skip this when the query is already specific or the user asked for a quick answer.
   - Each paper carries a `domain` (`computer_science`, `biomedical`, `economics`, `general_stem`); use `detect_paper_domain` to see why, and `search_papers(domain=...)` to restrict results.
   - **Yes/no or directional question** ("does X improve/cause/outperform Y?") → produce an **Evidence Meter (Noul Meter)**.
   - **Open/exploratory question** → skip the meter and synthesize key themes and methodologies.
   - **Select appropriate sources**:
     - *Biomedical / Health / Psychology*: `["openalex", "pubmed", "europepmc", "s2"]`, prioritize `study_types: ["meta-analysis", "systematic-review", "rct"]` first, then widen to `cohort`/`review`.
     - *Computer Science / AI / Physics / Math*: `["openalex", "s2", "arxiv", "crossref"]`, include `empirical`, `review`, `systematic-review`.
     - *Economics / Social Sciences / Engineering*: `["openalex", "s2", "crossref"]`, include `meta-analysis`, `cohort` (panel/quasi-experimental/diff-in-diff), `empirical`, `review`.
2. **Search** in English using 2–4 complementary phrasings (synonyms, population/system, outcome/metric).
3. **Classify** each relevant paper from its abstract or full text: `supportive (yes)` / `inconclusive (possibly)` / `contrary (no)` relative to the question, with a one-sentence finding. Never invent results absent from the retrieved text.
4. **Weigh** by study design (`meta-analysis` > `systematic-review` > `rct` > `review` > `cohort` > `empirical` > `preclinical`), citation/venue band (`tier`: top/high/standard/emerging, not a JCR quartile), citations, and recency. Flag `preclinical` (animal/in vitro) evidence as not directly applicable to humans.
5. **Deepen** when needed:
   - Use `query_fulltexts` or `get_fulltext` to check exact methods, sample sizes, dosages, or benchmarks across key papers.
   - Use `related_papers` (`relation: "all" | "similar" | "references" | "citing"`) to find landmark studies, semantically close papers, or contradicting evidence.
   - Use `get_paper_references(paper_id, direction, sort_by="influential")` to walk the citation graph by influence; every returned `paper_id` can be chained into `get_paper`, `get_paper_references` or `download_papers`.
   - For CS/AI (reproducibility), use `scan_paper_artifacts(id)` to check for live code, weights and datasets; report `evasive_availability_statement` ("available on request") as a reproducibility limitation.
6. **Compute the meter** (yes/no questions): call `evidence_meter(items=[{"id": "<paper_id>", "stance": "supportive|inconclusive|contrary"}, ...])` with your classifications. Report its `raw_percentages` and `weighted_percentages`; do not compute percentages yourself.
7. **Answer** in the user's language using this structure:
   - **Evidence Meter (Noul Meter)**: `Supportive N% · Inconclusive N% · Contrary N%` from `evidence_meter` (state total classified papers $N$ and whether the design-weighted percentages agree with the raw count). Note that it is a literature heuristic, not a pooled meta-analysis.
   - **Synthesis** (150–300 words) citing sources as `[n]`.
   - **Key Studies Snapshot (Table)**: `| [n] Study | Design | Sample / Dataset | Main Finding |`.
   - **Evidence Quality & Limitations**: study designs, sample sizes, heterogeneity, gaps.
   - **Papers**: `[n] Authors (year). Title. Venue. DOI (id: <paper_id>) — evidence level [OA if open_access]`.
8. **Persist & Offer Follow-ups**:
   - Save the synthesis with `save_research_dossier` so the user has a permanent Markdown dossier (`./research/<slug>/README.md` inside the workspace) to converse over.
   - Remind the user they can ask follow-up questions across the papers (`query_fulltexts`), request **related/cited papers** (`related_papers`), **download Open Access PDFs** (`download_papers`), or **export citations** (`export_papers` as BibTeX/RIS/CSV).

## Conversational Follow-up Workflow
- **"Tell me more about method/dosage/results in papers X and Y"**: Call `query_fulltexts(ids=[...], question="...")` and answer with exact excerpts.
- **"Find papers similar to [n] or cited by [n]"**: Call `related_papers(id="...", relation="all" | "similar" | "references" | "citing")` and present the top matches with their evidence levels.
- **"Download the PDFs of these papers"**: Call `download_papers(ids=[...])` (optionally `output_dir` inside the workspace) and report the saved file paths (or official DOI links if paywalled).

## Rules
- Cite only papers returned by the tools. Report tool warnings (e.g., if a source timed out).
- Medical/health questions: add a brief note that this is a literature summary, not medical advice.
