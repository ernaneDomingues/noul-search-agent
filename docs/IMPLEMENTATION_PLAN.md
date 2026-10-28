# Plano de Implementação — Fase 2: Síntese, Comparação Multi-Paper, Obsidian e Expansão Booleana (Issues #04, #05, #06, #07)

> **Instruções para o Agente Executor (GitHub Copilot / Antigravity):**
> Implemente as **Etapas 1 a 5** da Fase 2 na ordem exata abaixo.
> **Regras de Ouro:**
> 1. Os **73 testes existentes** (`tests/test_agent.py` + suítes da Fase 1) **devem continuar passando (`73/73 OK`)** sem regressões.
> 2. Mantenha `noul/` funcionando apenas com a biblioteca padrão do Python (usando `noul.schemas._compat` para compatibilidade Pydantic v2).
> 3. Qualquer ferramenta nova que grave arquivos em disco (`export_obsidian_notes`) **deve** usar o confinamento de workspace (`_safe_output_dir` + proteção contra symlinks) e ser incluída em `WRITE_TOOLS` no `noul_mcp.py`.
> Ao final de cada etapa, execute:
> ```bash
> python3 -m unittest discover -s tests -p "test_*.py" -v
> ```

---

## Etapa 1 — Contratos de Dados de Análise e Síntese (`noul/schemas/analysis.py`)

### 1.1. Criar `noul/schemas/analysis.py`
Implementar usando `from noul.schemas._compat import BaseModel, Field`:
```python
from typing import Any, Dict, List, Optional
from noul.schemas._compat import BaseModel, Field


class ComparisonDimension(BaseModel):
    paper_id: str
    paper_title: str
    year: Optional[int] = None
    domain: str = "general_stem"
    evidence_level: str = "other"
    core_hypothesis: str
    methodology: str
    benchmarks_used: List[str] = Field(default_factory=list)
    primary_metrics: Dict[str, Any] = Field(default_factory=dict)
    reported_limitations: List[str] = Field(default_factory=list)


class ComparativeMatrixResult(BaseModel):
    papers: List[ComparisonDimension] = Field(default_factory=list)
    divergence_summary: str
    convergence_summary: str
    markdown_table: str


class ThematicInsightsResult(BaseModel):
    topic: str
    consolidated_consensuses: List[str] = Field(default_factory=list)
    methodological_tensions: List[str] = Field(default_factory=list)
    emerging_future_work: List[str] = Field(default_factory=list)
    citations_map: Dict[str, List[str]] = Field(default_factory=dict)


class BooleanQuerySuggestion(BaseModel):
    index: int
    strategy: str
    boolean_query: str
    rationale: str
    target_terms: List[str] = Field(default_factory=list)
```
- Reexportar `ComparisonDimension`, `ComparativeMatrixResult`, `ThematicInsightsResult` e `BooleanQuerySuggestion` em `noul/schemas/__init__.py`.

---

## Etapa 2 — Issue #04: Matriz Comparativa Parametrizada de Artigos (`compare_papers` / `noul compare`)

### 2.1. Implementar `compare_papers` em `noul/tools/comparative_tools.py`
- **Assinatura**: `compare_papers(paper_ids: list[str], inspect_fulltext: bool = False) -> dict`
- **Validação**:
  - `paper_ids` deve ser uma lista não-vazia de strings ($1 \le \text{len(paper\_ids)} \le 10$, padrão típico 3–5). Se vazia ou inválida, lançar `HTTPError(400, ...)`.
