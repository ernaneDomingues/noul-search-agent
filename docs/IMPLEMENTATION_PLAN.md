# Plano de Implementação — Fase 6: Copiloto de Escrita Acadêmica, Saneador de BibTeX, Rebuttal, Pré-Submissão (Gatekeeper) e Landing Page GitHub Pages

Este documento detalha o plano de execução da **Fase 6** do `noul-search-agent`, cobrindo as **Issues #22, #23, #24, #25, #26, #27 e #28**.
O objetivo desta fase final é transformar o `noul` em um **Copiloto Completo de Autoria e Submissão Científica** — auxiliando desde o esqueleto estrutural (IMRaD) e preenchimento contextual de citações até a síntese de *Related Work*, saneamento de `.bib`, resposta a revisores (*rebuttal*) e auditoria preventiva de *desk-reject* (anonimato double-blind, checklists de conferências/periódicos e *overclaiming*) — além de publicar a **Landing Page e Portal de Documentação no GitHub Pages**.

---

## Status das Fases Anteriores (Baseline Atual)

- **Fase 0 & Hardening P0 (`22/22` testes em `tests/test_agent.py`)**: Concluída.
- **Fase 1 — Issues #19, #02, #03, #01 (`73/73` testes acumulados)**: Concluída.
- **Fase 2 — Issues #04, #05, #06, #07 (`90/90` testes acumulados)**: Concluída.
- **Fase 3 — Issues #08, #09, #10, #11, #12, #13 (`121/121` testes acumulados)**: Concluída.
- **Fase 4 — Issues #14, #15 (`128/128` testes acumulados)**: Concluída.
- **Fase 5 — Issues #16, #17, #18, #20, #21 (`138/138` testes acumulados)**: Concluída (`review_paper`, `scaffold_implementation`, `generate_research_hypotheses`).

> [!IMPORTANT]
> **Regras Inegociáveis de Compatibilidade e Segurança (Preservar na Fase 6)**
> 1. **Zero regressão nos 138 testes existentes**: todas as suítes `tests/test_*.py` devem continuar passando 100% tanto no modo padrão quanto com `NOUL_FORCE_PYDANTIC_FALLBACK=1`.
> 2. **Dual-Mode em `noul/schemas/`**: novos contratos em `noul/schemas/authoring.py` devem usar `_Model` e `_field` de `noul/schemas/_compat.py`, funcionando com ou sem `pydantic` instalado.
> 3. **Confinamento estrito ao Workspace e Proteção Anti-Symlink/SSRF**:
>    - Qualquer leitura ou escrita de arquivos de rascunho (`.md`, `.tex`, `.pdf`, `.bib`, `reviews.txt`) por tools deve validar que o caminho está dentro do workspace configurado (`noul_core._safe_output_dir` / helper `_safe_workspace_file`) e rejeitar symlinks ou *path traversal* (`..`).
>    - Todas as chamadas HTTP (para atualizar preprints do arXiv via Crossref/OpenAlex/Semantic Scholar ou buscar referências) devem usar `noul_core._http_get`.
> 4. **Determinismo e Execução Offline em Testes**: todas as ferramentas devem funcionar de forma determinística quando os papers ou respostas HTTP estiverem em cache/mockados, sem depender de LLM externo ou rede ativa nos testes unitários.

---

## Escopo da Fase 6

| Etapa | Issues | Entrega Principal | Arquivos Criados / Modificados |
| :--- | :--- | :--- | :--- |
| **Etapa 1** | Base Fase 6 | Contratos de dados de Autoria, BibTeX, Rebuttal e Gatekeeper | `noul/schemas/authoring.py`, `noul/schemas/__init__.py` |
| **Etapa 2** | **#22 & #24** | Recomendador Contextual de Citações (`recommend_citations` / `noul cite`) & Sintetizador Narrativo de Related Work (`synthesize_related_work` / `noul related-work`) | `noul/tools/authoring_tools.py`, `noul_tools.py`, `noul_cli.py`, `tests/test_citation_and_related_work.py` |
| **Etapa 3** | **#23** | Copiloto de Estruturação IMRaD e Auditoria de Manuscritos (`generate_manuscript_blueprint`, `audit_manuscript_draft` / `noul draft structure\|audit`) | `noul/tools/authoring_tools.py`, `noul_tools.py`, `noul_cli.py`, `tests/test_draft_auditor.py` |
| **Etapa 4** | **#25** | Saneador de BibTeX e Resolvedor de Publicações Definitivas (`sanitize_bibtex` / `noul bibtex sanitize`) | `noul/providers/arxiv_client.py`, `noul/tools/authoring_tools.py`, `noul_tools.py`, `noul_cli.py`, `tests/test_bibtex_sanitizer.py` |
| **Etapa 5** | **#26 & #27** | Copiloto de Rebuttal (`parse_and_draft_rebuttal` / `noul rebuttal parse`) & Subagente de Pré-Submissão / Desk-Reject (`audit_submission_readiness` / `noul gatekeeper audit`) | `noul/tools/authoring_tools.py`, `noul/tools/gatekeeper_tools.py`, `noul_tools.py`, `noul_cli.py`, `tests/test_rebuttal_and_gatekeeper.py` |
| **Etapa 6** | **#28** | Landing Page Responsiva, Simulador de Terminal, Portal de Docs MCP e Workflow GitHub Pages | `site/index.html`, `site/styles.css`, `site/app.js`, `site/favicon.svg`, `site/og-card.svg`, `scripts/build_site.py`, `.github/workflows/deploy-pages.yml`, `tests/test_landing_page.py` |

