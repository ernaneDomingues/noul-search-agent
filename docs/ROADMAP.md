# Roadmap — noul-search-agent

> **Status em 31/10/2026.** Este roadmap foi escrito em 27/10/2026, antes das Fases 1–6 e da v1.0.0. A evolução
> agora segue o plano de refatoração aprovado em [`REFACTOR_PLAN.md`](REFACTOR_PLAN.md) (v2.0, um PR por fase);
> as seções abaixo ficam como histórico e lista de ideias. Estado dos itens P0 da seção 1:
>
> | P0 | Situação |
> |---|---|
> | 1 — HTTP sem autenticação/CORS `*` | Resolvido (Bearer, validação de `Host`, CORS por allowlist, escrita bloqueada via HTTP) |
> | 2 — arXiv `YY10.*` tratado como DOI | Resolvido (regex de DOI; teste de regressão) |
> | 3 — "Q1" falso | Resolvido (`tier` sem rótulo de quartil; teste de regressão) |
> | 4 — falsos positivos do classificador | Parcial: padrões negativos implementados; golden set mede 0,854 de acurácia (`tests/golden/`) |
> | 5 — variáveis de ambiente genéricas | Resolvido (`NOUL_DB_PATH`, sem mutar `os.environ`) |
> | 6 — import implícito de código vizinho | Resolvido (só com `NOUL_APP_DIR` explícito; removido na Fase 1 do refactor) |
> | 7 — instalador no Windows / sem backup | Resolvido (`sys.executable`, TOML seguro, `.bak`, JSON inválido não é tocado) |
> | 8 — conexões SQLite abertas | Resolvido (conexão fechada, WAL) |
> | 9 — timeout global da busca | Resolvido (`concurrent.futures.wait` + `cancel_futures`) |
> | 10 — fulltext falso-positivo / downloads sem limite | Resolvido (limite de 50 MB, mínimo de caracteres no HTML) |
> | 11 — User-Agent/e-mail falsos | Resolvido |
> | 12 — MCP parcialmente conforme | Parcial: versão negociada; Streamable HTTP, `outputSchema` e *annotations* na Fase 2a do refactor |
> | 13 — diretório relativo indefinido | Resolvido (`NOUL_WORKSPACE`) |
> | 14 — Europe PMC fora das fontes | Resolvido |
>
> Novo achado do golden set: o classificador de domínio acerta 0,62 (recall biomédico 0,44 e de economia 0,32;
> a maioria dos erros cai em `general_stem`). Corrigido na Fase 1c: 1,00 no conjunto de desenvolvimento e 0,975 no holdout de 40 casos escrito antes do ajuste.

Plano de melhorias, implementações e disponibilização ao público. Baseado na análise do código em 27/10/2026.

## 0. Diagnóstico

**Pontos fortes**
- Zero dependências obrigatórias (stdlib), fácil de auditar e instalar.
- Uma única camada de ferramentas (`noul_tools.TOOLS`) exposta por MCP (stdio), HTTP/OpenAPI e CLI.
- Instalador idempotente com desinstalação limpa para 6 clientes (Claude Code/Desktop, Copilot, Codex, Gemini, Antigravity).
- Skill bem estruturada (Evidence Meter, tabela de estudos, limitações, dossiês persistentes).
- Testes offline passando (em 27/10/2026: 3 testes; em 31/10/2026: 155 testes + golden sets).

**Pontos fracos**
- Repositório com apenas o commit inicial; quase todo o código está fora do controle de versão.
- Sem `pyproject.toml`, CI, versão única (`1.1.0` repetida em `noul_mcp.py` e `agents/gemini-extension.json`; `1.1` no User-Agent).
- `noul_core.py` concentra ~1.100 linhas (fontes, ranking, cache, fulltext, export).
- Testes não cobrem parsers, classificador, ranking nem segurança do servidor HTTP.
- Servidor HTTP inseguro para qualquer exposição (ver P0-1).

