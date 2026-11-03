# Changelog

Todas as mudanças relevantes do Noul Search. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

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
