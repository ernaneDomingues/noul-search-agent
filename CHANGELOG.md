# Changelog

Todas as mudanças relevantes do Noul Search. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não publicado]

### Novidades
- **CSL-JSON**: `export_papers(format="csl-json")` e `noul export --format csl-json`, formato que o Zotero, o Mendeley e o
  Pandoc importam. Artigos retratados conhecidos levam a nota `RETRACTED`.
- **Exportar um projeto**: `export_papers(project=..., status=...)` e `noul export --project`, em qualquer formato, só com
  os incluídos (padrão) ou com outro grupo. No conector público esse argumento não existe, assim como `project_get`.
- **Zotero**: a nova ferramenta `zotero_push` (`noul zotero`) cria os artigos na sua biblioteca, numa coleção existente
  se você quiser, pula os que já estão lá e marca os retratados com a etiqueta `retracted`. Precisa de uma chave com
  permissão de escrita: use `NOUL_ZOTERO_API_KEY` e `NOUL_ZOTERO_USER_ID` ou
  `noul config --zotero-user-id ID --zotero-api-key-stdin`. A chave fica no `config.json` (permissão 0600 no Linux e no macOS; no Windows, as permissões da pasta do usuário) e não aparece
  nos resultados de `configure_noul` e `zotero_push`, nos logs nem nos erros. As credenciais não podem ser definidas nem removidas por `configure_noul`, cujo `clear`
  as preserva, para que o texto de um artigo não leve o modelo a trocar de conta nem a apagá-las; para remover a chave, use
  `noul config --zotero-remove-key`. As ferramentas também se recusam a ler ou sobrescrever o `config.json`.
- **Autores completos**: os registros passam a guardar até 100 autores (antes eram 10), e os projetos guardam todos os que
  a fonte devolver, para as exportações saírem completas. `project_get` continua mostrando os 4 primeiros.
- **Avisos fora do arquivo**: `export_papers` avisa à parte, sem alterar o arquivo, sobre ids não encontrados e artigos
  retratados, que agora vêm marcados em todos os formatos (BibTeX `note`, RIS `N1`, CSV `retracted`, CSL-JSON `note`).
- **Retratações**: `search_papers` marca com `retraction` os artigos que OpenAlex, PubMed ou Europe PMC indexam como
  retratados. `get_paper` também consulta o Crossref (avisos de retratação, retirada e remoção, inclusive os dados do
  Retraction Watch) e informa o DOI do aviso, a data e a fonte, além de `concern` (expressão de preocupação).
- **Preprint e versão publicada**: `get_paper` devolve `versions.published_as` (para um preprint) e
  `versions.preprint` (para um artigo publicado), com ids que podem ser encadeados em `get_paper`. Quando o vínculo vem
  da comparação de título e autores, e não do depósito da editora, `versions.published_as_match` avisa para confirmar.
- **Onde a retratação aparece**:
  - `evidence_meter` deixa os artigos retratados fora da contagem e os lista em `excluded`, com aviso e `notice_doi`;
  - `review_paper`, `review_paper_scientific`, `verify_paper_claims` e `detect_failure_modes` trazem
    `subject.retraction` e o sinal `integrity-retracted` em primeiro lugar;
  - `recommend_citations` não propõe artigos retratados;
  - `project_get` avisa sobre artigos retratados que continuam incluídos;
  - `sanitize_bibtex` mantém a entrada, mas a sinaliza (`retracted_entries`).
- **Skill**: as referências de busca e de revisão explicam como tratar artigos retratados e preprints com versão
  publicada. Entram duas evals novas: MMR e autismo, e a revisão de um artigo retratado.
- **Checagem incompleta não passa por artigo limpo**: se o Crossref não responder, `get_paper`, as ferramentas de
  revisão, `recommend_citations` e `sanitize_bibtex` avisam que a checagem não terminou.

Os contratos só ganham campos; nenhum campo existente muda. Uma mudança de comportamento: se todos os artigos
passados ao `evidence_meter` forem retratados, ele responde com erro 400 que lista os ids e os avisos de retratação,
em vez de percentuais.

## [2.0.0] — 2026-11

