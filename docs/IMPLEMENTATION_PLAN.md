# Plano de Implementação — Fase 3: Deep Dive Técnico & Métricas de Engenharia (Issues #08, #09, #10, #11, #12, #13)

> **Instruções para o Agente Executor (GitHub Copilot / Antigravity):**
> Implemente as **Etapas 1 a 6** da Fase 3 na ordem exata abaixo.
> **Regras de Ouro:**
> 1. Os **90 testes existentes** (P0 + Fase 1 + Fase 2) **devem continuar passando (`90/90 OK`)** em todas as etapas, inclusive com `NOUL_FORCE_PYDANTIC_FALLBACK=1`.
> 2. Mantenha `noul/` utilizando apenas a biblioteca padrão do Python (com `noul.schemas._compat` para compatibilidade Pydantic v2).
> 3. Adicione `if str(Path(__file__).resolve().parent) not in sys.path:` em `tests/_support.py` (ou nos novos arquivos de teste) para que cada arquivo de teste também possa ser executado individualmente (`python3 -m unittest tests/test_xyz.py`).
> Ao final de cada etapa, execute:
> ```bash
> python3 -m unittest discover -s tests -p "test_*.py" -v
> ```

---

## Etapa 1 — Contratos de Dados de Hardware e Análise Técnica (`noul/schemas/hardware.py` e `noul/schemas/technical.py`)

### 1.1. Criar `noul/schemas/hardware.py`
Implementar usando `from noul.schemas._compat import BaseModel, Field`:
```python
from typing import List, Optional
from noul.schemas._compat import BaseModel, Field


class PrecisionMemory(BaseModel):
    weights_gb: float
    kv_cache_8k_context_gb: float
    recommended_min_vram_gb: float


class HardwareFeasibility(BaseModel):
    paper_id: str
    total_parameters_billion: float
    active_parameters_billion: Optional[float] = None
    detected_model_sizes_b: List[float] = Field(default_factory=list)
    architecture_type: str = "dense"  # "dense" | "moe" | "encoder_only" | "rnn_ssm"
    fp16: PrecisionMemory
    int8: PrecisionMemory
    int4: PrecisionMemory
    reported_training_hardware: Optional[str] = None
    operational_tier: str = "workstation"  # "edge_consumer_gpu" | "workstation" | "datacenter_cluster"
    notes: List[str] = Field(default_factory=list)
```

### 1.2. Criar `noul/schemas/technical.py`
```python
from typing import Any, Dict, List, Optional
from noul.schemas._compat import BaseModel, Field


class BenchmarkTable(BaseModel):
    table_id: str
    caption: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    normalized_metrics: Dict[str, Any] = Field(default_factory=dict)


class GroundedClaim(BaseModel):
    claim: str
    status: str  # "verified" | "partially_supported" | "extrapolated"
    citation_tag: str  # Ex: '[pág. 4, Tabela 2: "...trecho..."]'
    page: Optional[int] = None
    table_or_section: Optional[str] = None
    verbatim_quote: Optional[str] = None
    numbers_checked: List[str] = Field(default_factory=list)


class MathItem(BaseModel):
    index: int
    kind: str  # "equation" | "algorithm"
    label: str
    latex: str
    python_code: str
    variables: List[str] = Field(default_factory=list)


class AblationEntry(BaseModel):
    component: str
    metric: str
    baseline_value: float
    variant_value: float
    delta: float
    delta_percent: float
    is_marginal: bool = False


class PromptExtractionResult(BaseModel):
    paper_id: str
    prompts: List[Dict[str, Any]] = Field(default_factory=list)
    hyperparameters: Dict[str, Any] = Field(default_factory=dict)
    json_schemas: List[str] = Field(default_factory=list)
```
- Reexportar todos os novos schemas em `noul/schemas/__init__.py`.

---

## Etapa 2 — Parser Técnico (`noul/providers/pdf_parser.py`) & Issue #08: Extrator e Normalizador de Tabelas de Benchmarks (`extract_benchmark_tables`)

