# Plano de Implementação — Fase 5: Super Agente Autônomo e Multidisciplinar (Issues #16, #17, #18, #20, #21)

> **Instruções para o Agente Executor (GitHub Copilot / Antigravity):**
> Implemente as **Etapas 1 a 4** da Fase 5 na ordem exata abaixo.
> **Regras de Ouro:**
> 1. Os **128 testes existentes** (P0 + Fases 1, 2, 3 e 4) **devem continuar passando (`128/128 OK`)** em todas as etapas, inclusive com `NOUL_FORCE_PYDANTIC_FALLBACK=1`.
> 2. Mantenha `noul/` utilizando apenas a biblioteca padrão do Python (com `noul.schemas._compat` para compatibilidade Pydantic v2).
> 3. Todo código Python gerado pelos scaffolders (`source_code` e `unit_tests`) **deve** ser validado sintaticamente via `ast.parse()` antes de ser retornado.
> Ao final de cada etapa, execute:
> ```bash
> python3 -m unittest discover -s tests -p "test_*.py" -v
> ```

---

## Etapa 1 — Contratos de Dados de Revisão, Scaffolder e Hipóteses (`noul/schemas/review.py`, `noul/schemas/code.py`, `noul/schemas/analysis.py`)

### 1.1. Criar `noul/schemas/review.py`
Implementar usando `from noul.schemas._compat import BaseModel, Field` e `from noul.schemas.domain import ReviewVerdict`:
```python
from typing import List
from noul.schemas._compat import BaseModel, Field
from noul.schemas.domain import ReviewVerdict


class PersonaEvaluation(BaseModel):
    persona_role: str
    rubric_focus: str = ""
    strengths: List[str] = Field(default_factory=list)
    critical_flaws: List[str] = Field(default_factory=list)
    unverified_claims: List[str] = Field(default_factory=list)
    unanswered_questions: List[str] = Field(default_factory=list)
    score: float = Field(default=3.0, ge=1.0, le=5.0)
    confidence_score: float = Field(default=3.5, ge=1.0, le=5.0)


class PeerReviewReport(BaseModel):
    paper_id: str
    title: str = ""
    domain_evaluated: str
    rubric_applied: str
    verdict: ReviewVerdict
    overall_score: float = Field(default=3.0, ge=1.0, le=5.0)
    executive_summary: str
    evaluations: List[PersonaEvaluation] = Field(default_factory=list)
    meta_reviewer_notes: str
    missing_experiments_or_controls: List[str] = Field(default_factory=list)
    unanswered_questions: List[str] = Field(default_factory=list)
    markdown_report: str = ""
```

### 1.2. Criar `noul/schemas/code.py`
```python
from typing import List
from noul.schemas._compat import BaseModel, Field


class CodeScaffold(BaseModel):
    paper_id: str
    title: str = ""
    domain: str = "computer_science"
    target: str = "pytorch"  # "pytorch" | "statsmodels" | "r" | "clinical_protocol"
    language: str = "python"  # "python" | "r" | "markdown"
    framework: str = "torch"
    source_code: str
    unit_tests: str
    syntax_valid: bool = True
    shape_verified: bool = True
    dependencies: List[str] = Field(default_factory=list)
    requirements_txt: str = ""
    execution_notes: str = ""
```

### 1.3. Estender `noul/schemas/analysis.py` (para Issue #18)
Adicionar em `noul/schemas/analysis.py`:
```python
class ResearchHypothesis(BaseModel):
    index: int
    title: str
    rationale: str
    source_papers: List[str] = Field(default_factory=list)
    proposed_methodology: str
    required_baselines: List[str] = Field(default_factory=list)
    target_datasets_or_population: List[str] = Field(default_factory=list)
    evaluation_metrics: List[str] = Field(default_factory=list)


class ResearchGapReport(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)
    contradictions_found: List[str] = Field(default_factory=list)
    unexplored_complementarities: List[str] = Field(default_factory=list)
    hypotheses: List[ResearchHypothesis] = Field(default_factory=list)
    markdown_report: str = ""
```
- Reexportar `PersonaEvaluation`, `PeerReviewReport`, `CodeScaffold`, `ResearchHypothesis` e `ResearchGapReport` em `noul/schemas/__init__.py`.

---

## Etapa 2 — Issues #17 e #20: Motor de Revisão Crítica Multi-Persona Adaptável por Domínio (`review_paper` / `noul review`)