- **Extração Determinística das 5 Dimensões por Artigo (`ComparisonDimension`)**:
  Para cada artigo resolvido via `noul_core.resolve_paper(pid)` (e texto completo cacheado/extraído se `inspect_fulltext=True`):
  1. **`core_hypothesis` (Problema Central)**:
     - Extrair a sentença do resumo que declara o objetivo/hipótese (padrões: `"we investigate"`, `"we propose"`, `"we introduce"`, `"this study examines"`, `"objective:"`, `"aim:"`, `"background:"`, `"to evaluate"`, `"we present"`), ou a primeira sentença completa do resumo (ou o próprio título se o resumo estiver vazio).
  2. **`methodology` (Abordagem Metodológica)**:
     - Combinar o desenho de estudo (`evidence`) + domínio (`domain`) + sentença metodológica extraída do resumo (`"methods:"`, `"methodology"`, `"using a"`, `"we train"`, `"randomized"`, `"double-blind"`, `"cohort"`, `"difference-in-differences"`, `"panel data"`, `"architecture"`).
  3. **`benchmarks_used` (Datasets / Benchmarks / População)**:
     - Extrair nomes de datasets/benchmarks conhecidos (ex.: `MMLU`, `GSM8K`, `HumanEval`, `MBPP`, `MATH`, `ImageNet`, `CIFAR-10`, `CIFAR-100`, `GLUE`, `SuperGLUE`, `SQuAD`, `WMT`, `MS MARCO`, `BEIR`, `SWE-bench`, `MIMIC-III`, `MIMIC-IV`, `UK Biobank`, `NHANES`, `Penn World Table`) **e/ou** descritores de amostra/população (`n = ...`, `N = ...`, `patients`, `participants`, `adults`, `subjects`, `firms`, `countries`). Se nenhum for detectado explicitamente, preencher com `["Not explicitly stated in abstract"]`.
  4. **`primary_metrics` (Resultados Chave / Métricas)**:
     - Extrair pares de métricas quantitativas reportadas no texto (ex.: porcentagens `84.5%`, `p < 0.01`, `p = ...`, `AUC`, `F1`, `BLEU`, `ROUGE`, `HR`, `OR`, `RR`, `SMD`, `95% CI`, `speedup`, `VRAM`, `latency`) + um campo `"headline_finding"` contendo a principal sentença de resultado/conclusão (`"results:"`, `"findings:"`, `"conclusion:"`, `"achieved"`, `"outperforms"`, `"improved"`, `"reduced"`, `"demonstrate"`).
  5. **`reported_limitations` (Limitações Declaradas)**:
     - Extrair sentenças que mencionem limitações ou escopo restrito (`"limitation"`, `"however"`, `"future work"`, `"small sample"`, `"single-center"`, `"observational"`, `"preclinical"`, `"in vitro"`, `"computational cost"`, `"latency"`, `"lack of"`), além de sinalizar automaticamente se o estudo é `preclinical` ou se tem `evasive_availability_statement=True`.
- **Geração da Tabela Markdown (`markdown_table`)**:
  - Gerar tabela Markdown limpa com cabeçalho fixo:
    ```markdown
    | Paper | Core Problem | Methodology | Datasets / Population | Key Results | Declared Limitations |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    ```
    (Escapando eventuais caracteres `|` e quebras de linha dentro das células).
- **Síntese de Convergência e Divergência (`convergence_summary` e `divergence_summary`)**:
  - `convergence_summary`: descrever onde os artigos convergem (domínio compartilhado, desenhos de estudo em comum, benchmarks/populações em comum).
  - `divergence_summary`: destacar onde os métodos divergem tecnicamente (diferenças de desenho experimental/nível de evidência, abordagens metodológicas distintas, diferenças de escala/amostra, abertura de código/pesos e diferenças nas limitações).
- **Registro no MCP e CLI**:
  - Registrar `"compare_papers"` em `noul_tools.TOOLS` com schema `{"paper_ids": {"type": "array", "items": S}, "inspect_fulltext": {"type": "boolean", "default": False}}`, `required: ["paper_ids"]`.
  - Em `noul_cli.py`: adicionar subcomando `noul compare <ids...> [--fulltext] [--pretty]` que aceite tanto múltiplos argumentos posicionais (`noul compare id1 id2 id3`) quanto IDs separados por vírgula (`noul compare id1,id2,id3`). Quando `--pretty` for usado, imprimir a `markdown_table` seguida de `convergence_summary` e `divergence_summary`.

