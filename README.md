# Noul Search Agent

Noul is a scientific research co-assistant that runs inside the AI assistant you already use (Claude, ChatGPT, Copilot, Cursor, Gemini and other MCP clients). It searches six open scholarly sources, reads full texts, structures the evidence and helps you review papers, write and prepare submissions.

**Noul has no LLM of its own.** Its tools retrieve and structure evidence (passages with section and page, signals, checklists); the model in your assistant reasons over that evidence and writes the answer. No API key is required.

- **Website and install page:** <https://ernanedomingues.github.io/noul-search-agent/>
- **Releases:** <https://github.com/ernaneDomingues/noul-search-agent/releases>

## Who it is for

| You are | What you get | Start here |
|---|---|---|
| **A researcher, clinician or student** who wants answers grounded in the literature | Cited syntheses, an Evidence Meter weighted by study design, retraction checks, persistent research projects, PDF downloads and Zotero/BibTeX export | [Install without a terminal](#install-without-a-terminal) |
| **An author or reviewer** | Evidence for peer review, checks of numeric claims, citation suggestions, related-work drafts, BibTeX clean-up, rebuttal templates and pre-submission checks | [Install without a terminal](#install-without-a-terminal) |
| **A developer or AI engineer** | An MCP server with 50 typed tools, a CLI, an HTTP/OpenAPI server and a Claude Code plugin with a skill and subagents | [Install for developers](#install-for-developers) |

## What it does

- **Search and weigh the literature.** Multi-source search (OpenAlex, PubMed, Semantic Scholar, Crossref, arXiv, Europe PMC, plus Unpaywall for open-access copies), deduplicated and ranked by study design and citations. The Evidence Meter turns the stances your model assigns into reproducible percentages, raw and weighted by study design.
- **Read and check papers.** Full-text passages with section and page, citation graphs, retraction and expression-of-concern notices, preprint-to-published links, arXiv version diffs, and extraction of benchmarks, ablations, equations, prompts and hardware requirements.
- **Review.** Review rubrics per domain (NeurIPS/ICLR, CONSORT/STROBE/PRISMA, causal econometrics), numeric-claim verification against the paper's own text and failure-mode detection. The tools return signals and excerpts, not verdicts or scores.
- **Write and submit.** Citation recommendations, related-work synthesis, manuscript blueprints and draft audits, BibTeX sanitising, rebuttal templates that never invent results, and pre-submission checks for anonymity and venue requirements.
- **Keep the work.** Research projects record the question, every paper considered with its status and reason, notes and the search history, across sessions and clients. Export to BibTeX, RIS, CSL-JSON, Markdown dossiers, Obsidian notes or Zotero.

Text that comes from papers, PDFs, web pages and reviews is marked as untrusted data, so your assistant does not treat it as instructions.

## Install without a terminal

Open the [**install page**](https://ernanedomingues.github.io/noul-search-agent/install.html) and pick your setup:

- **Claude Desktop, full version:** download `noul.mcpb` and double-click it. Claude Desktop asks for your e-mail (optional) and the Noul folder.
- **Single executable** for Windows, macOS and Linux (no Python needed). Open it and a wizard detects your apps and asks where to install Noul.
- **Claude.ai, Claude Desktop or ChatGPT, nothing to install:** paste the URL of the public read-only connector, once it is live (the install page shows it).
- **VS Code / Cursor:** one-click install buttons, once the public connector is live.

The executables are not digitally signed yet. [`docs/release/signing.md`](docs/release/signing.md) explains how to open them on Windows and macOS and how to verify a download.

## Install for developers

Pick **one** of the options. All of them need [`uv`](https://docs.astral.sh/uv/) except the PyPI route with `pipx`.

```bash
# Claude Code plugin: skill + subagent + /noul-search-config + MCP server
/plugin marketplace add ernaneDomingues/noul-search-agent
/plugin install noul-search-agent@noul

# From source: installs into every supported agent
git clone https://github.com/ernaneDomingues/noul-search-agent && cd noul-search-agent
uv run noul-install --all                    # every supported environment
uv run noul-install antigravity claude       # or specific ones
uv run noul-install --all --dry-run          # only print what would change
uv run noul-install --all --uninstall        # remove everything cleanly

# PyPI (once the package is published)
pipx install noul-search-agent && noul-install --all
uvx noul-search-agent install --all --runner uvx   # agents start the MCP server through uvx
```

The installer uses the current interpreter (`sys.executable`, which also works on Windows) or `--runner uvx`. It writes TOML strings that are safe for Windows paths, creates a `.bak` before it first changes an existing configuration file, and leaves corrupted JSON files untouched (exit code 1).

To add the server to an MCP client by hand, point it at `noul-mcp` (stdio). From a source checkout:

```json
{
  "mcpServers": {
    "noul": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/noul-search-agent", "noul-mcp"],
      "env": { "NOUL_WORKSPACE": "/path/to/your/workspace" }
    }
  }
}
```

## Configuration

On first use, give a contact e-mail. It enables Unpaywall and the polite pools of OpenAlex, Crossref and NCBI. Noul never invents an e-mail.

```bash
/noul-search-config email:you@university.edu              # installed by noul-install
/noul-search-agent:noul-search-config email:you@university.edu   # Claude Code plugin
noul config email:you@university.edu --workspace ~/research --download-dir papers
noul config                                                # show the configuration
noul config --clear                                        # delete the configuration file
noul config --zotero-user-id ID --zotero-api-key-stdin     # Zotero credentials (terminal or environment only)
noul config --zotero-remove-key                            # remove the stored Zotero key
```

Downloads and dossiers are **always written inside the workspace** (default: the current directory; relative paths are relative to it). Paths outside it, hidden files and folders, and Noul's own folder are refused. The workspace and the Zotero credentials can only be changed from the terminal or through environment variables, never by a tool.

| Variable | Purpose |
|---|---|
| `NOUL_EMAIL` | Contact e-mail (alternative to `noul config`) |
| `NOUL_WORKSPACE` | Workspace where downloads and dossiers may be written. Takes priority over the `workspace` saved by `noul config`; with neither, the current folder is used or, if that is the disk root, `~/Documents/Noul` |
| `NOUL_HOME` | Cache and `config.json` (default `~/.cache/noul-search-agent`) |
| `NOUL_DB_PATH` | Path of the SQLite cache database |
| `NOUL_PROJECTS_DB` | Path of the research-projects database (default: `projects.db` next to `config.json`) |
| `NOUL_TOOL_PROFILE` | Tool profile the server exposes (`research`, `review`, `write`, `all`) |
| `NOUL_LOG_LEVEL` | Log level of the `noul` logger (stderr) |
| `NOUL_ALLOW_ANY_PATH=1` | Disables workspace confinement; reserved for isolated tests |
| `NOUL_INSTALL_RUNNER` | Default runner of the installer (`python`, `uvx`) |

## Usage examples

In a chat, just ask: *"What does the literature say about creatine and working memory in healthy adults?"* or *"Review arXiv 2305.14314 as a peer reviewer would."* The same tools are available from the terminal.

The examples use `noul` (installed through pip/pipx). From a source checkout, use `uv run noul`.

### 1. Search the literature
```bash
noul search "creatine supplementation cognitive performance" --types meta-analysis,rct --year-min 2018 --pretty
noul search "retrieval augmented generation hallucination benchmark" --sources openalex,s2,arxiv --pretty
noul search "statin myopathy" --sources europepmc,pubmed --open-access
```
The `tier` field is a transparent citation/venue band (`top`, `high`, `standard`, `emerging`), **not** a JCR/SJR quartile.

### 2. Find similar papers, references and citing papers
```bash
noul related "doi:10.1093/nutrit/nuac064" --relation all --types meta-analysis,rct
```

### 3. Download open-access PDFs (inside the workspace)
```bash
noul download "doi:10.1093/nutrit/nuac064,arxiv:2312.10997" --dir papers
```

### 4. Ask questions over the full text of several papers
```bash
noul ask "doi:10.1093/nutrit/nuac064,pmid:29704637" --question "What daily dosage and duration were used?"
```

### 5. Evidence Meter
Your model classifies the stance of each study; the calculation (raw percentages and percentages weighted by study design) is reproducible:
```bash
noul meter '[{"id":"doi:10.1093/nutrit/nuac064","stance":"supportive"},{"id":"pmid:29704637","stance":"inconclusive"}]'
```

### 6. HTTP / OpenAPI mode
```bash
noul-mcp --http 8765          # or: uv run noul-mcp --http 8765
# MCP: http://127.0.0.1:8765/mcp · OpenAPI: /openapi.json · health: /healthz
```
Security: the server listens on `127.0.0.1` by default and validates the `Host` header (against DNS rebinding). CORS is limited to `NOUL_CORS_ORIGINS`. When `NOUL_HTTP_TOKEN` is set, requests need `Authorization: Bearer` with that token (except `/healthz`); without it, the loopback server is unauthenticated. Tools that write to disk or to an external account (`download_papers`, `save_research_dossier`, `export_obsidian_notes`, `configure_noul`, `zotero_push` and the `project_*` tools that change a project: see the *Writes to disk* column below) are hidden and blocked over HTTP unless `NOUL_HTTP_ALLOW_WRITE=1` **and** a token are set. Other variables: `NOUL_HTTP_HOST`, `PORT`, `NOUL_HTTP_LOG=1`.

### 7. Public connector and Google Cloud Run
The same server can run as a **public read-only connector**: no login, 12 tools from the `research` profile (no writes and no server-side dossiers), stateless transport, a per-instance limit on tool calls, bounded concurrency and per-call caps announced in the schema. Anyone can use it by pasting `https://<service>/mcp` into Claude.ai/Claude Desktop (Connectors) or ChatGPT (developer mode).
```bash
NOUL_PUBLIC=1 noul-mcp --http 8765     # local (127.0.0.1): expose it through an HTTPS tunnel to test it as a connector
docker build -t noul-search-agent . && docker run -p 8080:8080 -e NOUL_PUBLIC=1 noul-search-agent
```
Deployment on Cloud Run (scales to zero, no service-account keys, through GitHub Actions): [`docs/deploy/cloud-run.md`](docs/deploy/cloud-run.md).

Private server with write access, for yourself: `NOUL_HTTP_TOKEN=change-me NOUL_HTTP_ALLOW_WRITE=1 noul-mcp --http 8765`.

## MCP tools

**Research projects.** A project stores the question, every paper considered with its status (`candidate`, `included` or `excluded`) and the reason, the notes and the search history (`search_papers(project=...)` logs each search and adds its results as candidates). It persists across sessions and clients. Tools: `project_create`, `project_list`, `project_get`, `project_add_papers`, `project_set_status`, `project_note`, `project_archive` (hides it from the list, reversible) and `project_delete` (permanent; needs the exact slug and a preview before `confirm: true`). Projects are also available from the CLI (`noul project ...`) and as the MCP resource `noul://project/<slug>`. `save_research_dossier` exports the project of the same name. Write tools are blocked on the HTTP server (unless `NOUL_HTTP_ALLOW_WRITE` is set) and all project tools are hidden on the public connector.

**Tool profiles.** To keep the model's context small, the server exposes one profile: `research` (search, reading, Evidence Meter, research projects, dossiers and downloads: 26 tools), `review` (research + review and technical audit), `write` (research + authoring, BibTeX and submission) or `all` (all 50). Default: `all` for local use (stdio: plugin, CLI, `noul-install`) and `research` on the HTTP/remote server. Change it with `noul-mcp --profile review` or `NOUL_TOOL_PROFILE=write` (an invalid value only logs a warning and falls back to the default).

The MCP server is built on the official SDK (`mcp` 2.x): stdio and Streamable HTTP (`/mcp`), per-tool annotations (read-only, destructive, network access), `structuredContent` (with `outputSchema` on the tools that have a result model), prompts (`noul-research`, `noul-review`, `noul-write`), per-source progress in `search_papers` and, over stdio, a one-time request for the contact e-mail (on the first full-text or PDF request, if none is configured; a refusal is remembered).

The table is generated from the registry (`src/noul/registry.py`) by `scripts/build_site.py`; do not edit it by hand.

<!-- noul:tools:start -->
| Tool | What it does | Profile | Writes to disk |
|---|---|---|---|
| `search_papers` | Search peer-reviewed and preprint literature across all scientific disciplines (OpenAlex, PubMed, Semantic Scholar, Crossref, arXiv, Europe PMC), deduplicated and ranked by study design and citations. | research (all) | only with `project` |
| `clarify_search_scope` | Clarification loop for broad queries: says whether a query is too broad (needs >= 2 of: 5+ meaningful words, boolean operators/quoted phrase, time constraint, method + outcome) and returns 3 domain-specific multiple-choice questions. | research (all) | no |
| `get_paper` | Get full record and abstract of a paper by cached ID, DOI, PMID, OpenAlex ID, arXiv ID, or Semantic Scholar ID. | research (all) | no |
| `detect_paper_domain` | Detect a paper's scientific macro-domain (computer_science, biomedical, economics, general_stem) from arXiv categories, fields of study, PubMed indexing, the journal/venue name and title/abstract vocabulary, explaining the signals used. | research (all) | no |
| `scan_paper_artifacts` | Open-artifact radar: find code repositories (GitHub/GitLab/...), model weights (Hugging Face, Civitai, Kaggle, PyTorch Hub) and datasets (Zenodo, Figshare, OSF, Dataverse, HF datasets) linked from a paper, verify that the links are alive (HEAD, 5s timeout, max 20 URLs) and flag evasive 'available on request' statements. | review | no |
| `get_paper_references` | Citation graph with influence ranking: papers this paper cites (`backward_citations`) and papers citing it (`forward_citations`), with title, year, venue, citation_count and Semantic Scholar's `is_influential` flag (OpenAlex fallback: >= 50 citations). | research (all) | no |
| `compare_papers` | Comparative matrix of 1-10 papers (3-5 typical): for each paper extracts the core problem/hypothesis, methodology (design + domain + method sentence), datasets/benchmarks/population, key results/metrics (headline finding, %, p-values, AUC, F1, HR, ...) and declared limitations, then returns a Markdown table plus convergence and divergence summaries. | review | no |
| `get_thematic_insights` | Thematic synthesis of 1-20 papers across 3 cross-cutting axes (no isolated per-paper summaries): `consolidated_consensuses` (shared concepts, benchmarks and highest-level evidence), `methodological_tensions` (evidence-pyramid gaps, dataset/scale differences, open-artifact asymmetry, mixed results) and `emerging_future_work` (cross-referenced limitations and future work). | review | no |
| `generate_research_hypotheses` | Autonomous research gap & hypothesis engine: cross-analyzes 2-15 papers to detect methodological contradictions (`contradictions_found`) and unexplored synergies where one paper's method mitigates another's limitation (`unexplored_complementarities`), then formulates 1-`max_hypotheses` structured, testable `ResearchHypothesis` proposals with `source_papers` (>= 2), `proposed_methodology`, `required_baselines`, `target_datasets_or_population`, and `evaluation_metrics`. | review | no |
| `export_obsidian_notes` | Export papers as atomic Obsidian/Markdown notes (`<Year> - <FirstAuthor> - <ShortTitle>.md`) into `vault_dir` (inside the workspace; default ./obsidian): YAML frontmatter (title, paper_id, doi, year, authors, venue, domain, evidence_level, citations, open_access, has_code, has_weights, tags), TL;DR, key findings & methodology, open artifacts, [[wikilinks]] between the exported notes and a BibTeX block. | write | yes |
| `suggest_boolean_queries` | Dynamic search expansion: mine acronyms (e.g. | research (all) | no |
| `extract_benchmark_tables` | Benchmark table extractor: detects Markdown, LaTeX (tabular) and plain-text/PDF tables plus inline `Benchmark: value` pairs in the paper's open-access full text (abstract fallback), keeps the exact model-row x metric-column correspondence, normalizes metric names (gsm-8k -> GSM8K, human-eval -> HumanEval, acc -> Accuracy, ppl -> Perplexity, ...) and converts values like '84.5%' or '84.5 ± 0.3' to numbers. | review | no |
| `estimate_hardware_specs` | Hardware feasibility / inference footprint: detects the model size (7B, 1.5B, 350M, 8x7B MoE, '671B parameters with 37B activated', family defaults) and architecture (dense, moe, encoder_only, rnn_ssm), then computes FP16/INT8/INT4 weights, KV cache (2 x layers x kv_heads x head_dim x context x bytes) and recommended minimum VRAM (+15% overhead), extracts the authors' reported training hardware (e.g. | review | no |
| `verify_paper_claims` | Claim grounding with strict attribution: locates each claim (or, when `claims` is empty, the numeric/comparative sentences auto-extracted from the abstract/text) in the paper's literal text, indexed by page and table/section. | review | no |
| `extract_paper_math` | Math & pseudocode extractor: finds equations (\begin{equation\|align}, $$..$$, \[..\], inline $a = b$, plain-text 'L_total = ...' / 'Attention(Q, K, V) = ...') and algorithms (\begin{algorithm}, 'Algorithm N:' blocks). | review | no |
| `analyze_ablation_tables` | Ablation analyzer: locates ablation tables/lists (rows 'Full model' / 'Ours' / 'Baseline' and variants 'w/o X', 'without X', '- X', '+ X', 'no X'), takes the full model (else baseline) as reference and computes, per component and metric, delta and delta_percent, flags is_marginal (\|Δ%\| < 1%), ranks components by \|Δ%\| and emits warnings_and_alerts for marginal components and for a single component accounting for >= 60% of the total reported gain. | review | no |
| `extract_prompts_and_decoding` | Prompt & inference protocol extractor: finds prompt templates (after 'System Prompt:', 'User Prompt:', 'Prompt Template:', 'Instruction:', in code fences/quotes, or text with {variable}, {{variable}}, <context>, "Let's think step by step", "You are an expert") keeping placeholders verbatim and listing `variables`; extracts decoding hyperparameters (temperature, top_p, top_k, max_tokens, repetition_penalty, beam_size, seed, decoding_strategy) with literal evidence, and the JSON output schemas shown in the paper. | review | no |
| `detect_failure_modes` | Failure mode & critical assumption detector: scans Limitations, Error Analysis, Failure Cases, Discussion, Threats to Validity and Appendix (plus abstract/body) to classify failure modes into long_context, noise_sensitivity, out_of_distribution, latency_or_compute, sample_or_population_bias and general_limitation (as `signals` with the triggering sentence and a priority hint), extracts critical_assumptions, baseline_superiority_scenarios (from text and from benchmark tables where a baseline beats Ours) and operational_limits. | review | no |
| `diff_arxiv_versions` | arXiv temporal version diff analyzer: fetches and compares two revisions of an arXiv preprint (e.g. | review | no |
| `review_paper` | Multi-persona peer-review committee: detects the paper's scientific domain (or uses `domain` override) and runs 3 specialized reviewer personas (NeurIPS/ICLR Theoretical, Skeptical Empirical, and Systems/Reproducibility for CS/AI; CONSORT/STROBE Clinical Trial Methodologist, Biostatistician, and Translational Safety Reviewer for Biomedical; Causal Identification Econometrician, Robustness Auditor, and Policy Economist for Economics). | review | no |
| `review_paper_scientific` | Alias for review_paper: evidence for a domain-adaptive 3-persona scientific peer review; no verdict - you judge with the rubric. | review | no |
| `scaffold_implementation` | Polyglot code & experimental protocol scaffolder: translates a paper's mathematical formulations, architecture, econometric specification or clinical trial design into executable, ast-validated code + unit tests (PyTorch `nn.Module` with shape assertions for CS/AI; Statsmodels or R `fixest` panel + robustness matrix for Economics; CONSORT/STROBE operational checklist + ITT analytical pipeline for Biomedical/STEM). | review | no |
| `scaffold_paper_code` | Alias for scaffold_implementation: generates PyTorch, Statsmodels/R, or Clinical Protocol code + unit tests. | review | no |
| `recommend_citations` | Contextual citation recommender & infiller: scans a draft paragraph or file (.md/.tex) for explicit `[CITATION NEEDED]` / `\cite{?}` markers and unsubstantiated empirical claims, retrieves up to `max_per_claim` candidate supporting papers per claim with a justification quoting each abstract (check that it really backs the claim; candidates known to be retracted - from the search sources or Crossref - are dropped with a warning), inserts inline LaTeX or Markdown citations, and generates a deduplicated BibTeX block. | write | no |
| `synthesize_related_work` | Narrative Related Work synthesizer: clusters 1-20 papers into thematic approach families (Architectural & Representation Foundations, Training/Optimization/Causal Identification, Efficiency/Scaling/Empirical Evaluation), writes comparative academic prose (While X..., Y..., whereas Z... | write | no |
| `generate_manuscript_blueprint` | IMRaD manuscript blueprint generator: generates a venue-adapted (neurips, icml, iclr, acl, ieee, nature, lancet, aer, general) structural template with paragraph-by-paragraph writing guidance and required elements in Markdown or LaTeX. | write | no |
| `audit_manuscript_draft` | Draft manuscript auditor: audits a draft (.md/.tex in workspace or inline text) for weak or unsubstantiated claims, mathematical/terminological inconsistencies ($L$ vs $\mathcal{L}$), and reproducibility omissions (seeds, hyperparameters, sample size N, error bars, compute hardware), as signals quoting the draft with suggested rewrites, a checklist and a rubric; no assessment. | write | no |
| `sanitize_bibtex` | BibTeX sanitizer & preprint-to-published resolver: parses `.bib` files or text, upgrades arXiv/CoRR preprints (`@misc`) to their definitive peer-reviewed conference (`@inproceedings`) or journal (`@article`) venue with DOI, removes duplicates keeping the cleanest key, standardizes authors, escapes unescaped LaTeX (`&`, `%`, `_`), and protects acronyms (`{LLM}`, `{GPU}`). | write | only with `output_path` |
| `parse_and_draft_rebuttal` | Peer-review Rebuttal Copilot: segments raw reviewer comments (`Reviewer 1`, `Reviewer 2`, ...) into numbered actionable points, classifies each objection, flags requests requiring new experiments (`requires_new_experiment=True`), and builds a rebuttal skeleton in Markdown and LaTeX whose responses are templates with [TODO] placeholders: help the author fill them with their real results, never invent results. | write | no |
| `audit_submission_readiness` | Pre-Submission & Desk-Reject Gatekeeper: runs a 4-stage audit on a `.tex`, `.md`, or `.pdf` manuscript for the target `venue` — (1) Double-Blind Anonymity Audit (non-anonymous GitHub/HF URLs, `\author` blocks, PDF metadata, first-person self-citations), (2) Venue Checklist Engine, (3) Overclaiming Scanner, and (4) Mandatory Sections Check - anonymity/overclaiming findings as signals, checklist and sections as a checklist (detected yes/no) and suggested actions; no readiness score: you judge readiness with the returned rubric. | write | no |
| `get_fulltext` | Fetch open-access full text (via OpenAlex, EuropePMC, arXiv, Unpaywall) and return the passages most relevant to `question` (BM25), each with its section and, when the source has real pages, its page. | research (all) | no |
| `query_fulltexts` | Query across multiple papers simultaneously for a specific question (uses full text when Open Access or abstract fallback). | research (all) | no |
| `citation_graph` | Papers this paper cites (references) and papers citing it (OpenAlex), automatically cached for follow-up. | research (all) | no |
| `related_papers` | Find papers semantically similar to the target paper, papers it cites (references), or papers citing it. | research (all) | no |
| `download_papers` | Download Open Access PDFs for one or more paper IDs or DOIs into `output_dir` (inside the workspace; default: configured download_dir, ./papers). | research (all) | yes |
| `export_papers` | Export papers as bibtex, ris, csv or csl-json (imported by Zotero, Mendeley and Pandoc), given `ids` or a research `project` (its included papers, or those of `status`). | research (all) | no |
| `zotero_push` | Create papers in the user's Zotero library (Web API, needs a key with write access), given `ids` or a research `project`, optionally in an existing `collection`. | research (all) | yes |
| `save_research_dossier` | Save a persistent research dossier (README.md, papers.json, references.bib) in `output_dir` (inside the workspace; default: configured research_dir, ./research) so the user can converse over the research documentation. | research (all) | yes |
| `list_research_dossiers` | List saved research dossiers available for follow-up conversation. | research (all) | no |
| `get_research_dossier` | Load a saved research dossier (Markdown report and paper metadata) by slug or topic. | research (all) | no |
| `configure_noul` | View (no arguments) or update the persistent Noul configuration: contact email (enables Unpaywall and the OpenAlex/Crossref polite pool) and default download/research folders. | research (all) | yes |
| `evidence_meter` | Compute the Evidence Meter deterministically from YOUR stance classification of each paper: raw counts and percentages plus study-design-weighted percentages (meta-analysis 1.0 … preclinical 0.2). | research (all) | no |
| `project_create` | Create a persistent research project for a question. | research (all) | yes |
| `project_list` | List the user's research projects, most recently updated first, with paper counts per status. | research (all) | no |
| `project_get` | Get a research project: its question, papers (included first, each with status and reason), notes and search history (`searches`: each query logged with search_papers(project=...), its filters and how many papers it found, returned and added). | research (all) | no |
| `project_add_papers` | Add papers (ids, DOIs, PMIDs or arXiv ids from search_papers) to a research project with a status (candidate by default) and an optional reason. | research (all) | yes |
| `project_set_status` | Include or exclude papers of a research project, or move them back to candidate, recording why: the inclusion criterion they meet or fail. | research (all) | yes |
| `project_note` | Add a note to a research project, optionally attached to one of its papers. | research (all) | yes |
| `project_archive` | Archive a research project the user has finished or set aside: it leaves project_list (unless `include_archived`) but nothing is deleted, and it stays readable and writable by slug. | research (all) | yes |
| `project_delete` | Permanently delete a research project: its paper statuses and reasons, notes and search history. | research (all) | yes |
<!-- noul:tools:end -->

## Repository layout

| Path | Role |
|---|---|
| `src/noul/search/`, `src/noul/providers/` | Search engine, one module per source, deduplication and ranking; domain classifier, artifact radar, PDF/table parser and arXiv version client |
| `src/noul/evidence/`, `src/noul/library/`, `src/noul/net/` | Study-design classification and impact bands; full text, citation graph, downloads and export; HTTP with SSRF protection and SQLite cache |
| `src/noul/registry.py`, `src/noul/api.py`, `src/noul/schemas/` | The tool registry (`@tool`), the 50 tool functions and their Pydantic input models, shared by MCP, HTTP/OpenAPI and the CLI (`noul tool <name> --args '<json>'`) |
| `src/noul/server/` | MCP server (`mcp.py`) and hardened HTTP server (`http.py`) |
| `src/noul/cli/`, `src/noul/install.py` | `noul` CLI (`python -m noul`) and the user-level installer `noul-install` |
| `skills/noul-search/`, `agents/`, `commands/`, `.claude-plugin/` | Skill (router, per-journey references, evals), subagent templates, the `/noul-search-config` command and the Claude Code plugin |
| `site/`, `scripts/build_site.py` | Website published on GitHub Pages; the tool catalog and the install page are generated |
| `packaging/`, `scripts/build_mcpb.py`, `Dockerfile` | Single-file executable, Claude Desktop extensions (`.mcpb`) and the container image |

## Development

```bash
uv sync --extra dev
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pytest -p no:cacheprovider --cov      # offline tests with a coverage floor
.venv/bin/python scripts/build_site.py --check             # site catalog in sync with the tools
.venv/bin/python tests/test_golden_sets.py --report        # classifier metrics
```

Guidance for coding agents is in [`CLAUDE.md`](CLAUDE.md). Release notes are on the [releases page](https://github.com/ernaneDomingues/noul-search-agent/releases).

## Limitations and disclaimer

- The **Evidence Meter (Noul Meter)** is a heuristic indicator of the distribution of the studies found, not a statistical meta-analysis.
- Tools return evidence produced by heuristics (classifiers, pattern-based extraction). They can miss things and produce false positives; check signals against the quoted excerpts.
- Literature summaries on health topics are for information and academic purposes only and are not medical advice.
- Independent project inspired by the principles of evidence-based medicine and by academic tools such as Consensus and Elicit, with no affiliation with or endorsement by Consensus NLP Inc.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