A 2.0.0 fecha a refatoração descrita em [`docs/REFACTOR_PLAN.md`](docs/REFACTOR_PLAN.md) (Fases 0 a 3, PRs #29 a #40).
O Noul continua sem LLM próprio: as ferramentas recuperam e estruturam evidências, e o modelo do seu cliente
(Claude, ChatGPT, Copilot, Cursor…) faz o julgamento.

### Quebras de compatibilidade
- **As ferramentas analíticas não dão mais veredito nem nota.** `review_paper`, `review_paper_scientific`,
  `verify_paper_claims`, `detect_failure_modes`, `audit_manuscript_draft`, `audit_submission_readiness` e
  `parse_and_draft_rebuttal` devolvem o contrato comum `AnalysisResult`: `signals` com trechos, seção e página,
  `checklist`, `rubric` com `how_to_judge`, `extraction_confidence`, `limitations` e `untrusted_fields`.
- **Trechos viram `Excerpt`.** `get_fulltext` e `query_fulltexts` devolvem objetos com texto, seção, página (quando a
  fonte tem páginas reais), relevância BM25 e `chunk_id` estável. Antes eram strings.
- **Rótulos em inglês** na saída de todas as ferramentas.
- **Limites explícitos nos esquemas de entrada.** Valores fora da faixa passam a ser recusados (antes eram cortados
  em silêncio):

  | Ferramenta | Campo | Faixa |
  |---|---|---|
  | `search_papers` | `limit` / `offset` | 1–40 / ≥ 0 |
  | `related_papers` | `limit` | 1–40 |
  | `generate_research_hypotheses` | `max_hypotheses` | 1–10 |
  | `query_fulltexts` | `ids` / `question` / `max_chunks_per_paper` | 1–20 ids / não vazia / 1–10 |
  | `download_papers` | `ids` | até 25 |
  | `synthesize_related_work` | `paper_ids` | até 20 |

  Os limites aparecem no `inputSchema` (`minimum`/`maximum`, `minItems`/`maxItems`, `minLength`/`pattern`) e valem
  também para as opções da CLI (`--limit`, `--chunks`, `--max`). No conector público, um `limit` acima de 40 passa
  a ser recusado; entre o teto público (25) e 40, continua sendo reduzido com aviso.
- **Removidos os arquivos da raiz** `noul_core.py`, `noul_tools.py`, `noul_mcp.py`, `noul_cli.py` e `install.py`
  (obsoletos desde a Fase 1).
- **Removido o servidor MCP v1** (`noul-mcp --legacy` e `src/noul/server/legacy.py`). `noul-mcp` roda sobre o SDK
  oficial.

### Guia de migração
| Se você usava… | Use agora |
|---|---|
| `verdict`, `overall_score` e notas por persona (`review_paper`) | `signals` por perspectiva, `questions_for_authors` e `rubric`; a recomendação é do seu modelo |
| `status` de `verify_paper_claims` | `best_match` / `other_matches` com seção e página, `numbers_found` / `numbers_missing` |
| `readiness_score` e `desk_reject_risk` (`audit_submission_readiness`) | `checklist` com `detected` e `signals`; a decisão de submeter é sua |
| respostas prontas de `parse_and_draft_rebuttal` | modelos com `[TODO]` para você preencher com resultados reais |
| `excerpts` como lista de strings | `excerpts[].text`, mais `section`, `page` e `relevance` |
| `import noul_core` / `noul_tools` / `noul_cli` | `noul.search.engine`, `noul.net`, `noul.library.*`, `noul.api`, `noul.cli` |
| `python noul_mcp.py` / `python noul_cli.py` | `noul-mcp` / `noul` (ou `uv run --project <pasta> noul-mcp`) |
| `python install.py` | `noul-install` (ou `noul install`) |
| `noul-mcp --legacy` | `noul-mcp`; por HTTP, `noul-mcp --http` |

Configurações criadas pelo `noul-install` antigo que apontavam para `noul_mcp.py` param de funcionar: rode
`noul-install` de novo (é idempotente).

### Novidades
- **Servidor MCP no SDK oficial** (stdio e Streamable HTTP): *annotations*, `outputSchema`/`structuredContent`,
  *prompts* `noul-research`, `noul-review` e `noul-write`, progresso por fonte, pedido único do e-mail de contato e
  perfis de ferramentas (`research`, `review`, `write`, `all`).
- **Projetos de pesquisa persistentes**: `project_create`, `project_list`, `project_get`, `project_add_papers`,
  `project_set_status` e `project_note`, o *resource* `noul://project/<slug>` e a CLI `noul project`; os dossiês
  passam a ser uma exportação do projeto. São 47 ferramentas no total.
- **Texto completo com seção e página**: trechos ranqueados com BM25 que não atravessam seções nem páginas, XML JATS
  do Europe PMC, palavras hifenizadas rejuntadas e IDs do arXiv resolvidos por três fontes.
- **Texto de terceiros marcado como não confiável** (`untrusted: true`, `untrusted_fields` e blocos
  `<untrusted_source nonce=…>`), para o modelo tratar como dado e não como instrução.
- **Instalação sem terminal**: executável único, extensões do Claude Desktop (`.mcpb`), página de instalação e
  conector remoto somente leitura (Cloud Run).
- **Skill em camadas**: `SKILL.md` como roteador e uma referência por jornada (busca, revisão, escrita e
  submissão). Nas 12 evals, a skill nova fez 100% contra 80% da v1.0.0.
- **Qualidade**: registro único de ferramentas, cliente HTTP resiliente com proteção SSRF, golden sets dos
  classificadores e das heurísticas de evidência, CI multiplataforma e contratos de entrada
  (`tests/fixtures/tools_list_v2.json`) e de saída (`tests/fixtures/output_schemas_v2.json`) fixados.

## [1.0.0] — 2026-10

Primeira versão pública: 41 ferramentas MCP, CLI, skill e subagentes, com o servidor MCP escrito à mão.