---

## Detalhamento Técnico por Etapa

### Etapa 1 — Contratos de Dados de Autoria, Saneamento BibTeX, Rebuttal e Gatekeeper

#### 1.1. Criar `noul/schemas/authoring.py`
Definir os modelos utilizando `_Model` e `_field` de `noul/schemas/_compat.py`:

```python
"""Schemas for Phase 6 (#22-#27): Academic Authoring, BibTeX Sanitizer, Rebuttal & Submission Gatekeeper."""
from typing import List, Optional
from noul.schemas._compat import _Model, _field


class CitationRecommendationItem(_Model):
    claim_sentence: str
    marker_or_trigger: str = ""
    search_query: str = ""
    recommended_papers: List[dict] = _field(default_factory=list)
    justifications: List[str] = _field(default_factory=list)
    citation_keys: List[str] = _field(default_factory=list)
    inline_replacement: str = ""


class CitationInfillerReport(_Model):
    input_format: str = "markdown"  # plain | markdown | latex
    annotated_text: str = ""
    claims_detected: int = 0
    recommendations: List[CitationRecommendationItem] = _field(default_factory=list)
    bibtex_block: str = ""
    warnings: List[str] = _field(default_factory=list)


class BlueprintSection(_Model):
    section_title: str
    imrad_role: str  # abstract | introduction | related_work | methodology | experiments | discussion | conclusion | venue_mandatory
    paragraph_prompts: List[str] = _field(default_factory=list)
    recommended_elements: List[str] = _field(default_factory=list)


class ManuscriptBlueprint(_Model):
    topic: str
    venue: str = "general"
    hypothesis: str = ""
    key_findings: List[str] = _field(default_factory=list)
    output_format: str = "markdown"  # markdown | latex
    sections: List[BlueprintSection] = _field(default_factory=list)
    rendered_template: str = ""


class DraftAuditFinding(_Model):
    category: str  # weak_claim | notation_inconsistency | reproducibility_omission
    severity: str  # high | medium | low
    excerpt: str
    issue_description: str
    suggested_rewrite: str


class DraftAuditReport(_Model):
    source_label: str = "inline_draft"
    venue: str = "general"
    output_format: str = "markdown"
    weak_or_unsubstantiated_claims: List[DraftAuditFinding] = _field(default_factory=list)
    notation_or_terminology_inconsistencies: List[DraftAuditFinding] = _field(default_factory=list)
    reproducibility_omissions: List[DraftAuditFinding] = _field(default_factory=list)
    pre_submission_checklist: List[str] = _field(default_factory=list)
    rendered_report: str = ""


class RelatedWorkThemeCluster(_Model):
    theme_name: str
    approach_family: str
    paper_ids: List[str] = _field(default_factory=list)
    citation_keys: List[str] = _field(default_factory=list)
    comparative_narrative: str = ""


class RelatedWorkSynthesis(_Model):
    proposal_summary: str
    citation_style: str = "latex"  # latex | numeric
    output_format: str = "markdown"  # markdown | latex
    thematic_clusters: List[RelatedWorkThemeCluster] = _field(default_factory=list)
    positioning_paragraph: str = ""
    rendered_related_work: str = ""
    bibtex_block: str = ""


class BibtexEntryUpdate(_Model):
    original_key: str
    canonical_key: str
    title: str
    action: str  # kept | upgraded_preprint | deduplicated | doi_completed | latex_sanitized
    old_venue_or_type: str = ""
    new_venue_or_type: str = ""
    doi: Optional[str] = None
    notes: List[str] = _field(default_factory=list)


class BibtexSanitizeReport(_Model):
    entries_total: int = 0
    entries_out: int = 0
    duplicates_removed: int = 0
    preprints_upgraded: int = 0
    dois_completed: int = 0
    latex_escapes_fixed: int = 0
    updates: List[BibtexEntryUpdate] = _field(default_factory=list)
    sanitized_bibtex: str = ""
    warnings: List[str] = _field(default_factory=list)


class RebuttalPoint(_Model):
    point_id: str
    reviewer_id: str
    category: str  # clarity_question | new_experiment_request | baseline_criticism | notation_or_formatting | methodological_or_statistical
    severity: str = "medium"  # high | medium | low
    original_comment: str = ""
    requires_new_experiment: bool = False
    draft_response: str = ""
    manuscript_action_plan: str = ""


class RebuttalReport(_Model):
    reviewers_detected: List[str] = _field(default_factory=list)
    total_points: int = 0
    new_experiments_required: int = 0
    points: List[RebuttalPoint] = _field(default_factory=list)
    markdown_rebuttal: str = ""
    latex_rebuttal: str = ""


class GatekeeperCheckItem(_Model):
    check_id: str
    category: str  # anonymity | checklist | overclaiming | mandatory_section
    passed: bool
    severity: str  # critical | high | medium | low
    description: str
    evidence_or_location: str = ""
    remediation: str = ""


class SubmissionGatekeeperReport(_Model):
    venue: str
    readiness_score: int  # 0 to 100
    desk_reject_risk: str  # low | medium | critical
    anonymity_violations: List[GatekeeperCheckItem] = _field(default_factory=list)
    venue_checklist: List[GatekeeperCheckItem] = _field(default_factory=list)
    overclaiming_flags: List[GatekeeperCheckItem] = _field(default_factory=list)
    mandatory_sections: List[GatekeeperCheckItem] = _field(default_factory=list)
    mandatory_corrective_actions: List[str] = _field(default_factory=list)
    summary_markdown: str = ""
```