## 1. Bugs e riscos confirmados (P0 — corrigir antes de divulgar)

| # | Problema | Local | Evidência / impacto | Correção proposta |
|---|---|---|---|---|
| 1 | **Gravação arbitrária de arquivos via navegador.** Servidor HTTP sem autenticação, com `Access-Control-Allow-Origin: *` e `output_dir` livre. | `noul_mcp.py:129,136`; `noul_tools.py` (`save_research_dossier`, `download_papers`) | Testado: `POST /tools/save_research_dossier` com `Origin: https://evil.example` e `output_dir=/tmp/...` criou arquivos. Qualquer site aberto no navegador pode escrever no disco do usuário enquanto o servidor roda. | Remover CORS `*` (ou allowlist), exigir token Bearer, validar header `Host` (DNS rebinding), confinar caminhos a um `NOUL_WORKSPACE`, desabilitar ferramentas de escrita no modo HTTP por padrão. |
| 2 | **IDs arXiv de outubro tratados como DOI.** O teste `"10." in ident` casa com `2410.12345`. | `noul_core.py:663` | `resolve_paper("arxiv:2410.12345")` consulta `doi.org/arxiv:2410.12345` → 404. Afeta todo artigo arXiv `YY10.*` fora do cache. | Detectar DOI por regex `^(doi:)?(https?://(dx\.)?doi\.org/)?10\.\d{4,9}/`. |
| 3 | **"Q1" falso por substring de periódico.** | `noul_core.py:181` | `Procedia Computer Science`, `Journal of Food Science`, `Stem Cells International` → `('high', 'Q1')`. "Q1" sugere quartil JCR/SJR real, o que é enganoso. | Casar nomes exatos/normalizados; usar `summary_stats` do OpenAlex (`2yr_mean_citedness`, `h_index`); renomear `tier` para `impact_band` sem rótulo "Q". |
| 4 | **Classificador com falsos positivos.** | `noul_core.py:136` | Resumo transversal com "future meta-analyses are needed" → `meta-analysis`; "non-randomized clinical trial" → `rct`. Distorce o Evidence Meter. | Priorizar tipos estruturados (PubMed `PublicationType`, tipo OpenAlex), dar peso maior ao título, padrões negativos, retornar `evidence_confidence` e `evidence_reason`. |
| 5 | **Variáveis de ambiente genéricas.** `DB_PATH` e `UNPAYWALL_EMAIL` são definidas globalmente. | `noul_tools.py:12-13` | Se o usuário já tiver `DB_PATH` de outro app, o cache do noul é gravado no banco desse app; vaza para subprocessos. | Usar `NOUL_DB_PATH` e ler configuração sem mutar `os.environ`. |
| 6 | **Import implícito de código vizinho.** | `noul_tools.py:20` | Se existir `../noul-search-app/noul/core.py`, ele substitui o core silenciosamente. | Tornar opt-in apenas via `NOUL_APP_DIR` explícito. |
| 7 | **Instalador quebra no Windows e não faz backup.** | `install.py:73-114` | `python3` geralmente não existe no Windows; caminho com `\` dentro de string TOML básica gera TOML inválido; JSON com comentário/ inválido derruba o instalador; configs do usuário são sobrescritas sem `.bak`. | Usar `sys.executable` (ou `uvx`), strings TOML literais (`'...'`), backup antes de editar, mensagem clara para JSON inválido. |
| 8 | **Conexões SQLite não fechadas.** | `noul_core.py:54` | `with sqlite3.connect()` faz commit mas não fecha; nova conexão a cada `cache_get/set` (centenas por busca). | Conexão única por processo com WAL, ou fechar explicitamente. |
| 9 | **Timeout global da busca ineficaz.** | `noul_core.py:614` | O `with ThreadPoolExecutor` espera todas as threads ao sair; `fut.result(timeout=18)` é sequencial. | `concurrent.futures.wait(..., timeout=)` + `shutdown(wait=False, cancel_futures=True)`. |
| 10 | **Fulltext falso-positivo e downloads sem limite.** | `noul_core.py:789-797,1021` | Páginas HTML de landing/paywall viram "fulltext disponível" (inclui scripts/menus); downloads lidos inteiros em memória, sem limite de tamanho. | Remover `<script>/<style>`, detectar paywall, checar `Content-Type`, streaming para disco com limite (ex.: 50 MB). Em modo hospedado, bloquear IPs privados (SSRF). |
| 11 | **Identificação incorreta para as APIs.** | `noul_core.py:34,84` | User-Agent aponta para `https://github.com/noul-search-agent` (inexistente); e-mail padrão `research@noul.local` é falso (o OpenAlex recusa placeholders). | URL real do repositório; omitir `mailto` quando não configurado. |
| 12 | **MCP parcialmente conforme.** | `noul_mcp.py:88`, `noul_tools.py:444` | `initialize` ecoa qualquer `protocolVersion`; `/mcp` HTTP não implementa Streamable HTTP (sessão, SSE, 202 para notificações); argumentos não são validados contra o schema. | Negociar a partir de uma lista de versões suportadas; validar argumentos; implementar transporte HTTP conforme a spec (ver seção 3). |
| 13 | **Diretório relativo indefinido no MCP.** | `noul_tools.py` (`./research`, `./papers`) | O `cwd` do processo MCP é definido pelo cliente; dossiês/PDFs vão parar em locais imprevisíveis. | `NOUL_WORKSPACE` configurável e suporte a MCP *roots*. |
| 14 | README cita EuropePMC como fonte de busca, mas `SOURCES` não o inclui (só é usado para fulltext). | `README.md`, `noul_core.py` | Documentação inconsistente. | Adicionar EuropePMC como fonte (P2) ou ajustar o README. |

