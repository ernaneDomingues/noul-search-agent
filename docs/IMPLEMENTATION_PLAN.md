# Plano de Implementação — Fase 4: Auditoria Crítica de Modos de Falha & Diff Temporal no arXiv (Issues #14, #15)

> **Instruções para o Agente Executor (GitHub Copilot / Antigravity):**
> Implemente as **Etapas 1 a 3** da Fase 4 na ordem exata abaixo.
> **Regras de Ouro:**
> 1. Os **121 testes existentes** (P0 + Fases 1, 2 e 3) **devem continuar passando (`121/121 OK`)** em todas as etapas, inclusive com `NOUL_FORCE_PYDANTIC_FALLBACK=1`.
> 2. Mantenha `noul/` utilizando apenas a biblioteca padrão do Python (com `noul.schemas._compat` para compatibilidade Pydantic v2).
> 3. Qualquer chamada HTTP no `arxiv_client.py` deve passar pelos helpers seguros do `noul_core` (respeitando a proteção anti-SSRF P0-10 e o teto de bytes).
> Ao final de cada etapa, execute:
> ```bash
> python3 -m unittest discover -s tests -p "test_*.py" -v
> ```

---

## Etapa 1 — Contratos de Dados de Modos de Falha e Diff de Versões (`noul/schemas/technical.py`)

### 1.1. Estender `noul/schemas/technical.py`
Adicionar os seguintes modelos usando `BaseModel` e `Field` de `noul.schemas._compat`:
```python
class FailureModeEntry(BaseModel):
    category: str  # "long_context" | "noise_sensitivity" | "out_of_distribution" | "latency_or_compute" | "sample_or_population_bias" | "general_limitation"
    severity: str = "medium"  # "high" | "medium" | "low"
    description: str
    trigger_condition: str = ""
    evidence_quote: str = ""


class FailureModesReport(BaseModel):
    paper_id: str
    title: str = ""
    domain: str = "general_stem"
    failure_modes: List[FailureModeEntry] = Field(default_factory=list)
    critical_assumptions: List[str] = Field(default_factory=list)
    baseline_superiority_scenarios: List[str] = Field(default_factory=list)
    operational_limits: Dict[str, Any] = Field(default_factory=dict)


class VersionChangeItem(BaseModel):
    category: str  # "metric_change" | "claim_attenuation" | "claim_strengthening" | "new_baseline_or_experiment" | "author_or_title_change" | "methodology_change"
    summary: str
    before: Optional[str] = None
    after: Optional[str] = None


class ArxivVersionDiffReport(BaseModel):
    arxiv_base_id: str
    from_version: str
    to_version: str
    title_changed: bool = False
    authors_added: List[str] = Field(default_factory=list)
    authors_removed: List[str] = Field(default_factory=list)
    metric_deltas: List[Dict[str, Any]] = Field(default_factory=list)
    claim_shifts: List[VersionChangeItem] = Field(default_factory=list)
    new_baselines_or_datasets: List[str] = Field(default_factory=list)
    markdown_diff: str = ""
```
- Reexportar `FailureModeEntry`, `FailureModesReport`, `VersionChangeItem` e `ArxivVersionDiffReport` em `noul/schemas/__init__.py`.

---

## Etapa 2 — Issue #14: Detector de Casos de Falha e Premissas Críticas (`detect_failure_modes` / `noul failures`)