#### 1.2. Reexportar em `noul/schemas/__init__.py`
Importar e adicionar todos os novos modelos ao `__all__` de `noul/schemas/__init__.py`.

---

### Etapa 2 — Issue #22 (Citation Infiller) e Issue #24 (Related Work Synthesizer)

#### 2.1. Criar `noul/tools/authoring_tools.py`
Implementar utilitários seguros de leitura de texto/arquivo dentro do workspace (`_load_text_or_workspace_file(raw_input)`) que:
- Se `raw_input` corresponder a um caminho de arquivo existente ou parecer um caminho de arquivo (`.md`, `.tex`, `.txt`, `.bib`), valida confinamento estrito ao workspace (`noul_core.workspace_dir()`) e ausência de symlinks (`noul_core._reject_symlink_path`), lançando `HTTPError(400, ...)` se tentar escapar do workspace (`../`, `/etc/passwd`, symlink externo).
- Caso contrário, interpreta `raw_input` como o próprio texto do rascunho.

##### A) `recommend_citations(draft_text, max_per_claim=2, citation_style="latex")` (Issue #22)
1. **Detecção de formato (`plain` | `markdown` | `latex`)**:
   - `latex` se contiver `\cite`, `\begin{`, `\section{` etc.; `markdown` se contiver `#`, `**`, `[CITATION NEEDED]`; senão `plain`.
2. **Detecção de alegações que precisam de citação**:
   - **Marcadores explícitos**: `[CITATION NEEDED]`, `[citation needed]`, `[CITE]`, `\cite{?}`, `\cite{}`, `\citep{?}`, `\citet{?}`, `[?]`, `[TODO: cite]`.
   - **Sentenças declarativas empíricas/teóricas sem suporte bibliográfico**: sentenças que **não** possuem citação existente (`\cite{...}`, `[1]`, `(Author et al., 202X)`) mas contêm gatilhos de afirmação científica/empírica (`studies show`, `has been shown`, `prior work`, `recent advances`, `outperforms`, `significantly reduces`, `achieves state-of-the-art`, `demonstrates that`, `is known to`, `scaling laws`, `clinical trials indicate`, percentagens ou métricas comparativas).
3. **Recuperação semântica direcionada e geração BibTeX**:
   - Para cada sentença detectada, extrai os termos-chave técnicos (removendo *stopwords* e marcadores como `[CITATION NEEDED]`) para formar `search_query`.
   - Consulta `noul_tools.search_papers(search_query, limit=max_per_claim, sort="citations")` (com fallback determinístico para papers já presentes no cache SQLite caso a busca externa retorne vazio/offline).
   - Para cada paper recuperado (2 a 3 por alegação):
     - Gera a chave BibTeX padronizada (`noul_core.format_bibtex(p)` / chave `autorAnoPalavra`).
     - Constrói a justificativa explícita:
       `"Este artigo de {PrimeiroAutor} et al. ({Ano}) comprova essa afirmação ({Venue}, {Citações} citações): {Evidência resumida do abstract/título}."`
   - Substitui o marcador (`[CITATION NEEDED]` / `\cite{?}`) ou anexa ao final da sentença o comando `\cite{key1,key2}` (quando `citation_style="latex"` ou input LaTeX) ou `[@key1; @key2]` / `[key1, key2]` (quando `citation_style="markdown"`), produzindo `annotated_text`.
   - Concatena todas as entradas `@article` / `@inproceedings` sem duplicatas em `bibtex_block`.

##### B) `synthesize_related_work(paper_ids, proposal, citation_style="latex", output_format="markdown")` (Issue #24)
1. **Validação e Resolução**:
   - Valida `paper_ids` (lista de 1 a 20 IDs/DOIs ou string separada por vírgula; se `paper_ids` vazio, lança `HTTPError(400, ...)`).
   - Valida `proposal` (string não vazia descrevendo a proposta central do novo trabalho; se vazia, lança `HTTPError(400, ...)`).
   - Resolve cada paper via `noul_tools._resolve(pid)`.
2. **Agrupamento Semântico por Famílias de Abordagem**:
   - Classifica cada paper em um dos eixos temáticos com base no título, abstract, `study_type` e `domain`:
     - `Architectural & Representation Foundations` (arquitetura, backbone, representação, atenção, grafo, mecanismo teórico);
     - `Training, Optimization & Causal Identification` (treinamento, otimização, fine-tuning, RLHF, inferência causal, painel econométrico, protocolo clínico);
     - `Efficiency, Scaling & Empirical Evaluation` (inferência, quantização, hardware, benchmark, avaliação empírica, meta-análise).