Cada correção deve vir com um teste de regressão offline.

## 2. Engenharia e qualidade (P1)

1. **Empacotamento**: transformar em pacote `noul/` com `pyproject.toml`, entry points `noul` (CLI) e `noul-mcp` (servidor), extras `[pdf]` (`pypdf`) e `[server]` (dependências do modo hospedado). Versão única em `noul/__init__.py`.
2. **Modularização** de `noul_core.py`: `sources/{openalex,pubmed,s2,crossref,arxiv,europepmc}.py`, `classify.py`, `ranking.py`, `cache.py`, `fulltext.py`, `export.py`, `http.py`.
3. **Cliente HTTP robusto**: retry com backoff exponencial e respeito a `Retry-After` (429/5xx); limitador por host (Semantic Scholar sem chave ≈ 100 req/5 min; NCBI 3 req/s sem chave, 10 com chave; arXiv pede 1 req/3 s); gzip.
4. **Chaves de API opcionais (BYOK)**: `OPENALEX_API_KEY` (o OpenAlex hoje oferece chave gratuita com orçamento diário 10× maior que o anônimo), `S2_API_KEY` (header `x-api-key`), `NCBI_API_KEY`, `NOUL_EMAIL`. Carregar `.env` e publicar um `.env.example`.
5. **Cache**: TTL por tipo, limite de tamanho, comandos `noul cache stats|prune|clear`.
6. **Logs** via `logging` em stderr (`NOUL_LOG_LEVEL`) — nunca em stdout no modo stdio.
7. **Qualidade de código**: type hints, `ruff` (lint + format), `mypy`/`pyright`, `pre-commit`.
8. **Testes**:
   - fixtures offline para cada parser (JSON OpenAlex/S2/Crossref, XML PubMed, Atom arXiv);
   - regressão dos bugs P0;
   - *golden set* do classificador (~100 resumos rotulados) com métrica de precisão/recall;
   - testes de segurança do servidor HTTP (CORS, token, confinamento de caminhos);
   - migrar para `pytest` + cobertura; job noturno de *smoke test* com rede (não bloqueante).