### 2.1. Implementar `detect_failure_modes` em `noul/tools/technical_tools.py`
- **Assinatura**: `detect_failure_modes(paper_id: str, inspect_fulltext: bool = True) -> dict`
- **Requisitos Técnicos da Issue #14**:
  1. Obter o corpus do artigo via `pdf_parser.get_paper_corpus(paper_id)` (se `inspect_fulltext=True`, inclui o texto completo quando disponível; caso contrário, usa o resumo e metadados).
  2. **Categorizar Vulnerabilidades e Cenários de Degradação (`failure_modes`)**:
     Varrer as sentenças do texto (com prioridade para seções `Limitations`, `Error Analysis`, `Failure Cases`, `Discussion`, `Threats to Validity`, `Broader Impact`, `Appendix`) e classificar em:
     - `"long_context"`: degradação em contextos longos (`long context`, `sequence length`, `context window`, `lost in the middle`, `quadratic complexity`, `O(n^2)`, `memory overflow`, `> 8k`, `> 32k`);
     - `"noise_sensitivity"`: sensibilidade a ruído, perturbações, prompts mal formatados, outliers ou dados ausentes (`noisy`, `noise`, `perturbation`, `adversarial`, `corrupted`, `missing data`, `prompt sensitivity`, `brittle`);
     - `"out_of_distribution"`: desvio de distribuição, falta de generalização entre domínios/línguas/populações (`out-of-distribution`, `OOD`, `domain shift`, `distribution shift`, `generalize to`, `unseen domains`, `cross-lingual`, `external validity`);
     - `"latency_or_compute"`: custo de latência, throughput baixo, alto consumo de VRAM/energia ou múltiplas chamadas de inferência (`latency`, `slow`, `computational cost`, `overhead`, `inference time`, `memory footprint`, `bottleneck`, `multi-step`);
     - `"sample_or_population_bias"`: amostra pequena, centro único, seguimento curto, viés de seleção ou evidência puramente pré-clínica (`small sample`, `single-center`, `retrospective`, `selection bias`, `attrition`, `short follow-up`, `in vitro`, `animal model`, `mice`);
     - `"general_limitation"`: demais sentenças com marcadores de limitação (`limitation`, `drawback`, `weakness`, `fails when`, `struggles with`, `does not support`, `cannot handle`).
     - Atribuir `severity = "high"` quando a sentença contiver termos fortes (`fails`, `collapses`, `severe`, `prohibitive`, `critical`, `cannot`, `only in vitro`), `"medium"` por padrão e `"low"` quando descrita como marginal/menor (`minor`, `slightly`).
  3. **Extrair Premissas Críticas Implícitas e Explícitas (`critical_assumptions`)**:
     - Capturar sentenças com `"assume"`, `"assumption"`, `"requires that"`, `"under the condition"`, `"provided that"`, `"i.i.d."`, `"stationary"`, `"parallel trends"`, `"exogeneity"`, `"no unmeasured confounding"`, `"oracle"`, `"clean labels"`.
  4. **Identificar Condições em que Métodos Clássicos / Baselines Superam o Método Proposto (`baseline_superiority_scenarios`)**:
     - Capturar sentenças que indiquem onde um baseline ou método clássico supera ou empata com o método proposto (`"outperformed by"`, `"baseline outperforms"`, `"underperforms"`, `"worse than"`, `"classical"`, `"simple baseline"`, `"linear model"`, `"BM25"`, `"logistic regression"`, `"in low-data"`, `"on small datasets"`, `"when N is small"`).
     - Além do texto, inspecionar `extract_benchmark_tables(paper_id)`: se houver uma tabela comparativa com linha `"Ours"` / modelo proposto e outra linha (baseline) tiver valor superior em alguma coluna de métrica (onde maior é melhor), registrar automaticamente esse cenário em `baseline_superiority_scenarios`!
  5. **Limites Operacionais (`operational_limits`)**:
     - Extrair limites quantitativos mencionados (ex.: `max_context_tokens`, `min_sample_size`, `max_latency_ms`, `supported_languages`).
  6. Validar com `FailureModesReport` e retornar `.model_dump(mode="json")`.
- **Registro no MCP e CLI**:
  - Registrar `"detect_failure_modes"` em `noul_tools.TOOLS` (`{"paper_id": S, "inspect_fulltext": {"type": "boolean", "default": True}}`, `required: ["paper_id"]`).
  - Adicionar subcomando `noul failures <paper_id> [--no-fulltext] [--pretty]` em `noul_cli.py`.

### 2.2. Testes Unitários (`tests/test_failure_modes.py`)
- Testar categorização dos modos de falha (`long_context`, `noise_sensitivity`, `out_of_distribution`, `latency_or_compute`, `sample_or_population_bias`).
- Testar extração de `critical_assumptions` e `baseline_superiority_scenarios` (tanto por texto quanto por tabela de benchmark onde o baseline vence em uma métrica).
- Testar o subcomando CLI `noul failures <id> --pretty`.

---

## Etapa 3 — Issue #15: Analisador de Diferenças entre Versões do Artigo (`diff_arxiv_versions` / `noul diff`)