### 2.1. Criar `noul/providers/pdf_parser.py`
- Implementar utilitários determinísticos para obter e segmentar o texto de um artigo (`get_paper_corpus(paper_id: str) -> dict`):
  - Recupera o artigo via `noul_core.resolve_paper(paper_id)` e verifica se há texto completo em `noul_core.cache_get("ft:" + p["id"])` (ou chama `noul_core.fulltext(p["id"])` se `open_access` e disponível).
  - Divide o texto em páginas (detectando marcadores `\f`, `[Page N]`, `--- Page N ---`, ou paginando em blocos de ~3000 caracteres quando não houver marcadores explícitos) e identifica legendas de tabelas (`Table 1`, `Tabela 1`, `\caption{...}`).

### 2.2. Implementar `extract_benchmark_tables(paper_id: str)` em `noul/tools/technical_tools.py`
- **Requisitos Técnicos da Issue #08**:
  1. **Detecção e Parsing de Tabelas**:
     - Suportar **3 formatos**:
       1. Tabelas Markdown (`| Model | MMLU | GSM8K | ...`);
       2. Tabelas LaTeX (`\begin{tabular}{...} ... \end{tabular}`, dividindo linhas por `\\` e células por `&`, limpando `\textbf{}`, `\underline{}`, `\hline`, `\toprule`, `\midrule`, `\bottomrule`);
       3. Tabelas em texto plano / PDF (blocos após `Table N:` com cabeçalho de métricas e linhas alinhadas por espaços contendo pelo menos 2 números por linha) e pares `Benchmark: valor` no resumo/texto.
  2. **Normalização de Métricas e Tipos Primitivos**:
     - Mapear sinônimos de métricas/benchmarks para chaves canônicas:
       - `mmlu` → `"MMLU"`, `gsm8k` / `gsm-8k` → `"GSM8K"`, `humaneval` / `human-eval` → `"HumanEval"`, `mbpp` → `"MBPP"`, `math` → `"MATH"`, `hellaswag` → `"HellaSwag"`, `arc-c` / `arc_challenge` → `"ARC-Challenge"`, `truthfulqa` → `"TruthfulQA"`, `bleu` → `"BLEU"`, `rouge-l` / `rougel` → `"ROUGE-L"`, `f1` / `f1-score` → `"F1"`, `auc` / `auroc` → `"AUC"`, `accuracy` / `acc` → `"Accuracy"`, `perplexity` / `ppl` → `"Perplexity"`, `latency` → `"Latency_ms"`, `throughput` → `"Throughput"`.
     - Converter strings numéricas (`"84.5%"`, `"84.5 ± 0.3"`, `"42"`, `"12.4ms"`) em `float` ou `int` primitivos nos dicionários de `rows` e em `normalized_metrics`.
  3. **Retorno**:
     - `{"paper_id": p["id"], "title": p.get("title"), "tables": [BenchmarkTable(...).model_dump() ...], "normalized_summary": {...}}`.
  4. **Registro no MCP e CLI**:
     - Registrar `"extract_benchmark_tables"` em `noul_tools.TOOLS` (`{"paper_id": S}`, `required: ["paper_id"]`).
     - Adicionar subcomando `noul benchmarks <paper_id> [--pretty]` em `noul_cli.py`.

### 2.3. Testes Unitários (`tests/test_benchmark_tables.py`)
- Testar parsing de tabelas em LaTeX (`\begin{tabular}`), Markdown e texto plano.
- Verificar que linhas (modelos) e colunas (métricas) mantêm correspondência exata, que nomes como `gsm-8k` e `human-eval` são normalizados para `GSM8K` e `HumanEval` e que porcentagens `"78.4%"` viram `float` `78.4`.
- Testar o subcomando CLI `noul benchmarks <id>`.

---

## Etapa 3 — Issue #09: Estimador de Viabilidade de Hardware e Pegada de Inferência (`estimate_hardware_specs`)