9. **CI (GitHub Actions)**: matriz Python 3.10–3.14 × Ubuntu/macOS/Windows; lint, testes, build; CodeQL e Dependabot.

## 3. Novas funcionalidades (P2)

**Qualidade da evidência**
- Ferramenta determinística `evidence_meter(classifications)`: o LLM informa a postura de cada artigo e a ferramenta calcula as porcentagens bruta e ponderada por desenho de estudo — resultado reprodutível entre agentes.
- Sinais tipo GRADE: extração de tamanho amostral (`n = …`), duração, grupo controle, cegamento.
- Detecção de **retratações** (Crossref `update-to` / Retraction Watch) e de **preprint vs. revisado por pares**, ligando preprints às versões publicadas.

**Busca**
- EuropePMC, bioRxiv/medRxiv e DOAJ como fontes; CORE opcional (requer chave).
- Expansão de consulta (MeSH para PubMed), filtros por autor, periódico e idioma.
- Re-ranqueamento BM25 sobre o conjunto recuperado; paginação por cursor do OpenAlex (requer chave).

**Texto completo**
- Parsing JATS do PMC por seções (Métodos/Resultados) e *chunking* por seção; BM25 no lugar de contagem de termos; GROBID opcional.

**Dossiês**
- Histórico/versionamento (não sobrescrever slug existente), atualização incremental ("novos artigos desde a última versão").
- Exportação CSL-JSON e integração com Zotero/Obsidian; BibTeX com escape correto e tipos adequados (`@misc` para arXiv, `@inproceedings`).

**MCP**
- *Tool annotations* (`readOnlyHint`, `destructiveHint`, `openWorldHint`), `outputSchema` + `structuredContent`.
- *Prompts* MCP (ex.: `noul-research`), *resource templates*, notificações de progresso em buscas longas.
- Decisão de arquitetura: manter o stdio sem dependências e usar o SDK oficial `mcp` apenas no extra `[server]`, onde Streamable HTTP e OAuth são complexos de implementar à mão.

## 4. Plano de disponibilização para o público

Públicos-alvo, em ordem de encaixe com o produto atual:
1. **Pesquisadores e devs que já usam agentes** (Claude Code, Copilot, Codex, Gemini) — encaixe imediato.
2. **Usuários de apps de chat** (Claude Desktop/claude.ai, ChatGPT) — exigem instalação em 1 clique ou servidor remoto.
3. **Público geral sem agente** — exige uma aplicação web com LLM próprio (custo).

