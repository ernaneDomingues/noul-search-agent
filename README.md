# noul-search-agent

Agente autônomo de pesquisa científica, inteligência documental e copiloto de escrita acadêmica (*Co-Scientist*) multidisciplinar com classificação de nível de evidência, **Evidence Meter (Noul Meter)** determinístico, extração profunda de tabelas/equações/VRAM/ablação/prompts, revisão crítica *multi-persona* adaptável por domínio, geração poliglota de código e protocolos (`PyTorch`, `Statsmodels`, `R`, `CONSORT/STROBE`), copiloto de autoria e submissão (`Citation Infiller`, `Related Work Synthesizer`, `IMRaD Blueprint & Auditor`, `BibTeX Sanitizer`, `Rebuttal Copilot` e `Submission Gatekeeper`), download de PDFs Open Access e dossiês de pesquisa em Markdown. Funciona como **skill + subagente + servidor MCP (41 tools) + servidor HTTP/OpenAPI + CLI** para **Antigravity**, **Claude Code**, **Claude Desktop**, **GitHub Copilot**, **Codex CLI**, **Gemini CLI** e **ChatGPT Actions**.

- **Portal & Documentação Pública**: [https://ernanedomingues.github.io/noul-search-agent/](https://ernanedomingues.github.io/noul-search-agent/)

Zero chaves de API obrigatórias e nenhum LLM próprio: o LLM do agente que você já usa classifica a postura dos estudos e redige a síntese citada. Requer Python 3.10+ com `pydantic` e `pypdf`; o caminho recomendado é o [`uv`](https://docs.astral.sh/uv/), que instala o Python e as dependências automaticamente.

## Componentes
| Arquivo | Função |
|---|---|
| `src/noul/search/`, `src/noul/providers/` | Motor de busca (`OpenAlex`, `PubMed`, `Semantic Scholar`, `Crossref`, `arXiv`, `Europe PMC`, `Unpaywall`), um módulo por fonte, deduplicação e ranqueamento; classificador de domínio, radar de artefatos, parser de PDF/tabelas e cliente temporal do arXiv |
| `src/noul/evidence/`, `src/noul/library/`, `src/noul/net/` | Classificação de desenho de estudo e faixas de impacto; texto completo, grafo de citações, downloads e exportação; HTTP com proteção SSRF e cache SQLite |
| `src/noul/registry.py`, `src/noul/api.py`, `src/noul/schemas/tool_inputs.py` | **41 ferramentas MCP**: registro único (`@tool`), funções e modelos de entrada Pydantic, usados pelo MCP, pelo HTTP/OpenAPI e pela CLI (`noul tool <nome> --args '<json>'`) |
| `src/noul/tools/`, `src/noul/schemas/`, `src/noul/prompts/` | Ferramentas das Fases 1 a 6, contratos Pydantic v2 e rubricas (`NeurIPS/ICLR`, `CONSORT/STROBE/PRISMA`, `Econometria Causal`) |
| `src/noul/server/` | Servidor MCP no SDK oficial (`mcp.py`: stdio, *prompts*, progresso, *elicitation*) e servidor HTTP endurecido (`http.py`: Streamable HTTP em `/mcp`, OpenAPI em `/openapi.json`, `/healthz`, `POST /tools/<nome>`); `legacy.py` = servidor v1 (`--legacy`) |
| `src/noul/cli/` | CLI (`noul`, ou `python -m noul`) para terminal (`--pretty`, `search --interactive`) ou agentes sem MCP; também expõe `mcp` e `install` |
| `src/noul/install.py` | Instalador idempotente em nível de usuário (`noul-install`) |
| `noul_core.py`, `noul_tools.py`, `noul_mcp.py`, `noul_cli.py`, `install.py` | *Shims* de compatibilidade (obsoletos; removidos na 2.0.0) |
| `site/` & `scripts/build_site.py` | Landing Page responsiva (Modo Claro/Escuro), simulador de terminal interativo e catálogo sincronizado das 41 ferramentas MCP publicado via GitHub Pages (`.github/workflows/deploy-pages.yml`) |
| `skills/noul-search/SKILL.md` | Fluxo de pesquisa, auditoria técnica, revisão por pares, autoria científica e conversa sobre os artigos |
| `agents/`, `commands/` | Subagentes/extensões (Claude, Copilot, Codex, Gemini) e o comando `/noul-search-config` |
| `.claude-plugin/`, `plugin.json`, `pyproject.toml`, `package.json` + `bin/noul.js`, `Dockerfile` | Distribuição: plugin do Claude Code / Antigravity, PyPI (`pip`/`pipx`/`uvx`), npm (`npx`) e contêiner (Cloud Run) |

## Ferramentas MCP

**Perfis de ferramentas.** Para não sobrecarregar o contexto do modelo, o servidor expõe um perfil: `research` (núcleo de busca, leitura, Evidence Meter, dossiês e downloads, 17 ferramentas), `review` (núcleo + revisão e auditoria técnica), `write` (núcleo + autoria, BibTeX e submissão) ou `all` (as 41). Padrão: `all` no uso local (stdio: plugin, CLI, `noul-install`) e `research` no servidor HTTP/remoto. Mude com `noul-mcp --profile review` ou `NOUL_TOOL_PROFILE=write` (um valor inválido em `NOUL_TOOL_PROFILE` só gera um aviso e usa o padrão).

O servidor MCP usa o SDK oficial (`mcp` 2.x): stdio e Streamable HTTP (`/mcp`), *annotations* por ferramenta (somente leitura, destrutiva, acesso à rede), `structuredContent` (com `outputSchema` nas ferramentas que já têm modelo de resultado), *prompts* (`noul-research`, `noul-review`, `noul-write`), progresso por fonte em `search_papers` e, em stdio, um pedido único do e-mail de contato (na primeira busca de texto completo ou PDF, se ainda não configurado; uma recusa é lembrada). O servidor v1 continua disponível com `noul-mcp --legacy` até a 2.0.0.

Gerado a partir do registro (`src/noul/registry.py`) por `scripts/build_site.py`; não edite à mão.

<!-- noul:tools:start -->
| Ferramenta | O que faz | Perfil | Grava em disco |
|---|---|---|---|
| `search_papers` | Search peer-reviewed and preprint literature across all scientific disciplines (OpenAlex, PubMed, Semantic Scholar, Crossref, arXiv, Europe PMC), deduplicated and ranked by study design and citations. | research (todos) | não |
| `clarify_search_scope` | Clarification loop for broad queries: says whether a query is too broad (needs >= 2 of: 5+ meaningful words, boolean operators/quoted phrase, time constraint, method + outcome) and returns 3 domain-specific multiple-choice questions. | research (todos) | não |
| `get_paper` | Get full record and abstract of a paper by cached ID, DOI, PMID, OpenAlex ID, arXiv ID, or Semantic Scholar ID. | research (todos) | não |
| `detect_paper_domain` | Detect a paper's scientific macro-domain (computer_science, biomedical, economics, general_stem) from arXiv categories, fields of study, PubMed indexing, the journal/venue name and title/abstract vocabulary, explaining the signals used. | research (todos) | não |
| `scan_paper_artifacts` | Open-artifact radar: find code repositories (GitHub/GitLab/...), model weights (Hugging Face, Civitai, Kaggle, PyTorch Hub) and datasets (Zenodo, Figshare, OSF, Dataverse, HF datasets) linked from a paper, verify that the links are alive (HEAD, 5s timeout, max 20 URLs) and flag evasive 'available on request' statements. | review | não |
| `get_paper_references` | Citation graph with influence ranking: papers this paper cites (`backward_citations`) and papers citing it (`forward_citations`), with title, year, venue, citation_count and Semantic Scholar's `is_influential` flag (OpenAlex fallback: >= 50 citations). | research (todos) | não |
| `compare_papers` | Comparative matrix of 1-10 papers (3-5 typical): for each paper extracts the core problem/hypothesis, methodology (design + domain + method sentence), datasets/benchmarks/population, key results/metrics (headline finding, %, p-values, AUC, F1, HR, ...) and declared limitations, then returns a Markdown table plus convergence and divergence summaries. | review | não |
| `get_thematic_insights` | Thematic synthesis of 1-20 papers across 3 cross-cutting axes (no isolated per-paper summaries): `consolidated_consensuses` (shared concepts, benchmarks and highest-level evidence), `methodological_tensions` (evidence-pyramid gaps, dataset/scale differences, open-artifact asymmetry, mixed results) and `emerging_future_work` (cross-referenced limitations and future work). | review | não |
| `generate_research_hypotheses` | Autonomous research gap & hypothesis engine (#18): cross-analyzes 2-15 papers to detect methodological contradictions (`contradictions_found`) and unexplored synergies where one paper's method mitigates another's limitation (`unexplored_complementarities`), then formulates 1-`max_hypotheses` structured, testable `ResearchHypothesis` proposals with `source_papers` (>= 2), `proposed_methodology`, `required_baselines`, `target_datasets_or_population`, and `evaluation_metrics`. | review | não |
| `export_obsidian_notes` | Export papers as atomic Obsidian/Markdown notes (`<Year> - <FirstAuthor> - <ShortTitle>.md`) into `vault_dir` (inside the workspace; default ./obsidian): YAML frontmatter (title, paper_id, doi, year, authors, venue, domain, evidence_level, citations, open_access, has_code, has_weights, tags), TL;DR, key findings & methodology, open artifacts, [[wikilinks]] between the exported notes and a BibTeX block. | write | sim |
| `suggest_boolean_queries` | Dynamic search expansion: mine acronyms (e.g. | research (todos) | não |
| `extract_benchmark_tables` | Benchmark table extractor: detects Markdown, LaTeX (tabular) and plain-text/PDF tables plus inline `Benchmark: value` pairs in the paper's open-access full text (abstract fallback), keeps the exact model-row x metric-column correspondence, normalizes metric names (gsm-8k -> GSM8K, human-eval -> HumanEval, acc -> Accuracy, ppl -> Perplexity, ...) and converts values like '84.5%' or '84.5 ± 0.3' to numbers. | review | não |
| `estimate_hardware_specs` | Hardware feasibility / inference footprint: detects the model size (7B, 1.5B, 350M, 8x7B MoE, '671B parameters with 37B activated', family defaults) and architecture (dense, moe, encoder_only, rnn_ssm), then computes FP16/INT8/INT4 weights, KV cache (2 x layers x kv_heads x head_dim x context x bytes) and recommended minimum VRAM (+15% overhead), extracts the authors' reported training hardware (e.g. | review | não |
| `verify_paper_claims` | Evidence grounding with strict attribution: checks each claim (or, when `claims` is empty, the numeric/comparative sentences auto-extracted from the abstract/text) against the paper's literal text, indexed by page and table/section. | review | não |
| `extract_paper_math` | Math & pseudocode extractor: finds equations (\begin{equation\|align}, $$..$$, \[..\], inline $a = b$, plain-text 'L_total = ...' / 'Attention(Q, K, V) = ...') and algorithms (\begin{algorithm}, 'Algorithm N:' blocks). | review | não |
| `analyze_ablation_tables` | Ablation analyzer: locates ablation tables/lists (rows 'Full model' / 'Ours' / 'Baseline' and variants 'w/o X', 'without X', '- X', '+ X', 'no X'), takes the full model (else baseline) as reference and computes, per component and metric, delta and delta_percent, flags is_marginal (\|Δ%\| < 1%), ranks components by \|Δ%\| and emits warnings_and_alerts for marginal components and for a single component accounting for >= 60% of the total reported gain. | review | não |
| `extract_prompts_and_decoding` | Prompt & inference protocol extractor: finds prompt templates (after 'System Prompt:', 'User Prompt:', 'Prompt Template:', 'Instruction:', in code fences/quotes, or text with {variable}, {{variable}}, <context>, "Let's think step by step", "You are an expert") keeping placeholders verbatim and listing `variables`; extracts decoding hyperparameters (temperature, top_p, top_k, max_tokens, repetition_penalty, beam_size, seed, decoding_strategy) with literal evidence, and the JSON output schemas shown in the paper. | review | não |
| `detect_failure_modes` | Failure mode & critical assumption detector: scans Limitations, Error Analysis, Failure Cases, Discussion, Threats to Validity and Appendix (plus abstract/body) to classify failure modes into long_context, noise_sensitivity, out_of_distribution, latency_or_compute, sample_or_population_bias and general_limitation (with severity high/medium/low), extracts critical_assumptions, identifies baseline_superiority_scenarios (both from text and from benchmark tables where a baseline beats Ours), and extracts operational_limits. | review | não |
| `diff_arxiv_versions` | arXiv temporal version diff analyzer: fetches and compares two revisions of an arXiv preprint (e.g. | review | não |
| `review_paper` | Multi-persona peer-review committee (#17, #20): detects the paper's scientific domain (or uses `domain` override) and runs 3 specialized reviewer personas (NeurIPS/ICLR Theoretical, Skeptical Empirical, and Systems/Reproducibility for CS/AI; CONSORT/STROBE Clinical Trial Methodologist, Biostatistician, and Translational Safety Reviewer for Biomedical; Causal Identification Econometrician, Robustness Auditor, and Policy Economist for Economics) consolidated by a Meta-Reviewer into a PeerReviewReport with verdict, scores, cited flaws/omissions, and questions. | review | não |
| `review_paper_scientific` | Alias for review_paper: domain-adaptive 3-persona scientific peer-review committee (#17, #20). | review | não |
| `scaffold_implementation` | Polyglot code & experimental protocol scaffolder (#16, #21): translates a paper's mathematical formulations, architecture, econometric specification or clinical trial design into executable, ast-validated code + unit tests (PyTorch `nn.Module` with shape assertions for CS/AI; Statsmodels or R `fixest` panel + robustness matrix for Economics; CONSORT/STROBE operational checklist + ITT analytical pipeline for Biomedical/STEM). | review | não |
| `scaffold_paper_code` | Alias for scaffold_implementation (#16, #21): generates PyTorch, Statsmodels/R, or Clinical Protocol code + unit tests. | review | não |
| `recommend_citations` | Contextual citation recommender & infiller (#22): scans a draft paragraph or file (.md/.tex) for explicit `[CITATION NEEDED]` / `\cite{?}` markers and unsubstantiated empirical claims, retrieves 2-3 supporting papers per claim with explicit Portuguese justifications ('Este artigo de X et al. | write | não |
| `synthesize_related_work` | Narrative Related Work synthesizer (#24): clusters 1-20 papers into thematic approach families (Architectural & Representation Foundations, Training/Optimization/Causal Identification, Efficiency/Scaling/Empirical Evaluation), writes comparative academic prose (While X..., Y..., whereas Z... | write | não |
| `generate_manuscript_blueprint` | IMRaD manuscript blueprint generator (#23 Mode 1): generates a venue-adapted (neurips, icml, iclr, acl, ieee, nature, lancet, aer, general) structural template with paragraph-by-paragraph writing guidance and required elements in Markdown or LaTeX. | write | não |
| `audit_manuscript_draft` | Draft manuscript auditor (#23 Mode 2): audits a draft (.md/.tex in workspace or inline text) for weak or unsubstantiated claims, mathematical/terminological inconsistencies ($L$ vs $\mathcal{L}$), and reproducibility omissions (seeds, hyperparameters, sample size N, error bars, compute hardware). | write | não |
| `sanitize_bibtex` | BibTeX sanitizer & preprint-to-published resolver (#25): parses `.bib` files or text, upgrades arXiv/CoRR preprints (`@misc`) to their definitive peer-reviewed conference (`@inproceedings`) or journal (`@article`) venue with DOI, removes duplicates keeping the cleanest key, standardizes authors, escapes unescaped LaTeX (`&`, `%`, `_`), and protects acronyms (`{LLM}`, `{GPU}`). | write | só com `output_path` |
| `parse_and_draft_rebuttal` | Peer-review Rebuttal Copilot (#26): segments raw reviewer comments (`Reviewer 1`, `Reviewer 2`, ...) into numbered actionable points, classifies each objection, flags requests requiring new experiments (`requires_new_experiment=True`), and generates polite, evidence-backed Author Rebuttals in Markdown and LaTeX. | write | não |
| `audit_submission_readiness` | Pre-Submission & Desk-Reject Gatekeeper (#27): runs a 4-stage audit on a `.tex`, `.md`, or `.pdf` manuscript for the target `venue` — (1) Double-Blind Anonymity Audit (non-anonymous GitHub/HF URLs, `\author` blocks, PDF metadata, first-person self-citations), (2) Venue Checklist Engine, (3) Overclaiming Scanner, and (4) Mandatory Sections Check — returning a `readiness_score` (0-100) and `desk_reject_risk`. | write | não |
| `get_fulltext` | Fetch open-access full text (via OpenAlex, EuropePMC, arXiv, Unpaywall) and return excerpts relevant to `question`. | research (todos) | não |
| `query_fulltexts` | Query across multiple papers simultaneously for a specific question (uses full text when Open Access or abstract fallback). | research (todos) | não |
| `citation_graph` | Papers this paper cites (references) and papers citing it (OpenAlex), automatically cached for follow-up. | research (todos) | não |
| `related_papers` | Find papers semantically similar to the target paper, papers it cites (references), or papers citing it. | research (todos) | não |
| `download_papers` | Download Open Access PDFs for one or more paper IDs or DOIs into `output_dir` (inside the workspace; default: configured download_dir, ./papers). | research (todos) | sim |
| `export_papers` | Export papers as bibtex, ris or csv. | research (todos) | não |
| `save_research_dossier` | Save a persistent research dossier (README.md, papers.json, references.bib) in `output_dir` (inside the workspace; default: configured research_dir, ./research) so the user can converse over the research documentation. | research (todos) | sim |
| `list_research_dossiers` | List saved research dossiers available for follow-up conversation. | research (todos) | não |
| `get_research_dossier` | Load a saved research dossier (Markdown report and paper metadata) by slug or topic. | research (todos) | não |
| `configure_noul` | View (no arguments) or update the persistent Noul configuration: contact email (enables Unpaywall and the OpenAlex/Crossref polite pool), workspace folder, and default download/research folders. | research (todos) | sim |
| `evidence_meter` | Compute the Evidence Meter deterministically from YOUR stance classification of each paper: raw counts and percentages plus study-design-weighted percentages (meta-analysis 1.0 … preclinical 0.2). | research (todos) | não |
<!-- noul:tools:end -->

## Instalação

**Sem terminal (pesquisadores):** acesse a página [**Instalar**](https://ernanedomingues.github.io/noul-search-agent/install.html) e escolha o seu ambiente:
- **Claude.ai, Claude Desktop ou ChatGPT:** cole a URL do conector público, só de leitura e sem instalar nada.
- **Claude Desktop, versão completa:** baixe `noul.mcpb` e dê dois cliques. O Claude Desktop pede o e-mail (opcional) e a pasta do Noul.
- **VS Code / Cursor:** botões de instalação em um clique.
- **Executável único** para Windows, macOS e Linux (sem Python). Para instalar em vários aplicativos de uma vez, rode `noul install`, que detecta os aplicativos e pergunta onde instalar.

Os executáveis ainda não são assinados digitalmente. Veja como liberá-los no Windows e no macOS em [`docs/release/signing.md`](docs/release/signing.md).

**Desenvolvedores:** escolha **uma** das opções:

```bash
# Claude Code (plugin: skill + subagente + /noul-search-config + MCP)
/plugin marketplace add ernaneDomingues/noul-search-agent
/plugin install noul-search-agent@noul

# PyPI (após publicação) — instala o pacote e configura todos os agentes
pipx install noul-search-agent && noul-install --all
uvx noul-search-agent install --all          # agentes passam a iniciar o MCP via `uvx`

# npm (após publicação) — usa o uv para provisionar Python e dependências
npx -y noul-search-agent install --all       # agentes passam a iniciar o MCP via `npx`

# A partir do código-fonte (requer uv: https://docs.astral.sh/uv/)
git clone https://github.com/ernaneDomingues/noul-search-agent && cd noul-search-agent
uv run noul-install --all                    # todos os ambientes suportados
uv run noul-install antigravity claude       # ou ambientes específicos
uv run noul-install --all --dry-run          # apenas mostra o que faria
uv run noul-install --all --uninstall        # remove tudo de forma limpa
```

O instalador usa o interpretador atual (`sys.executable`, funciona no Windows) ou `--runner uvx|npx`, grava strings TOML seguras para caminhos do Windows, cria um `.bak` antes de alterar pela primeira vez um arquivo de configuração existente e não toca em arquivos JSON corrompidos (sai com código 1). O plugin do Claude Code e o launcher `npx` requerem o [`uv`](https://docs.astral.sh/uv/) no `PATH`.

## Configuração

Na primeira utilização, informe um e-mail de contato (habilita o Unpaywall e os "polite pools" de OpenAlex/Crossref/NCBI). O agente nunca inventa um e-mail.

```bash
/noul-search-config email:voce@universidade.br            # instalado por noul-install
/noul-search-agent:noul-search-config email:voce@universidade.br  # plugin do Claude Code
noul config email:voce@universidade.br --workspace ~/pesquisa --download-dir papers
noul config                                                 # mostra a configuração
noul config --clear                                         # apaga o arquivo de configuração
```

Downloads e dossiês ficam **sempre dentro do workspace** (padrão: diretório atual; caminhos relativos são relativos a ele). Caminhos fora dele retornam `403`.

| Variável | Uso |
|---|---|
| `NOUL_EMAIL` | E-mail de contato (alternativa ao `config`) |
| `NOUL_WORKSPACE` | Workspace onde downloads/dossiês podem ser gravados (tem prioridade sobre o `workspace` salvo por `noul config`; sem nenhum dos dois, vale a pasta atual ou, se ela for a raiz do disco, `~/Documents/Noul`) |
| `NOUL_HOME` | Cache e `config.json` (padrão `~/.cache/noul-search-agent`) |
| `NOUL_DB_PATH` | Caminho do banco SQLite de cache |
| `NOUL_ALLOW_ANY_PATH=1` | Desativa o confinamento ao workspace; reservado a testes isolados |
| `NOUL_INSTALL_RUNNER` | Runner padrão do instalador (`python`, `uvx`, `npx`) |
| `NOUL_UV` | Executável do `uv` usado pelo launcher `npx` |


## Exemplos de Uso (CLI e Conversa)

Os exemplos usam `noul` (instalado via pip/pipx). Com uvx, troque por `uvx noul-search-agent`; a partir do código-fonte, troque por `uv run noul`.

### 1. Pesquisar na literatura (Medicina, IA/Computação, Economia, Psicologia...)
```bash
noul search "creatine supplementation cognitive performance" --types meta-analysis,rct --year-min 2018 --pretty
noul search "retrieval augmented generation hallucination benchmark" --sources openalex,s2,arxiv --pretty
noul search "statin myopathy" --sources europepmc,pubmed --open-access
```
O campo `tier` é uma faixa transparente de citações/periódico (`top`, `high`, `standard`, `emerging`), **não** um quartil JCR/SJR.

### 2. Buscar artigos similares, referências citadas ou artigos que citam um estudo
```bash
noul related "doi:10.1093/nutrit/nuac064" --relation all --types meta-analysis,rct
```

### 3. Baixar os PDFs Open Access (dentro do workspace)
```bash
noul download "doi:10.1093/nutrit/nuac064,arxiv:2312.10997" --dir papers
```

### 4. Perguntar em cima do texto completo de múltiplos artigos
```bash
noul ask "doi:10.1093/nutrit/nuac064,pmid:29704637" --question "What daily dosage and duration were used?"
```

### 5. Evidence Meter determinístico
O LLM classifica a postura de cada estudo; o cálculo (percentuais brutos e ponderados pelo desenho do estudo) é reprodutível:
```bash
noul meter '[{"id":"doi:10.1093/nutrit/nuac064","stance":"supportive"},{"id":"pmid:29704637","stance":"inconclusive"}]'
```

### 6. Modo HTTP / OpenAPI (ChatGPT Actions, clientes Web, Cloud Run)
```bash
noul-mcp --http 8765          # ou: uv run noul-mcp --http 8765
# MCP: http://127.0.0.1:8765/mcp · OpenAPI: /openapi.json · saúde: /healthz
```
Segurança: escuta em `127.0.0.1` por padrão e valida o cabeçalho `Host` (anti DNS rebinding); CORS apenas para `NOUL_CORS_ORIGINS`; autenticação `Authorization: Bearer` com `NOUL_HTTP_TOKEN` (exceto `/healthz`); ferramentas que gravam em disco (`download_papers`, `save_research_dossier`, `configure_noul`) ficam ocultas e bloqueadas via HTTP, a menos que `NOUL_HTTP_ALLOW_WRITE=1` **e** um token estejam definidos. Outras variáveis: `NOUL_HTTP_HOST`, `PORT`, `NOUL_HTTP_LOG=1`.

### 7. Conector público (Claude.ai, ChatGPT) e Google Cloud Run
O mesmo servidor roda como **conector público só de leitura**: sem login, 12 ferramentas do perfil `research` (sem escrita e sem os dossiês do servidor), transporte *stateless*, limite de chamadas de ferramenta por instância, concorrência limitada e tetos por chamada anunciados no schema. Qualquer pessoa usa colando `https://<serviço>/mcp` em Claude.ai/Claude Desktop (Conectores) ou ChatGPT (modo desenvolvedor).
```bash
NOUL_PUBLIC=1 noul-mcp --http 8765                          # local (127.0.0.1): para testar como conector, exponha via túnel HTTPS
docker build -t noul-search-agent . && docker run -p 8080:8080 -e NOUL_PUBLIC=1 noul-search-agent
```
Deploy no Cloud Run (escala a zero, sem chaves de serviço, via GitHub Actions): [`docs/deploy/cloud-run.md`](docs/deploy/cloud-run.md).

Servidor privado (com escrita, para você): `NOUL_HTTP_TOKEN=troque-me NOUL_HTTP_ALLOW_WRITE=1 noul-mcp --http 8765`.

## Desenvolvimento
Ambiente: `uv venv .venv && uv pip install -e ".[dev]"`. Qualidade: `ruff check .`, `ruff format --check .`, `mypy` e `python -m pytest --cov` (testes offline, com piso de cobertura). Golden sets dos classificadores: `python tests/test_golden_sets.py --report`. Pacotes: `uv build` (wheel/sdist) e `npm pack`. Orientações para agentes de código: [`CLAUDE.md`](CLAUDE.md); plano da v2.0: [`docs/REFACTOR_PLAN.md`](docs/REFACTOR_PLAN.md).

Roadmap de melhorias e de distribuição: [`docs/ROADMAP.md`](docs/ROADMAP.md) · plano de implementação: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Limitações e Aviso Legal
- O **Evidence Meter (Noul Meter)** é um indicador heurístico da distribuição de estudos encontrados, não uma metanálise estatística.
- Resumos de literatura na área da saúde têm finalidade exclusivamente informativa e acadêmica, não constituindo aconselhamento médico.
- Projeto independente inspirado em princípios de Medicina Baseada em Evidências e ferramentas acadêmicas como Consensus e Elicit, sem qualquer afiliação ou endosso da Consensus NLP Inc.