### 2.1. Criar Rubricas Modulares por Área (`noul/prompts/rubrics_cs.py`, `rubrics_biomedical.py`, `rubrics_economics.py`)
1. **`noul/prompts/rubrics_cs.py` (Diretrizes NeurIPS / ICLR — cumpre a Issue #17)**:
   - Definir as 3 personas estritas de conferência de IA:
     - **Persona 1**: `"Theoretical Reviewer (NeurIPS/ICLR)"` — avalia formulação matemática, garantias teóricas/convergência, complexidade computacional e clareza das equações/algoritmos.
     - **Persona 2**: `"Skeptical Empirical Reviewer (NeurIPS/ICLR)"` — avalia adequação dos baselines, presença de estudos de ablação ($\Delta\%$), significância estatística / variância entre seeds e modos de falha / OOD.
     - **Persona 3**: `"Systems & Reproducibility Engineer (NeurIPS/ICLR)"` — avalia viabilidade de memória VRAM / KV Cache, latência/throughput, disponibilidade de código/pesos verificados e especificação completa de hiperparâmetros/prompts.
2. **`noul/prompts/rubrics_biomedical.py` (Diretrizes CONSORT / STROBE / PRISMA)**:
   - Definir as 3 personas biomédicas:
     - **Persona 1**: `"Clinical Trial & Study Design Methodologist (CONSORT/STROBE)"` — avalia desenho do estudo, randomização, mascaramento (duplo-cego), grupo controle/placebo, critérios de elegibilidade e registro de protocolo.
     - **Persona 2**: `"Biostatistician & Statistical Power Auditor"` — avalia tamanho amostral ($N$), poder estatístico ($1-\beta$), intervalos de confiança, controle de confundidores, perdas de seguimento (*attrition*) e análise *intention-to-treat*.
     - **Persona 3**: `"Translational Medicine & Clinical Safety Reviewer"` — avalia relevância clínica dos desfechos (clínicos vs substitutos), relato de eventos adversos/toxicidade, generalização para pacientes reais (alertando se for estudo pré-clínico `in vitro`/animal) e conflitos de interesse.
3. **`noul/prompts/rubrics_economics.py` (Econometria Empírica & Identificação Causal)**:
   - Definir as 3 personas de economia/finanças:
     - **Persona 1**: `"Causal Identification Econometrician"` — avalia estratégia de identificação causal (Diff-in-Diff, Variáveis Instrumentais / 2SLS, Regressão Descontínua / RDD, Experimento Natural), plausibilidade de tendências paralelas, restrição de exclusão e riscos de endogeneidade / variável omitida.
     - **Persona 2**: `"Empirical Robustness & Inference Auditor"` — avalia agrupamento de erros-padrão (*clustered standard errors*), testes de placebo e falsificação, sensibilidade a janelas temporais/controles e qualidade da base em painel.
     - **Persona 3**: `"Policy & External Validity Economist"` — avalia magnitude econômica do efeito (além da significância estatística), validade externa institucional/geográfica, efeitos de equilíbrio geral e disponibilidade de dados/scripts de replicação.
4. **Rubrica Geral (`GENERAL_STEM`)**:
   - Personas: `"Experimental Design & Methodology Reviewer"`, `"Quantitative & Statistical Auditor"`, `"Reproducibility & Open Data Reviewer"`.

### 2.2. Implementar `review_paper` em `noul/tools/review_tools.py`
- **Assinatura**: `review_paper(paper_id: str, domain: Optional[str] = None, inspect_fulltext: bool = True) -> dict` (e função alias `review_paper_scientific(paper_id: str, domain: Optional[str] = None, inspect_fulltext: bool = True) -> dict`).
- **Lógica de Auditoria Multi-Persona**:
  1. Resolver o artigo e detectar o domínio via `domain_classifier.resolve_domain(paper, override_domain=domain)`.
  2. Coletar evidências do documento usando os extratores determinísticos já implementados:
     - Corpus textual e seções (`pdf_parser.get_paper_corpus`);
     - Artefatos abertos (`scan_paper_artifacts(..., verify_http=False)`);
     - Modos de falha e premissas (`detect_failure_modes`);
     - Matemática/algoritmos (`extract_paper_math`), tabelas de benchmark (`extract_benchmark_tables`) e ablações (`analyze_ablation_tables`).
  3. **Executar cada uma das 3 Personas do domínio selecionado**:
     - Preencher `strengths` (citando trechos/achados presentes no artigo, ex.: desenho RCT, presença de código aberto, equações formais, tabelas de benchmark, tamanho amostral);
     - Preencher `critical_flaws` (citando explicitamente falhas detectadas ou **seções/controles omitidos no documento**, ex.: *"Section omitted / not reported: no ablation table found in manuscript"*, *"Blinding and sample-size power calculation (1-beta) not reported in abstract/text"*, *"No clustered standard errors or placebo falsification test reported"*, *"No public code repository linked"*);
     - Preencher `unverified_claims` e `unanswered_questions` específicas daquela persona e domínio;
     - Calcular `score` (`1.0` a `5.0`) e `confidence_score`.
  4. **Consolidação pelo Meta-Reviewer**:
     - Calcular `overall_score = round(sum(e.score for e in evaluations) / 3.0, 2)`;
     - Determinar `verdict`:
       - `overall_score >= 4.0` e sem falhas críticas graves → `ReviewVerdict.ACCEPT`
       - `3.2 <= overall_score < 4.0` → `ReviewVerdict.ACCEPT_WITH_RESERVATIONS`
       - `2.3 <= overall_score < 3.2` → `ReviewVerdict.BORDERLINE`
       - `overall_score < 2.3` → `ReviewVerdict.REJECT`
     - Compilar `meta_reviewer_notes`, `missing_experiments_or_controls`, `unanswered_questions` e gerar `markdown_report` completo (com Veredito, Nota, Forças Metodológicas, Falhas Críticas por Persona e Perguntas Não Respondidas).
- **Registro no MCP e CLI**:
  - Registrar `"review_paper"` e `"review_paper_scientific"` em `noul_tools.TOOLS`.
  - Adicionar subcomando `noul review <paper_id> [--domain D] [--no-fulltext] [--pretty]` em `noul_cli.py`.

### 2.3. Testes Unitários (`tests/test_review_committee.py`)
- Verificar que auditar um paper médico (`biomedical`), um paper de IA (`computer_science`) e um paper de economia (`economics`) muda sensivelmente a rubrica (`rubric_applied`), os papéis das 3 personas (`persona_role`), as falhas críticas e as perguntas não respondidas.
- Verificar que na área de IA as 3 personas correspondem a Theoretical Reviewer, Skeptical Empirical Reviewer e Systems & Reproducibility Engineer sob diretrizes NeurIPS/ICLR (Issue #17).
- Verificar que as falhas identificadas citam trechos ou seções/controles omitidos no documento analisado.
- Testar `review_paper`, o alias `review_paper_scientific` e o comando CLI `noul review <id> --pretty`.

---

## Etapa 3 — Issues #16 e #21: Scaffolder Poliglota de Código e Protocolos Experimentais (`scaffold_implementation` / `noul scaffold`)

### 3.1. Criar `noul/prompts/scaffolding.py` e `noul/tools/scaffold_tools.py`
- **Assinaturas**:
  - `scaffold_implementation(paper_id: str, target: Optional[str] = None, domain: Optional[str] = None) -> dict`
  - `scaffold_paper_code(paper_id: str, target_language: Optional[str] = None, domain: Optional[str] = None) -> dict` (alias compatível com a especificação).
- **Resolução de Domínio e Target (`target`)**:
  - Valores aceitos para `target` (case-insensitive): `None`, `"auto"`, `"pytorch"`, `"torch"`, `"python"`, `"statsmodels"`, `"econ"`, `"r"`, `"protocol"`, `"clinical_protocol"`, `"markdown"`. (Lançar `HTTPError(400, ...)` se um target desconhecido for passado).
  - Se `target` for `None` ou `"auto"`, selecionar a partir do domínio detectado (`resolve_domain(paper, override_domain=domain)`):
    - `computer_science` → `target = "pytorch"`
    - `economics` → `target = "statsmodels"`
    - `biomedical` / `general_stem` → `target = "clinical_protocol"`
- **Geração Fiel às Variáveis e Equações do Artigo**:
  Extrair equações, variáveis e algoritmos do artigo via `extract_paper_math(paper_id, mode="both")`, dimensões/hiperparâmetros via `estimate_hardware_specs` / `extract_prompts_and_decoding` e métricas via `extract_benchmark_tables`:
  1. **Target `"pytorch"` (`COMPUTER_SCIENCE` — cumpre as Issues #16 e #21)**:
     - `language = "python"`, `framework = "pytorch"`, `dependencies = ["torch>=2.0.0"]`.
     - `source_code`:
       - Incluir docstring de topo com título, ID do artigo, variáveis/equações extraídas e bloco `# requirements.txt:\n# torch>=2.0.0`;
       - Definir `dataclass` de configuração (`ModelConfig`) refletindo dimensões extraídas do artigo (ex.: `d_model: int = ...`, `num_heads: int = ...`, `num_layers: int = ...`, além das variáveis das equações do artigo);
       - Implementar uma classe `class PaperModule(nn.Module):` (ou nome derivado do título do paper) com `__init__(self, config: ModelConfig)` e `def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:` **com anotações de tipo completas e sem blocos vazios (`pass`)**;
       - Incluir funções `train_step(model: nn.Module, batch: torch.Tensor, targets: torch.Tensor, lr: float = 1e-3) -> float:` e ` run_synthetic_demo() -> Tuple[int, ...]:`.
     - `unit_tests`:
       - Gerar suíte `unittest.TestCase` completa com tensores sintéticos (`dummy_x = torch.randn(batch_size, seq_len, config.d_model)`), executando `out = model(dummy_x)` e contendo asserções explícitas de formato:
         `expected_shape = (batch_size, seq_len, config.d_model)`
         `assert out.shape == expected_shape, f"Shape mismatch: {out.shape} != {expected_shape}"`
       - Incluir também teste de retropropagação (`loss = out.pow(2).mean(); loss.backward()`).
     - Validar `ast.parse(source_code)` e `ast.parse(unit_tests)`!
  2. **Target `"statsmodels"` ou `"r"` (`ECONOMICS` — cumpre a Issue #21)**:
     - Extrair variáveis e método econométrico do artigo (ex.: Diff-in-Diff, Panel FE, IV/2SLS, OLS) e nomes de variáveis citadas no resumo/equações.
     - Se `target == "statsmodels"`:
       - `language = "python"`, `framework = "statsmodels"`, `dependencies = ["numpy>=1.24", "pandas>=2.0", "statsmodels>=0.14", "linearmodels>=5.0"]`.
       - `source_code`: script Python completo que gera um `DataFrame` sintético reproduzindo a estrutura de painel/variáveis do artigo, estima o modelo econométrico principal com erros-padrão agrupados/robustos (`cov_type="cluster"` / `HC3`) e executa uma matriz de testes de robustez (`run_robustness_matrix`: especificação base, controles adicionais, teste de placebo temporal e subamostra).
       - `unit_tests`: testes unitários verificando a geração dos dados sintéticos e as dimensões da matriz de regressão. Validar `ast.parse(source_code)` e `ast.parse(unit_tests)`.
     - Se `target == "r"`:
       - `language = "r"`, `framework = "fixest"`, `dependencies = ["fixest", "sandwich", "lmtest"]`.
       - `source_code`: script R completo com geração de painel sintético, estimação `feols(...)` e matriz de testes de robustez; `unit_tests`: asserções `stopifnot(...)` em R.
  3. **Target `"clinical_protocol"` (`BIOMEDICAL` / `GENERAL_STEM` — cumpre a Issue #21)**:
     - `language = "python"`, `framework = "clinical_protocol+scipy"`.
     - `source_code`:
       - Documento/script híbrido contendo em docstring/Markdown estruturado o **Checklist Operacional e Protocolo Experimental Passo a Passo** (1. Objetivo e Hipótese, 2. Critérios de Elegibilidade / Inclusão e Exclusão, 3. Intervenção, Dosagem e Cegamento, 4. Desfechos Primários e Secundários, 5. Pipeline Analítico Padronizado) **mais** código Python executável que gera dados sintéticos dos braços Tratamento vs Controle (`generate_synthetic_cohort`) e roda o pipeline analítico padronizado (`run_protocol_analysis`).
     - `unit_tests`: testes unitários em Python validando o protocolo analítico com dados sintéticos. Validar `ast.parse(source_code)` e `ast.parse(unit_tests)`.
- **Registro no MCP e CLI**:
  - Registrar `"scaffold_implementation"` e `"scaffold_paper_code"` em `noul_tools.TOOLS`.
  - Adicionar subcomando `noul scaffold <paper_id> [--target pytorch|statsmodels|r|protocol] [--domain D] [--pretty]` em `noul_cli.py`.

### 3.2. Testes Unitários (`tests/test_scaffolders.py`)
- Testar `scaffold_implementation` para:
  - Artigo de IA (`computer_science` / `pytorch`): verificar presença de `class ... (nn.Module)`, `def forward(`, `assert out.shape == expected_shape`, `requirements.txt` inline, `syntax_valid == True` e `ast.parse` passando tanto em `source_code` quanto em `unit_tests`;
  - Artigo de Economia (`economics` / `statsmodels` e `r`): verificar modelo de regressão, matriz de robustez, dados sintéticos e `ast.parse`;
  - Artigo Biomédico (`biomedical` / `clinical_protocol`): verificar checklist de elegibilidade, protocolo de intervenção, pipeline analítico e `ast.parse`.
- Testar alias `scaffold_paper_code` e subcomando CLI `noul scaffold <id> --pretty`.

---

## Etapa 4 — Issue #18: Motor Autônomo de Lacunas e Geração de Hipóteses (`generate_research_hypotheses` / `noul hypotheses`)

### 4.1. Implementar `generate_research_hypotheses` em `noul/tools/comparative_tools.py`
- **Assinatura**: `generate_research_hypotheses(paper_ids: list[str], topic: Optional[str] = None, max_hypotheses: int = 3) -> dict`
- **Requisitos Técnicos da Issue #18**:
  1. Validar que `paper_ids` é uma lista com pelo menos **2 artigos distintos** ($2 \le \text{len} \le 15$). Se menos de 2 artigos forem informados, lançar `HTTPError(400, "Provide at least 2 paper_ids to cross-analyze research gaps and generate hypotheses")`.
  2. Para cada artigo em `paper_ids`, coletar:
     - Dimensões comparativas (`compare_papers`);
     - Modos de falha e premissas (`detect_failure_modes`);
     - Insights temáticos e etiquetas de citação `[Autor et al., Ano]` (`get_thematic_insights`).
  3. **Detectar Contradições e Complementariedades Inexploradas**:
     - `contradictions_found`: cruzar pares de artigos $(A, B)$ identificando tensões metodológicas, divergências de escopo/desenho, premissas conflitantes ou trade-offs de desempenho/custo, sempre citando `[AutorA et al., AnoA]` e `[AutorB et al., AnoB]`.
     - `unexplored_complementarities`: identificar pares $(A, B)$ onde a metodologia/força técnica de $A$ endereça diretamente uma limitação ou modo de falha declarado em $B$ (ou onde combinar a população/dataset de $A$ com o método de $B$ preenche uma lacuna aberta).
  4. **Formular Propostas Formais de Hipóteses (`hypotheses: List[ResearchHypothesis]`)**:
     - Gerar de `1` a `max_hypotheses` hipóteses estruturadas, cada uma cruzando explicitamente $\ge 2$ artigos (`source_papers: [id_a, id_b, ...]`) e preenchendo:
       - `title`: título conciso da hipótese de pesquisa;
       - `rationale`: justificativa técnica explicando qual lacuna/contradição entre `[AutorA, AnoA]` e `[AutorB, AnoB]` origina a hipótese;
       - `proposed_methodology`: esboço do protocolo experimental combinando/contrastando as técnicas;
       - `required_baselines`: lista dos métodos/baselines dos artigos-fonte que devem servir de controle;
       - `target_datasets_or_population`: datasets/benchmarks ou população clínica recomendados para o experimento;
       - `evaluation_metrics`: métricas quantitativas de sucesso e de controle de regressão/segurança.
  5. Gerar `markdown_report` legível e validar retorno via `ResearchGapReport`.
- **Registro no MCP e CLI**:
  - Registrar `"generate_research_hypotheses"` em `noul_tools.TOOLS` (`{"paper_ids": {"type": "array", "items": S}, "topic": S, "max_hypotheses": {"type": "integer", "default": 3}}`, `required: ["paper_ids"]`).
  - Adicionar subcomando `noul hypotheses <ids...> [--topic "..."] [--max 3] [--pretty]` em `noul_cli.py` (aceitando IDs separados por espaço ou vírgula).

### 4.2. Testes Unitários (`tests/test_research_gap_engine.py`)
- Testar `generate_research_hypotheses` com 2–3 artigos verificando:
  - Preenchimento de `contradictions_found` e `unexplored_complementarities`;
  - Formulação de hipóteses cruzando dois ou mais artigos (`len(h["source_papers"]) >= 2`);
  - Presença obrigatória de `proposed_methodology`, `required_baselines`, `target_datasets_or_population` e `evaluation_metrics` em cada hipótese;
  - Rejeição (`HTTPError 400`) quando apenas 1 artigo é passado;
  - Funcionamento do subcomando CLI `noul hypotheses id1 id2 --pretty`.