### 3.1. Criar `noul/providers/arxiv_client.py`
- Implementar utilitários para buscar e comparar revisões específicas de um artigo do arXiv:
  1. **`parse_arxiv_version_args(arxiv_id: str, from_version: Optional[str] = "v1", to_version: Optional[str] = "v2") -> tuple[str, str, str]`**:
     - Extrair o `base_id` sem sufixo de versão (ex.: `"arxiv:2401.12345v1"` → `base_id = "2401.12345"`).
     - Se `arxiv_id` contiver duas versões separadas por vírgula ou `..` (ex.: `"2401.12345v1,2401.12345v2"` ou `"2401.12345v1..v3"`), extrair `from_version` e `to_version` automaticamente.
     - Normalizar números inteiros ou strings (`1` → `"v1"`, `"v2"` → `"v2"`). Validar que `base_id` tem formato válido de identificador arXiv (`^\d{4}\.\d{4,5}$` ou `[a-z-]+/\d{7}`) e que `from_version != to_version`, lançando `HTTPError(400, ...)` caso contrário.
  2. **`fetch_version_snapshot(base_id: str, version: str) -> dict`**:
     - Chave de cache: `"paper:arxiv:" + f"{base_id}{version}"` (ou `"arxiv_ver:" + f"{base_id}{version}"`).
     - Se já estiver em cache (incluindo eventual texto completo em `"ft:arxiv:" + f"{base_id}{version}"`), retornar o registro completo.
     - Caso contrário, consultar `https://export.arxiv.org/api/query?id_list={base_id}{version}` usando `noul_core._http_get`, parsear `<entry>` Atom (`title`, `summary`, `authors`, `published`, `updated`) e armazenar no cache.
  3. **`diff_snapshots(base_id: str, v_old: str, snap_old: dict, v_new: str, snap_new: dict) -> ArxivVersionDiffReport`**:
     - **Mudanças de título e autoria**:
       - `title_changed = (snap_old["title"] != snap_new["title"])`
       - `authors_added` e `authors_removed`
     - **Mudanças em métricas e números (`metric_deltas`)**:
       - Extrair pares `(label, number)` de tabelas e sentenças em `v_old` vs `v_new`; quando a mesma métrica/dataset/amostra aparecer em ambas as versões com valores distintos, adicionar `{"metric": label, "from_value": val_old, "to_value": val_new, "delta": round(val_new - val_old, 4)}`.
     - **Atenuação ou Reformulação de Alegações (`claim_shifts`)**:
       - Alinhar sentenças de `v_old` e `v_new` por similaridade (`difflib.SequenceMatcher`).
       - Detectar `"claim_attenuation"` quando palavras fortes/absolutas em `v_old` (`state-of-the-art`, `sota`, `proves`, `always`, `outperforms all`, `first`, `novel`, `significant`, `guarantees`) forem removidas ou trocadas por termos moderados em `v_new` (`competitive`, `suggests`, `often`, `comparable`, `empirical`, `promising`, `preliminary`, `may`).
       - Detectar `"claim_strengthening"` no sentido inverso, e `"methodology_change"` quando mudarem descrições de arquitetura/amostra/protocolo.
     - **Novos Baselines ou Datasets (`new_baselines_or_datasets`)**:
       - Detectar nomes de benchmarks, datasets ou baselines (ex.: `MMLU`, `GSM8K`, `HumanEval`, `GPT-4`, `Llama-3`, `Claude`, etc. ou linhas de tabelas de comparação) presentes em `v_new` mas ausentes em `v_old`.
     - **Formatação Markdown (`markdown_diff`)**:
       - Gerar relatório em Markdown claro com seções:
         - `## ArXiv Version Diff: <base_id> (<v_old> -> <v_new>)`
         - `### Metadata & Authorship Changes`
         - `### Metric & Table Number Changes`
         - `### Claim Shifts & Attenuations`
         - `### New Baselines & Datasets`
         - `### Text Diff Highlights`

### 3.2. Expor `diff_arxiv_versions` em `noul/tools/technical_tools.py`, `noul_tools.py` e `noul_cli.py`
- Registrar `"diff_arxiv_versions"` em `noul_tools.TOOLS`:
  - Schema: `{"arxiv_id": S, "from_version": {"type": "string", "default": "v1"}, "to_version": {"type": "string", "default": "v2"}}`, `required: ["arxiv_id"]`.
- Adicionar subcomando `noul diff <arxiv_id> [--from v1] [--to v2] [--pretty]` em `noul_cli.py`:
  - Quando `--pretty` for passado, imprimir `out["markdown_diff"]`.

### 3.3. Testes Unitários (`tests/test_arxiv_diff.py`)
- Testar `diff_arxiv_versions` comparando `v1` vs `v2` (com snapshots no cache e via mock da API Atom do arXiv) verificando:
  - Detecção de mudanças numéricas em tabelas/métricas (`metric_deltas`);
  - Detecção de atenuação de alegações (`claim_attenuation`, ex.: `"achieves state-of-the-art outperforming all baselines"` → `"achieves competitive performance comparable to recent baselines"`);
  - Detecção de novos baselines/datasets adicionados em `v2` e novos coautores;
  - Presença do destaque em Markdown (`markdown_diff`);
  - Comando CLI `noul diff 2401.12345 --from v1 --to v2 --pretty` e `noul diff 2401.12345v1,2401.12345v2`.