3. **Geração de Prosa Acadêmica Comparativa (Proibido listar paper por paper isoladamente)**:
   - Para cada cluster com $\ge 1$ paper, redige um parágrafo coeso que **contrasta e relaciona** os trabalhos do grupo usando conectivos comparativos (*"While X et al. \cite{k1} establish..., Y et al. \cite{k2} extend this paradigm by..., whereas Z et al. \cite{k3} highlight the trade-off in..."*), citando todos os papers do cluster no estilo solicitado (`\cite{key}` / `\citep{key}` para `latex` ou `[1]`, `[2]` para `numeric`).
4. **Parágrafo de Posicionamento e Lacuna (`positioning_paragraph`)**:
   - Redige um parágrafo final que sintetiza a limitação coletiva da literatura revista e posiciona explicitamente a inovação de `proposal` (*"Despite these advances across ..., existing approaches remain constrained by .... In contrast, our work ({proposal}) bridges this gap by ..."*).
5. **Formatação de Saída (`rendered_related_work`)**:
   - Se `output_format="latex"`, retorna um bloco `\section{Related Work}` com `\paragraph{...}` pronto para colar em `.tex`.
   - Se `output_format="markdown"`, retorna `## Related Work` com subtítulos `### ...` e bloco BibTeX.

#### 2.2. Registro em `noul_tools.py` e `noul_cli.py`
- Registrar `recommend_citations` e `synthesize_related_work` em `noul_tools.TOOLS`.
- Adicionar subcomandos em `noul_cli.py`:
  - `noul cite <text_or_file> [--limit-per-claim 2] [--style latex|markdown] [--pretty]`
  - `noul related-work <ids...> --proposal "..." [--style latex|numeric] [--format markdown|latex] [--pretty]`

---

### Etapa 3 — Issue #23: Copiloto de Estruturação e Auditoria de Manuscritos (`Draft Manuscript Auditor`)

#### 3.1. Implementar em `noul/tools/authoring_tools.py`

##### A) Modo 1 — `generate_manuscript_blueprint(topic, venue="neurips", hypothesis="", key_findings=None, output_format="markdown")`
- Valida `topic` (não vazio; caso contrário `HTTPError(400, ...)`).
- Normaliza `venue` (`neurips`, `icml`, `iclr`, `acl`, `ieee`, `nature`, `lancet`, `aer`, `general`) e `output_format` (`markdown`, `latex`).
- Gera a árvore completa no padrão **IMRaD** adaptada ao `venue`:
  1. `Abstract` (Contexto, Lacuna, Hipótese/Proposta, Principais Achados quantificados);
  2. `1. Introduction` (Motivação de alto impacto, Limitações do estado da arte, Hipótese central: `hypothesis`, Resumo das contribuições em *bullet points*);
  3. `2. Related Work` (Eixos temáticos comparativos e posicionamento);
  4. `3. Methodology / Problem Formulation` (Formulação matemática/protocolo, pressupostos de identificação ou desenho experimental);
  5. `4. Experimental Setup & Empirical Results` (Datasets/coorte, baselines, métricas, tabelas principais sustentando `key_findings`, estudos de ablação/robustez);
  6. `5. Discussion & Limitations` (Modos de falha, ameaças à validade, custo computacional ou limites clínicos/políticos);
  7. `6. Conclusion & Broader Impact` (Síntese e implicações éticas/reprodutibilidade, incluindo seções mandatórias do `venue` como `NeurIPS Paper Checklist` ou `CONSORT/STROBE Declarations`).
- Renderiza `rendered_template` em Markdown (`# ...`, `## ...`) ou LaTeX (`\section{...}`, `\subsection{...}`, `% Paragraph guidance: ...`).

##### B) Modo 2 — `audit_manuscript_draft(draft_input, venue="neurips", output_format="markdown")`
- Carrega `draft_input` (caminho de arquivo `.md`/`.tex` confinado ao workspace ou texto inline; se vazio, `HTTPError(400, ...)`).
- Executa 3 analisadores determinísticos:
  1. **`weak_or_unsubstantiated_claims`**:
     - Detecta sentenças com alegações absolutas/vagas sem evidência ou citação próxima (`clearly proves`, `obviously`, `undoubtedly`, `massive improvement`, `state-of-the-art` sem referência a `Table`/`Figure`/`\cite`/`\ref`, ou números soltos sem baseline).
     - Sugere reformulação acadêmica precisa (*hedging* calibrado + ancoragem em tabela/citação).
  2. **`notation_or_terminology_inconsistencies`**:
     - Detecta inconsistências de notação matemática (ex.: uso misto de `$L_{total}$` e `$\mathcal{L}_{total}$`, ou variáveis em equações não definidas no texto adjacente) e variações terminológicas/acrônimos redefinidos ou usados antes da definição.
     - Sugere padronização explícita.
  3. **`reproducibility_omissions`**:
     - Verifica menção a: (a) sementes aleatórias (`seed`), (b) hiperparâmetros / taxa de aprendizado / otimizador (ou protocolo de dosagem/intervenção), (c) tamanho de amostra / splits de dados (`train/val/test`, $N=$), (d) intervalos de confiança / desvio padrão / barras de erro (`\pm`, `CI`, `p <`), e (e) infraestrutura de hardware/tempo de execução.
     - Para cada item ausente, gera um `DraftAuditFinding` com um trecho modelo pronto para inserir na seção experimental.