### 2.2. Testes Unitários (`tests/test_comparative_matrix.py`)
- Testar `compare_papers` com 3 artigos de características distintas verificando o preenchimento das 5 dimensões, a geração da tabela Markdown alinhada e os resumos de convergência/divergência.
- Testar o comando CLI `noul compare id1 id2 id3` e `noul compare id1,id2,id3 --pretty`.
- Testar erros de validação (`paper_ids` vazio).

---

## Etapa 3 — Issue #05: Extração e Síntese Temática de Insights por Tópicos (`get_thematic_insights` / `noul insights`)

### 3.1. Implementar `get_thematic_insights` em `noul/tools/comparative_tools.py`
- **Assinatura**: `get_thematic_insights(paper_ids: list[str], topic: Optional[str] = None) -> dict`
- **Requisitos Técnicos da Issue #05**:
  1. Resolver os artigos de `paper_ids` ($1 \le \text{len} \le 20$).
  2. Formatar a etiqueta de citação canônica de cada artigo como `"[Sobrenome et al., Ano]"` (se $\ge 3$ autores), `"[Sobrenome1 & Sobrenome2, Ano]"` (se 2 autores) ou `"[Sobrenome, Ano]"` (se 1 autor), e registrar no dicionário `citations_map: Dict[str, List[str]]` mapeando `citation_tag -> [paper_id, ...]`.
  3. **Categorizar transversalmente as descobertas nos 3 eixos obrigatórios (sem sumários isolados redundantes)**:
     - **`consolidated_consensuses` (Consensos Consolidados)**:
       - Agrupar os artigos por conceitos/termos técnicos, desfechos e achados convergentes;
       - Cada item da lista deve sintetizar uma convergência entre artigos ou evidência de alto nível, incluindo **obrigatoriamente** as citações explícitas `[Autor et al., Ano]` de todos os artigos que sustentam aquele ponto.
     - **`methodological_tensions` (Discrepâncias e Tensões Metodológicas)**:
       - Identificar contrastes reais entre os artigos do corpus:
         - Diferenças no nível da pirâmide de evidências (ex.: `meta-analysis`/`rct` vs `cohort`/`empirical`/`preclinical`);
         - Diferenças metodológicas ou de escala (benchmarks/populações distintas, presença vs ausência de artefatos verificados `has_code`/`has_weights`);
         - Resultados divergentes ou condicionais (*"however"*, *"contrary"*, *"mixed"*);
       - Cada item deve citar explicitamente os artigos em tensão (`[AutorA et al., 2023] vs. [AutorB et al., 2024]`).
     - **`emerging_future_work` (Direções Futuras e Lacunas)**:
       - Extrair e cruzar as limitações e propostas de trabalho futuro dos artigos, sempre acompanhadas de citação explícita `[Autor et al., Ano]`.
  4. Validar com `ThematicInsightsResult` e retornar `.model_dump(mode="json")`.
- **Registro no MCP e CLI**:
  - Registrar `"get_thematic_insights"` em `noul_tools.TOOLS` (`required: ["paper_ids"]`).
  - Em `noul_cli.py`: adicionar subcomando `noul insights <ids...> [--topic "..."] [--pretty]`.

### 3.2. Testes Unitários (`tests/test_thematic_insights.py`)
- Verificar que `get_thematic_insights` retorna os 3 eixos (`consolidated_consensuses`, `methodological_tensions`, `emerging_future_work`) e `citations_map`.
- Verificar que **100% das afirmações** nos 3 eixos contêm citações explícitas no formato `[... , YYYY]` (ou `n.d.`) e que cruzam os artigos em vez de listar resumos isolados.
- Testar o subcomando CLI `noul insights`.

---

## Etapa 4 — Issue #06: Exportação Estruturada para Fichamento em Markdown / Obsidian (`export_obsidian_notes`)