### 3.1. Implementar `estimate_hardware_specs` em `noul/tools/hardware_tools.py`
- **Assinatura**: `estimate_hardware_specs(paper_id: str, total_params_b: Optional[float] = None, active_params_b: Optional[float] = None, context_length: int = 8192) -> dict`
- **Requisitos Técnicos da Issue #09**:
  1. **Extração de Porte do Modelo e Arquitetura**:
     - Extrair menções de parâmetros no texto/título/resumo:
       - Ex.: `7B`, `13B`, `70B`, `405B`, `1.5B`, `0.5B`, `350M` (`0.35B`), `110M` (`0.11B`), `8x7B` (MoE: total $\approx 46.7\text{B}$ ou $56\text{B}$, ativos $\approx 12.9\text{B}$ ou $14\text{B}$), `"671B parameters with 37B activated"`.
       - Se `total_params_b` não for passado e nenhum tamanho explícito for encontrado no texto, inferir um default conservador a partir da família citada (`BERT-base` → `0.11B`, `BERT-large` → `0.34B`, `GPT-2` → `1.5B`, `Llama` → `7.0B`, ou `7.0B` padrão documentado em `notes`).
     - Classificar `architecture_type` em `"moe"`, `"rnn_ssm"`, `"encoder_only"` ou `"dense"`.
  2. **Cálculo Exato de VRAM e KV Cache (`PrecisionMemory` para `fp16`, `int8`, `int4`)**:
     - Bytes por elemento: `fp16 = 2.0`, `int8 = 1.0`, `int4 = 0.5`.
     - `weights_gb = round(total_params_b * bytes_per_elem, 2)`.
     - Estimar $(L = \text{layers}, H = \text{kv\_heads}, d_h = d_{\text{head}})$ escalonando com o porte do modelo (ou extraindo do texto se explícito) e aplicar a fórmula da especificação para `seq_len = context_length` (padrão `8192`):
       $$\text{kv\_bytes} = 2 \times L \times H \times d_h \times \text{seq\_len} \times \text{bytes\_per\_elem}$$
       $$\text{kv\_cache\_8k\_context\_gb} = \text{round}\left(\frac{\text{kv\_bytes}}{1024^3}, 2\right)$$
       *(Para `"encoder_only"` e `"rnn_ssm"`, o KV cache recorrente não cresce linearmente com 8k tokens: usar `round(weights_gb * 0.05, 2)`).*
     - `recommended_min_vram_gb = round((weights_gb + kv_cache_8k_context_gb) * 1.15, 2)`.
  3. **Extração do Hardware Reportado pelos Autores (`reported_training_hardware`)**:
     - Capturar expressões como `"64x A100 80GB"`, `"8 NVIDIA H100 GPUs"`, `"TPU v4-1024"`, `"single RTX 4090"`.
  4. **Classificação de Viabilidade (`operational_tier`)**:
     - `"edge_consumer_gpu"`: se `int4.recommended_min_vram_gb <= 16.0` (roda em GPUs de consumo de 8–16 GB ou Apple Silicon / Edge);
     - `"workstation"`: se `16.0 < int4.recommended_min_vram_gb <= 48.0` (roda em RTX 3090/4090 24GB, A6000 48GB ou Mac Studio);
     - `"datacenter_cluster"`: se `int4.recommended_min_vram_gb > 48.0` (exige A100/H100 80GB ou múltiplas GPUs).
  5. **Registro no MCP e CLI**:
     - Registrar `"estimate_hardware_specs"` em `noul_tools.TOOLS`.
     - Adicionar subcomando `noul hardware <paper_id> [--params-b FLOAT] [--active-params-b FLOAT] [--context-length INT] [--pretty]` em `noul_cli.py`.

### 3.2. Testes Unitários (`tests/test_hardware_estimator.py`)
- Testar modelo pequeno (`7B` dense) → `edge_consumer_gpu` em INT4, verificando cálculo monotônico (`fp16.weights_gb > int8.weights_gb > int4.weights_gb`) e KV cache > 0.
- Testar modelo grande (`70B` ou `405B`) → `datacenter_cluster` e extração de `reported_training_hardware` (`"64 NVIDIA A100 80GB GPUs"`).
- Testar modelo MoE (`8x7B` / `"47B total, 13B active"`) → `architecture_type == "moe"` e `active_parameters_billion` preenchido.
- Testar subcomando CLI `noul hardware`.

---

## Etapa 4 — Issue #10: Verificação de Evidências e Atribuição Estrita (`verify_paper_claims` / `noul ground`)