- Compila `pre_submission_checklist` e `rendered_report` (em Markdown ou LaTeX).

#### 3.2. Registro em `noul_tools.py` e `noul_cli.py`
- Registrar `generate_manuscript_blueprint` e `audit_manuscript_draft` em `noul_tools.TOOLS`.
- Adicionar subcomando `noul draft` com subparsers em `noul_cli.py`:
  - `noul draft structure --topic "..." [--venue neurips] [--hypothesis "..."] [--findings "f1;f2"] [--format markdown|latex] [--pretty]`
  - `noul draft audit <draft_file_or_text> [--venue neurips] [--format markdown|latex] [--pretty]`

---

### Etapa 4 — Issue #25: Saneador de BibTeX e Resolvedor de Publicações Definitivas (`BibTeX Sanitizer`)

#### 4.1. Estender `noul/providers/arxiv_client.py` e `noul/tools/authoring_tools.py`
1. **Parser de entradas `.bib` (`parse_bibtex_entries(raw_bib)`)**:
   - Analisa entradas `@article{key, ...}`, `@misc{key, ...}`, `@inproceedings{key, ...}`, `@book{...}`, extraindo `entry_type`, `key` e dicionário de campos (`title`, `author`, `year`, `journal`, `booktitle`, `eprint`, `archiveprefix`, `doi`, `url`, `pages`, `volume`, `number`, `publisher`).
2. **Resolvedor de Publicação Definitiva (`resolve_published_venue_for_preprint(entry)`)**:
   - Identifica se uma entrada é um preprint do arXiv (`entry_type == "misc"` ou `journal` contendo `"arxiv"` ou `eprint` presente ou `doi` iniciando com `10.48550/arxiv`).
   - Quando `update_preprints=True`:
     - Primeiro verifica no cache local do `noul_core` ou via `noul_core.s2_paper` / `noul_core.search_crossref` / `noul_core.search_openalex` se existe versão publicada em conferência ou periódico (ex.: `NeurIPS`, `ICML`, `ICLR`, `CVPR`, `ACL`, `EMNLP`, `Nature`, `Science`, `IEEE`, ou qualquer venue formal que não seja `arXiv` / `CoRR`).
     - Se encontrar publicação formal com título correspondente, converte `@misc` / `@article` (arXiv) para `@inproceedings` (quando conferência: `NeurIPS`, `ICML`, `ICLR`, `CVPR`, `ACL`, `EMNLP`, `AAAI`, `KDD`, `Proceedings`, `Conference`) ou `@article` (quando periódico), preenchendo `booktitle` ou `journal`, `year`, `doi` definitivo e mantendo nota de rastreabilidade (`eprint`).
3. **Desduplicação, Padronização de Autores e Saneamento LaTeX**:
   - **Desduplicação**: agrupa entradas com mesmo DOI normalizado, mesmo arXiv ID base (sem `vN`) ou mesmo título normalizado (letras minúsculas alfanuméricas). Mantém a entrada mais completa (preferindo versão publicada sobre preprint) e escolhe a chave mais coerente (`autorAnoPalavra`, preservando chaves legíveis).
   - **Padronização de Autores**: normaliza separadores de autores para `" and "` e corrige espaços extras.
   - **Saneamento de Caracteres Especiais LaTeX**:
     - Escapa `&`, `%` e `_` desencapados fora de modo matemático (`$...$`) em `title`, `journal` e `booktitle` (incrementando `latex_escapes_fixed`).
     - Protege siglas maiúsculas conhecidas em títulos (`{LLM}`, `{LLMs}`, `{GPU}`, `{RLHF}`, `{Transformer}`, `{BERT}`, `{GPT}`, `{CONSORT}`, `{STROBE}`, `{DNA}`, `{RNA}`, `{COVID-19}`) quando ainda não estiverem entre chaves.
4. **Assinatura da Tool e CLI**:
   - `sanitize_bibtex(bibtex_input, update_preprints=True, output_path=None)` em `noul/tools/authoring_tools.py` e `noul_tools.py`.
   - Se `output_path` for fornecido, valida confinamento no workspace (`_safe_output_dir` / `_reject_symlink_path`) antes de gravar.
   - Subcomando CLI em `noul_cli.py`:
     - `noul bibtex sanitize <bib_file_or_text> [--update-preprints] [--no-update-preprints] [--output <out.bib>] [--pretty]`

---

### Etapa 5 — Issue #26 (Rebuttal Copilot) e Issue #27 (Submission Gatekeeper)