### Fase A — Repositório público pronto (semana 1)
- Concluir P0, fazer o primeiro commit completo e publicar a tag `v0.2.0`.
- README principal em inglês + `README.pt-BR.md`, GIF de demonstração e badges (CI, PyPI, licença).
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md` (Keep a Changelog), templates de issue/PR, arquivo `NOTICE` (Apache-2.0).
- Conformidade com os termos das fontes: atribuição ao arXiv, parâmetros `tool`/`email` no NCBI, licença da API do Semantic Scholar (revisar antes de qualquer uso hospedado/comercial), OpenAlex (CC0). Nunca redistribuir PDFs em modo hospedado — apenas links.
- Manter os avisos: Evidence Meter é heurístico; conteúdo de saúde não é aconselhamento médico; sem afiliação com Consensus/Elicit.

### Fase B — Distribuição para desenvolvedores (semanas 2–3)
1. **PyPI** com *Trusted Publishing* (OIDC no GitHub Actions, sem token): `uvx noul-search-agent`, `pipx install noul-search-agent`.
2. **Instalador como subcomando** (`noul install --all`) apontando para `uvx noul-search-agent mcp` em vez do caminho absoluto do clone — sobrevive a mudanças de diretório e recebe atualizações. Preferir os comandos oficiais de cada cliente quando existirem (ex.: `claude mcp add`).
3. **Registro oficial MCP** (`registry.modelcontextprotocol.io`): `server.json` com namespace `io.github.ernanedomingues/noul-search-agent`, pacote PyPI, publicação via `mcp-publisher` no CI a cada tag.
4. **Claude Desktop**: MCP Bundle (`.mcpb`) anexado às GitHub Releases, com `user_config` para e-mail e chaves de API (instalação em 1 clique).
5. **Marketplaces de plugins/extensões**: plugin do Claude Code (skill + subagente + MCP em um pacote), galeria de extensões do Gemini CLI e plugins do Copilot CLI.
6. **Imagem Docker** (`ghcr.io`) para o modo HTTP.
7. **Divulgação**: diretórios MCP (Smithery, Glama, mcp.so), PR em listas *awesome-mcp-servers*, post técnico com exemplos (EN/PT-BR).

### Fase C — Servidor MCP remoto hospedado (semanas 4–8)
Pré-requisitos: P0 resolvido e transporte Streamable HTTP conforme a spec.
- **Autenticação**: OAuth 2.1 conforme a spec de autorização MCP (ou chaves de API por usuário no início); cota e rate limit por usuário.
- **Modo hospedado sem sistema de arquivos**: `download_papers` retorna apenas links OA; dossiês ficam por usuário em banco/armazenamento de objetos ou são devolvidos como Markdown.
- **Proteções**: bloqueio de SSRF (IPs privados/metadata), limites de tamanho e tempo, logs estruturados, métricas e rastreamento de erros.
- **Orçamento das APIs**: tráfego concentrado em um único IP esgota cotas anônimas — usar chaves institucionais (OpenAlex, S2, NCBI), cache compartilhado (Postgres/Redis) e permitir BYOK.
- **Hospedagem**: Cloud Run, Fly.io, Render ou Railway com HTTPS e domínio próprio; escalar a zero no início.
- **Clientes**: conectores personalizados do claude.ai, conectores/apps do ChatGPT (MCP), servidores remotos no VS Code/Copilot. Para ChatGPT Actions, corrigir o `servers` do OpenAPI (hoje fixo em `http://localhost`) e adicionar autenticação.
- **Sustentabilidade**: plano gratuito com cotas, GitHub Sponsors/Open Collective, BYOK para uso intenso.

### Fase D — Aplicação web para o público geral (opcional, 2–3 meses)
- Precisa de um LLM próprio para classificar e sintetizar (hoje feito pelo agente do usuário): BYOK, plano pago ou integração com o `noul-search-app`.
- Interface: busca, Evidence Meter visual, tabela de estudos, exportação de dossiê; PT-BR e EN; acessibilidade.
- Jurídico: política de privacidade (LGPD/GDPR), termos de uso e aviso médico em destaque.

## 5. Cronograma resumido

| Sprint | Duração | Entregas |
|---|---|---|
| 0 | 1 semana | P0 (segurança e bugs) + testes de regressão + CI + commit inicial completo |
| 1 | 1 semana | Pacote PyPI, `uvx`, instalador refatorado, registro MCP, documentação pública |
| 2 | 2 semanas | Cliente HTTP robusto, chaves de API, cache, conformidade MCP, `.mcpb`, plugins |
| 3–4 | 4 semanas | Evidence Meter determinístico, retratações, novas fontes, dossiês incrementais |
| 5+ | 4+ semanas | Servidor remoto com OAuth; depois avaliar a aplicação web |

## 6. Métricas de sucesso
- **Adoção**: downloads no PyPI, instalações via registro/`.mcpb`, estrelas, issues de usuários externos.
- **Qualidade**: F1 ≥ 0,85 do classificador no *golden set*; ≥ 80% de cobertura no core.
- **Confiabilidade**: p95 da busca < 8 s; taxa de erro por fonte < 5%; zero vulnerabilidades abertas de severidade alta.