### 4.1. Implementar `verify_paper_claims` em `noul/tools/technical_tools.py`
- **Assinatura**: `verify_paper_claims(paper_id: str, claims: Optional[list[str]] = None) -> dict`
- **Requisitos Técnicos da Issue #10**:
  1. Se `claims` não for fornecido ou estiver vazio, extrair automaticamente do resumo/texto as sentenças que contenham números, percentuais ou comparações de resultado para auditá-las.
  2. Segmentar o documento do artigo em passagens indexadas por `(page_num, table_or_section_label, sentence_text)`.
  3. Para cada `claim`:
     - Extrair todos os tokens numéricos da alegação (`numbers_checked`, ex.: `["84.5%", "2.3x"]`) e palavras-chave.
     - Buscar a passagem do documento original com maior sobreposição léxica e numérica.
     - Classificar `status`:
       - `"verified"`: todos os números de `claim` aparecem literalmente no documento original no mesmo contexto;
       - `"partially_supported"`: o tema/métrica aparece no documento, mas algum número diverge ou não consta;
       - `"extrapolated"`: a alegação faz uma dedução/extrapolação que **não** está sustentada literalmente no texto original.
     - **Formatação Obrigatória da Citação (`citation_tag`)**:
       - Quando verificado/parcialmente suportado: `[pág. X, Tabela Y: "...trecho verbatim..."]` (ou `[pág. X, Seção Y: "...trecho verbatim..."]` se fora de tabela);
       - Quando extrapolado: `[EXTRAPOLAÇÃO NÃO LITERAL — sem correspondência exata no texto; trecho mais próximo na pág. X: "..."]`.
  4. **Registro no MCP e CLI**:
     - Registrar `"verify_paper_claims"` em `noul_tools.TOOLS`.
     - Adicionar subcomando `noul ground <paper_id> [--claims "alegacao 1; alegacao 2"] [--pretty]` em `noul_cli.py`.

### 4.2. Testes Unitários (`tests/test_grounding_verifier.py`)
- Verificar que uma alegação fiel produz `status == "verified"` e `citation_tag` no formato exato `[pág. X, Tabela Y: "..."]`.
- Verificar que uma extrapolação com número inventado é sinalizada com `status == "extrapolated"` e alerta explícito.
- Testar subcomando CLI `noul ground`.

---

## Etapa 5 — Issue #11 (`extract_paper_math`) & Issue #12 (`analyze_ablation_tables`)

### 5.1. Implementar `extract_paper_math(paper_id: str, mode: str = "latex")` em `noul/tools/technical_tools.py` — [Issue #11]
- **Modos aceitos**: `mode` em `("latex", "python", "both")` (se outro valor for passado, lançar `HTTPError(400, ...)`).
- **Extração de Equações e Pseudocódigo**:
  - Capturar blocos `\begin{equation}...\end{equation}`, `\begin{align}...\end{align}`, `$$...$$`, `$...$` com igualdade matemática, equações em texto (`L_total = ...`, `Attention(Q, K, V) = ...`) e blocos `Algorithm N` / `\begin{algorithm}...\end{algorithm}`.
  - Para cada item (`MathItem`):
    - Gerar `latex` limpo e delimitado;
    - Gerar `python_code`: traduzir a expressão matemática ou algoritmo para uma função Python tipada (`def equation_1(...) -> float:` ou `def algorithm_1(...) -> Any:`) com docstring contendo a fórmula LaTeX original e `import math`;
    - Validar `ast.parse(python_code)` antes de retornar!
- **Registro no MCP e CLI**:
  - Registrar `"extract_paper_math"` em `noul_tools.TOOLS` (`{"paper_id": S, "mode": {"type": "string", "enum": ["latex", "python", "both"], "default": "latex"}}`, `required: ["paper_id"]`).
  - Adicionar subcomando `noul math <paper_id> [--mode latex|python|both] [--pretty]` em `noul_cli.py`.