#### 5.1. Issue #26 — `parse_and_draft_rebuttal` em `noul/tools/authoring_tools.py`
1. **Segmentação por Revisor e por Objeção**:
   - Aceita `reviews_input` (caminho de arquivo `reviews.txt` / `.md` dentro do workspace ou texto bruto), mais `paper_id` opcional e `draft_input` opcional.
   - Detecta cabeçalhos de revisores (`Reviewer 1`, `Reviewer #2`, `R1`, `Reviewer A`, `Meta-Reviewer`, `Area Chair`; caso não haja cabeçalho explícito, assume `Reviewer 1`).
   - Divide os comentários de cada revisor em pontos acionáveis numerados (`R1.1`, `R1.2`, `R2.1`, ...) por parágrafos, listas numeradas/bullets ou blocos de questionamento (`?`, `Weaknesses:`, `Questions:`, `Major:`, `Minor:`).
2. **Classificação das Objeções**:
   - `new_experiment_request`: pede novos experimentos, ablações adicionais, mais sementes, novos datasets ou testes estatísticos (`run additional`, `new experiment`, `ablation`, `evaluate on`, `more seeds`, `p-value`, `statistical significance`) -> define `requires_new_experiment=True`.
   - `baseline_criticism`: questiona falta de comparação com baseline ou método recente (`compare against`, `missing baseline`, `prior work by`, `SOTA baseline`).
   - `clarity_question`: pede esclarecimento conceitual, intuição ou explicação (`unclear`, `clarify`, `how does`, `what is meant by`, `intuition`, `confusing`).
   - `notation_or_formatting`: aponta erro tipográfico, equação mal definida, figura ilegível ou referência quebrada (`typo`, `notation`, `equation`, `figure`, `table`, `caption`, `define symbol`).
   - `methodological_or_statistical`: questiona premissa teórica, viés, *data leakage*, identificação causal ou desenho do estudo.
3. **Geração do Modelo de Rebuttal em Markdown e LaTeX**:
   - Para cada `RebuttalPoint`, gera:
     - `draft_response`: resposta cordial, assertiva e técnica (*"We thank {reviewer_id} for this constructive observation. ... "*), ancorada em evidências do `paper_id` / `draft_input` quando disponível.
     - `manuscript_action_plan`: descrição exata da revisão a realizar no texto (*"Action in Manuscript: Update Section X / Table Y to include..."*).
   - Preenche `markdown_rebuttal` (com blocos `> **[R1.1 — Category]**` + `**Author Response:**` + `**Manuscript Revision:**`) e `latex_rebuttal` (com caixas delimitadas `\begin{quote}\textbf{Reviewer Comment [R1.1]:} ... \end{quote}` + `\noindent\textbf{Response:}` + `\noindent\textcolor{blue}{\textbf{Changes in Manuscript:}}`).
4. **CLI e MCP**:
   - Tool `parse_and_draft_rebuttal(reviews_input, paper_id=None, draft_input=None, output_format="markdown")` em `noul_tools.TOOLS`.
   - Subcomando CLI: `noul rebuttal parse <reviews_file_or_text> [--paper-id <id>] [--draft <draft_path>] [--format markdown|latex] [--pretty]`.

#### 5.2. Issue #27 — `audit_submission_readiness` em `noul/tools/gatekeeper_tools.py`
1. **Entrada e Suporte a `.tex`, `.md` e `.pdf`**:
   - Aceita `draft_path` (caminho para `.tex`, `.md` ou `.pdf` dentro do workspace, ou string de conteúdo do manuscrito) e `venue` (`neurips`, `icml`, `iclr`, `acl`, `ieee`, `nature`, `lancet`, `elsevier`).
   - Se `draft_path` for um `.pdf` local dentro do workspace, extrai o texto usando `noul/providers/pdf_parser.py` (ou leitura de texto/streams PDF stdlib).
2. **Pipeline de 4 Estágios do Gatekeeper**:
   - **Estágio 1 — *Anonymity Audit (Double-Blind)*** (aplicável a `neurips`, `icml`, `iclr`, `acl` e conferências double-blind):
     - Detecta links de GitHub/GitLab/HuggingFace que revelam identidade (`https://github.com/<user>/<repo>` onde `<user>` **não** é `anonymous`, `anon`, ou `anonymous.4open.science`).
     - Detecta blocos `\author{...}` em LaTeX ou cabeçalhos de autor que contenham nomes reais/afiliações/emails (`@`) em vez de `Anonymous Author(s)`.
     - Detecta metadados reveladores (`\hypersetup{...pdfauthor=...}`, `\thanks{Supported by grant...}`).
     - Detecta auto-citações em primeira pessoa que quebram anonimato (`In our previous work`, `We previously proposed in \cite{...}`, `Following our earlier paper`).
   - **Estágio 2 — *Checklist Engine*** (adaptado ao `venue`):
     - Para conferências de IA/CS (`neurips`, `icml`, `iclr`, `acl`, `ieee`): verifica (1) Declaração de Limitações, (2) Especificação de Recursos Computacionais (GPU/TPU/tempo), (3) Hiperparâmetros e Sementes (`seed`), (4) Barras de Erro / Desvio Padrão nos resultados, (5) Licenças de código/datasets.
     - Para periódicos biomédicos/gerais (`nature`, `lancet`, `elsevier`): verifica (1) Data Availability Statement, (2) Competing Interests / Conflict of Interest, (3) Ethics Approval / IRB / Consentimento, (4) Funding Statement, (5) Registro de protocolo / diretriz CONSORT-STROBE.
   - **Estágio 3 — *Overclaiming Scanner***:
     - Detecta superlativos e alegações absolutas sem ressalvas (`proves beyond any doubt`, `solves the problem completely`, `universally outperforms all`, `100% accurate in all scenarios`, `first ever in human history`, `eliminates all errors`, `zero limitations`).
   - **Estágio 4 — *Mandatory Sections Check***:
     - Verifica a presença explícita das seções exigidas pelo `venue` (`Limitations`, `Ethics Statement` / `Broader Impact`, `Reproducibility`, `References`, e `Appendix` / `Checklist`).