### 4.1. Implementar `export_obsidian_notes` em `noul/tools/comparative_tools.py`
- **Assinatura**: `export_obsidian_notes(paper_ids: list[str], vault_dir: Optional[str] = None, include_related_wikilinks: bool = True) -> dict`
- **Requisitos Técnicos e de Segurança da Issue #06**:
  1. **Confinamento de Workspace e Proteção Anti-Symlink**:
     - Resolver o diretório de destino usando `noul_tools._safe_output_dir(vault_dir, default_subdir="obsidian")` e verificar que nenhum componente do caminho é symlink (`_reject_symlink_path`), exatamente como em `download_papers` e `save_research_dossier`.
  2. **Formato de cada Nota Atômica `.md`**:
     - Nome do arquivo: `<Ano> - <PrimeiroAutor> - <TituloCurto>.md` (higienizado para sistemas de arquivos Windows/Linux/macOS, sem caracteres `\/:*?"<>|`).
     - **Frontmatter YAML válido** no topo:
       ```yaml
       ---
       title: "Título do Artigo"
       paper_id: "doi:10.1000/xyz"
       doi: "10.1000/xyz"
       year: 2024
       authors:
         - "Alice Smith"
         - "Bob Jones"
       venue: "Journal Name"
       domain: "computer_science"
       evidence_level: "empirical"
       citations: 42
       open_access: true
       has_code: true
       has_weights: false
       tags:
         - "noul/paper"
         - "domain/computer_science"
         - "evidence/empirical"
       ---
       ```
     - **Seções do Corpo Markdown**:
       - `# <Título>`
       - `## TL;DR` (síntese concisa com problema central + achado principal)
       - `## Key Findings & Methodology` (bullet points com hipótese, método, datasets/população, métricas e limitações extraídos da Etapa 2)
       - `## Open Artifacts` (links verificados de código, pesos e datasets quando presentes)
       - `## Related Notes (Wikilinks)` (lista de `[[NomeDaNota]]` apontando para os demais artigos exportados no mesmo lote e/ou referências em cache)
       - `## BibTeX` (bloco ```` ```bibtex ... ``` ```` gerado por `noul_core.export([paper], "bibtex")[0]`, garantindo sintaxe `@article{...}` válida).
  3. **Retorno**:
     - `{"vault_dir": str(dest_dir), "exported_count": len(notes), "notes": [{"paper_id": ..., "title": ..., "filepath": ..., "wikilink": "[[...]]"}]}`.
- **Integração no MCP e CLI**:
  - Registrar `"export_obsidian_notes"` em `noul_tools.TOOLS` (`{"paper_ids": {"type": "array", "items": S}, "vault_dir": S, "include_related_wikilinks": {"type": "boolean", "default": True}}`, `required: ["paper_ids"]`).
  - Adicionar `"export_obsidian_notes"` ao conjunto `WRITE_TOOLS` em `noul_mcp.py` (para que o modo HTTP continue exigindo `NOUL_HTTP_ALLOW_WRITE=1` + Bearer Token!).
  - Em `noul_cli.py`:
    - No subcomando `noul export <ids>`: aceitar `--format obsidian`, `--vault-dir <caminho>` e `--export-obsidian <caminho>`.
    - No subcomando `noul search "<query>"`: aceitar a flag `--export-obsidian <caminho>` (quando informada, exporta automaticamente os artigos retornados pela busca para o diretório Obsidian indicado e inclui `"obsidian_export"` no JSON/output!).

### 4.2. Testes Unitários (`tests/test_obsidian_export.py`)
- Testar `export_obsidian_notes` verificando:
  - Criação dos arquivos `.md` dentro do workspace;
  - Frontmatter YAML válido (delimitado por `---`, campos `title`, `paper_id`, `doi`, `year`, `authors`, `domain`, `evidence_level`, `tags`);
  - Presença de wikilinks `[[...]]` entre notas;
  - Presença do bloco `@article{` BibTeX válido;
  - Bloqueio de escrita fora do `NOUL_WORKSPACE` (erro 403);
  - Flags CLI `noul search "..." --export-obsidian ./vault` e `noul export id1,id2 --vault-dir ./vault`.