### 5.2. Implementar `analyze_ablation_tables(paper_id: str)` em `noul/tools/technical_tools.py` — [Issue #12]
- **Lógica de Análise de Ablação**:
  - Localizar tabelas ou listas de resultados de ablação (linhas com `Full model` / `Baseline` / `Ours` e variantes `w/o ...`, `without ...`, `- ...`, `+ ...`, `no ...`).
  - Identificar o valor de referência (`baseline_value`, geralmente da linha `Full model` / `Ours` ou `Baseline`) e, para cada variante ablatada:
    - Calcular `delta = round(variant_value - baseline_value, 4)` e `delta_percent = round((delta / abs(baseline_value)) * 100.0, 2)` (se `baseline_value != 0`).
    - Marcar `is_marginal = abs(delta_percent) < 1.0`.
  - Ordenar a lista de componentes por `abs(delta_percent)` decrescente (do mais impactante para o mais marginal).
  - Gerar alertas em `warnings_and_alerts`:
    - Alertar sobre componentes cujo ganho/queda seja marginal ($|\Delta\%| < 1\%$);
    - Alertar quando um único componente secundário responde pela maior parte ($\ge 60\%$) do ganho total divulgado.
- **Registro no MCP e CLI**:
  - Registrar `"analyze_ablation_tables"` em `noul_tools.TOOLS` (`{"paper_id": S}`, `required: ["paper_id"]`).
  - Adicionar subcomando `noul ablation <paper_id> [--pretty]` em `noul_cli.py`.

### 5.3. Testes Unitários (`tests/test_math_and_ablation.py`)
- Testar `extract_paper_math` nos modos `"latex"` e `"python"`, validando que o código Python retornado passa em `ast.parse`.
- Testar `analyze_ablation_tables` verificando a ordenação por impacto percentual, o cálculo de `delta_percent`, a flag `is_marginal` e o alerta de componente dominante/marginal.
- Testar subcomandos CLI `noul math` e `noul ablation`.

---

## Etapa 6 — Issue #13: Extrator de Templates de Prompt e Protocolos de Inferência (`extract_prompts_and_decoding` / `noul prompts`)

### 6.1. Implementar `extract_prompts_and_decoding(paper_id: str)` em `noul/tools/technical_tools.py`
- **Requisitos Técnicos da Issue #13**:
  1. **Extração de Templates de Prompt preservando `{variavel}`**:
     - Varrer o texto procurando blocos de prompt (precedidos por `"System Prompt:"`, `"User Prompt:"`, `"Prompt Template:"`, `"Instruction:"`, blocos entre aspas/cercas de código ou contendo marcadores `{variable}`, `{{variable}}`, `<context>`, `<question>`, `"Let's think step by step"`, `"You are an expert"`).
     - **Nunca remover nem formatar** os marcadores `{variavel}`: extraí-los intactos e listar as variáveis detectadas em `variables: ["context", "question", ...]`.
  2. **Extração de Hiperparâmetros de Amostragem / Decodificação (`hyperparameters`)**:
     - Extrair valores reportados para:
       - `temperature` (ex.: `temperature = 0.7`, `temp of 0.0`, `T = 0.2`)
       - `top_p` (ex.: `top-p = 0.95`, `top_p=0.9`, `nucleus sampling p = 0.9`)
       - `top_k` (ex.: `top-k = 50`, `top_k=40`)
       - `max_tokens` (ex.: `max_tokens = 2048`, `max new tokens of 512`)
       - `repetition_penalty`, `beam_size`, `seed`
  3. **Extração de Esquemas JSON (`json_schemas`)**:
     - Capturar blocos JSON exemplificados nas instruções de prompt do artigo.
  4. **Registro no MCP e CLI**:
     - Registrar `"extract_prompts_and_decoding"` em `noul_tools.TOOLS` (`{"paper_id": S}`, `required: ["paper_id"]`).
     - Adicionar subcomando `noul prompts <paper_id> [--pretty]` em `noul_cli.py`.

### 6.2. Testes Unitários (`tests/test_prompt_extractor.py`)
- Testar `extract_prompts_and_decoding` verificando que marcadores `{context}`, `{question}` e `{{input}}` são preservados intactos no prompt extraído e listados em `variables`.
- Verificar extração correta de `temperature`, `top_p`, `top_k` e `max_tokens`.
- Testar o subcomando CLI `noul prompts <id>`.