3. **Cálculo do `readiness_score` (0–100) e `desk_reject_risk`**:
   - Inicia em `100` e deduz pontos de forma determinística:
     - Cada violação de anonimato em venue double-blind: `-30` pontos e força `desk_reject_risk = "critical"`.
     - Cada seção mandatória ausente: `-12` pontos.
     - Cada item do checklist reprovado: `-8` pontos.
     - Cada flag de *overclaiming*: `-6` pontos.
   - Classifica `desk_reject_risk`:
     - `"critical"` se houver qualquer violação de anonimato double-blind ou `readiness_score < 50`;
     - `"medium"` se `50 <= readiness_score < 80` ou houver seção mandatória ausente;
     - `"low"` se `readiness_score >= 80` e zero violações de anonimato.
   - Gera `mandatory_corrective_actions` ordenadas por prioridade.
4. **CLI e MCP**:
   - Registrar `audit_submission_readiness(draft_path, venue="neurips")` em `noul_tools.TOOLS` (automaticamente exposta no servidor MCP `noul_mcp.py`).
   - Adicionar subcomando CLI em `noul_cli.py`:
     - `noul gatekeeper audit <draft_path> [--venue neurips] [--pretty]`

---

### Etapa 6 — Issue #28: Landing Page e Documentação Pública via GitHub Pages

#### 6.1. Estrutura do Diretório `site/` e Gerador Determinístico `scripts/build_site.py`
Criar o portal estático em `site/` (moderno, responsivo, com suporte a tema Claro/Escuro, zero dependências externas obrigatórias no build e sincronizado automaticamente com `noul_tools.TOOLS` via `scripts/build_site.py`):

1. **`site/index.html`**:
   - **SEO & Open Graph Meta Tags**: `<title>`, `<meta name="description">`, `og:title`, `og:description`, `og:type`, `og:image` (`og-card.svg`), `twitter:card`, e link para `favicon.svg`.
   - **Hero Section (Primeira Dobra)**:
     - Título de impacto: **Noul Search Agent**
     - Subtítulo técnico: *"Deterministic Multi-Model Search Agent & Autonomous Scientific Research Assistant"*
     - Badges de status: `v2.0.0`, `MIT License`, `MCP Ready (stdio + HTTP/OpenAPI)`, `Python 3.10+`, `CI Passing`.
     - Duplo CTA: `Instalação Rápida` (`#quickstart`) e `Ver no GitHub` (`https://github.com/ernaneDomingues/noul-search-agent`) + link para `Releases`.
     - **Simulador de Terminal Interativo/Animado**: abas clicáveis e animação de digitação demonstrando a saída real dos comandos `noul compare`, `noul review`, `noul scaffold` e `noul gatekeeper audit`.
   - **Matriz de Funcionalidades por Perfil (Showcase — 4 Cards)**:
     - *Para Engenheiros & Construtores*: Paper-to-Code em PyTorch, validação de tensores (`ast.parse`), cálculo de VRAM (FP16/INT8/INT4) e estimativa de KV Cache.
     - *Para Médicos & Pesquisadores de Saúde*: Auditoria de ensaios clínicos com diretrizes CONSORT/STROBE/PRISMA e extração de protocolos operacionais.
     - *Para Economistas & Analistas*: Verificação de identificação causal (DiD, IV, RDD), testes de endogeneidade e scripts em Python (`statsmodels`) e R (`fixest`).
     - *Para Autores Acadêmicos*: Infilling de citações com BibTeX (`noul cite`), síntese de Related Work (`noul related-work`), saneamento de `.bib` (`noul bibtex sanitize`), Rebuttal Copilot (`noul rebuttal parse`) e Pre-Submission Gatekeeper (`noul gatekeeper audit`).
   - **Guia de Inicialização e Configuração (1-Click Setup)**:
     - Snippets com botão `Copiar` (1 clique) para `uvx noul-search-agent` e `npx noul-search-agent`.
     - Abas de configuração JSON MCP prontas para copiar:
       - **Claude Desktop / Claude Code** (`claude_desktop_config.json` / `.mcp.json`)
       - **Cursor** (`.cursor/mcp.json`)
       - **Google Gemini / Antigravity CLI** (`settings.json` / `mcp_config.json`)
   - **Arquitetura e Relação com o Ecossistema**:
     - Diagrama vetorial/estruturado ilustrando o desacoplamento entre o agente local (`noul_cli.py` / `noul_mcp.py` / `noul_tools.py`), as 6 APIs científicas (OpenAlex, Semantic Scholar, PubMed, EuropePMC, arXiv, Crossref + Unpaywall) e o ecossistema *The AI Research Journal*.
   - **Portal de Documentação Técnica & Roadmap**:
     - Catálogo interativo com busca/filtro de todas as ferramentas MCP expostas em `noul_tools.TOOLS` (nome, descrição, parâmetros obrigatórios/opcionais e subcomando CLI equivalente).
     - Changelog e Roadmap das Fases 1 a 6 (`v1.0` a `v2.0.0`).