---

## Etapa 5 — Issue #07: Expansão Dinâmica de Pesquisa e Sugestão de Consultas Booleanas (`suggest_boolean_queries` / `noul expand`)

### 5.1. Implementar `suggest_boolean_queries` em `noul/tools/search_tools.py`
- **Assinatura**: `suggest_boolean_queries(query: str = "", paper_ids: Optional[list[str]] = None) -> dict`
- **Requisitos Técnicos da Issue #07**:
  1. Coletar o corpus de análise:
     - Se `paper_ids` for fornecido, resolver os artigos correspondentes;
     - Se apenas `query` for fornecido e houver resultados em cache (ou extrair termos da própria query + papers cacheados), analisar os títulos e resumos;
     - Se nem `query` nem `paper_ids` forem fornecidos, lançar `HTTPError(400, "Provide `query` and/or `paper_ids`")`.
  2. **Extração de Entidades Técnicas, Acrônimos e Termos Emergentes**:
     - Extrair acrônimos técnicos (`\b[A-Z][A-Z0-9-]{1,9}\b`, ex.: `RAG`, `LoRA`, `QLoRA`, `MMLU`, `RCT`, `CRISPR`, `BDNF`, `DiD`, `IV`, `GNN`, `SSM`);
     - Extrair termos técnicos e bigramas de alta frequência nos artigos que **não** fazem parte das palavras originais de `query` (evitando termos já saturados).
  3. **Geração de 3 Consultas Booleanas Estruturadas (`AND`, `OR`, `NOT`)**:
     - Sugestão `1` (`strategy: "precision_deepening"`): refina a consulta original com `AND` combinando os principais termos técnicos/acrônimos descobertos:
       Ex.: `(<nucleo_query>) AND ("<termo1>" OR "<termo2>")`
     - Sugestão `2` (`strategy: "synonym_and_method_expansion"`): expande para métodos, métricas ou populações adjacentes encontrados no corpus usando `OR` e `AND`:
       Ex.: `(<nucleo_query> OR "<acronimo>") AND ("<metodo1>" OR "<metodo2>")`
     - Sugestão `3` (`strategy: "noise_exclusion"`): aplica operador `NOT` para excluir ruído comum do domínio (ex.: `NOT ("survey" OR "review")` quando buscando estudos primários, ou `NOT ("mice" OR "in vitro")` em buscas clínicas humanas).
     - Garantir que todas as 3 sugestões contenham operadores booleanos explícitos em maiúsculas (`AND`, `OR`, `NOT`).
  4. **Retorno**:
     - `{"original_query": query, "extracted_acronyms": [...], "emerging_terms": [...], "suggestions": [BooleanQuerySuggestion(...).model_dump() for ...]}`.
- **Integração no MCP e CLI**:
  - Registrar `"suggest_boolean_queries"` em `noul_tools.TOOLS`.
  - Em `noul_cli.py`:
    - Adicionar subcomando `noul expand "<query>" [--ids id1,id2] [--run {1,2,3}] [--limit N] [--pretty]`:
      - Sem `--run`: exibe as 3 sugestões numeradas `[1]`, `[2]`, `[3]`;
      - Com `--run N` (atalho direto exigido pelo critério de aceitação da Issue #07!): seleciona automaticamente a sugestão `N` (`1`, `2` ou `3`) e dispara `search_papers(boolean_query, ...)` retornando a sugestão aplicada + os artigos encontrados!

### 5.2. Testes Unitários (`tests/test_boolean_expansion.py`)
- Testar `suggest_boolean_queries` com um conjunto de papers em cache verificando a extração de acrônimos/entidades e a presença de `AND`, `OR` e `NOT` nas 3 sugestões.
- Testar o atalho CLI `noul expand "llm quantization" --ids ... --run 1` verificando que ele dispara a busca imediatamente com a query sugerida #1.