2. **`site/styles.css` & `site/app.js` & `site/favicon.svg` & `site/og-card.svg`**:
   - Design responsivo (mobile e desktop) com variáveis CSS para **Modo Escuro / Modo Claro** (botão de alternância persistente), tipografia técnica limpa, botões de cópia com feedback visual e simulador de terminal interativo.
3. **`scripts/build_site.py`**:
   - Script Python stdlib que lê `noul_tools.TOOLS` e `noul_core.__version__`, gera `site/tools-reference.json` e valida/atualiza o catálogo de tools em `site/index.html`. Suporta `--check` para uso no CI.
4. **Workflow `.github/workflows/deploy-pages.yml`**:
   - Acionado em `push` para `master`/`main`, `release: [published]` e `workflow_dispatch`.
   - Executa `python3 scripts/build_site.py --check`, roda a suíte de testes (`python3 -m unittest discover -s tests -p "test_*.py"`), empacota o artefato `site/` com `actions/upload-pages-artifact@v3` e publica no GitHub Pages com `actions/deploy-pages@v4`.

---

## Plano de Verificação e Testes Automatizados

Ao final de cada etapa, executar a suíte completa de testes unitários:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
NOUL_FORCE_PYDANTIC_FALLBACK=1 python3 -m unittest discover -s tests -p "test_*.py" -v
```

### Suítes de Teste da Fase 6:
1. **`tests/test_citation_and_related_work.py` (Issues #22 & #24)**:
   - Testa `recommend_citations` com texto contendo `[CITATION NEEDED]`, `\cite{?}` e sentenças declarativas sem citação; verifica justificativas (`"Este artigo de ... comprova essa afirmação..."`), geração do bloco BibTeX `@article` e substituição em Markdown/LaTeX.
   - Testa `synthesize_related_work` com múltiplos papers em cache e uma `proposal`, verificando agrupamento em famílias de abordagem, prosa comparativa (não isolada por autor), `positioning_paragraph` e exportação em Markdown e LaTeX (`\cite{}`).
   - Testa os subcomandos CLI `noul cite` e `noul related-work`.
2. **`tests/test_draft_auditor.py` (Issue #23)**:
   - Testa `generate_manuscript_blueprint` (`noul draft structure --topic "..." --venue "neurips"`) em Markdown e LaTeX, verificando a árvore IMRaD completa.
   - Testa `audit_manuscript_draft` (`noul draft audit`) em arquivo `.md` e `.tex` dentro do workspace, verificando detecção de alegações fracas, inconsistências notacionais e omissões de reprodutibilidade (sementes, hiperparâmetros, tamanho de amostra), além do bloqueio de caminhos fora do workspace.
3. **`tests/test_bibtex_sanitizer.py` (Issue #25)**:
   - Testa `sanitize_bibtex` (`noul bibtex sanitize references.bib --update-preprints`) atualizando preprints antigos do arXiv (`@misc` / `arXiv`) para atas de conferência (`@inproceedings`, ex.: NeurIPS/ICML) ou periódico com DOI completo.
   - Verifica remoção de duplicados mantendo a chave mais coerente e correção de caracteres especiais LaTeX (`&`, `%`, `_`) e proteção de siglas.
4. **`tests/test_rebuttal_and_gatekeeper.py` (Issues #26 & #27)**:
   - Testa `parse_and_draft_rebuttal` (`noul rebuttal parse reviews.txt`) separando `Reviewer 1` e `Reviewer 2`, categorizando objeções, sinalizando itens que exigem novos experimentos (`requires_new_experiment=True`) e gerando saída em Markdown e LaTeX.
   - Testa `audit_submission_readiness` (`noul gatekeeper audit draft.tex --venue neurips`) verificando detecção de quebra de anonimato em links do GitHub (`github.com/johndoe/project`) e auto-citações em primeira pessoa, checklist do venue, scanner de *overclaiming* e cálculo do `readiness_score` (0–100) + `desk_reject_risk`.
5. **`tests/test_landing_page.py` (Issue #28)**:
   - Verifica a presença e integridade de `site/index.html`, `site/styles.css`, `site/app.js`, `site/favicon.svg`, `site/og-card.svg`, `scripts/build_site.py` e `.github/workflows/deploy-pages.yml`.
   - Valida que `site/index.html` contém o simulador de terminal (`noul compare`, `noul review`, `noul scaffold`), comandos `uvx noul-search-agent` e `npx noul-search-agent`, configurações MCP (Claude, Cursor, Gemini/Antigravity), os 4 perfis de usuários, metatags Open Graph e suporte a modo claro/escuro, e que `python3 scripts/build_site.py --check` retorna código `0`.
