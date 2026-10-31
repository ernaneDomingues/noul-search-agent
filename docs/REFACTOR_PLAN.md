# Plano de refatoração completa — noul-search-agent v2.0

## Contexto

O relatório técnico (https://claude.ai/artifact/U4sd4NRRxnghxC5GpSta4C) concluiu que o projeto deve **evoluir no mesmo repositório**, não ser reescrito. Premissa confirmada: o Noul **roda no cliente do usuário, sem LLM próprio**.

Problemas que a refatoração resolve:

1. **Dois núcleos acoplados.** Os arquivos da raiz (`noul_core.py` com 1.544 linhas, `noul_tools.py` com 1.154) convivem com o pacote `noul/`, que importa de volta `noul_core`. A ligação usa `_phase1()`/`importlib`, `sys.path.insert` e `NOUL_APP_DIR`.
2. **Ferramentas analíticas dão veredito por regex.** Exemplo: `review_paper` em `noul/tools/review_tools.py:633`, que emite ACCEPT/REJECT por limiar de pontuação.
3. **O MCP é escrito à mão** (`noul_mcp.py`). Faltam `outputSchema`, *annotations*, *prompts*, Streamable HTTP e *elicitation*.
4. **Não existe projeto de pesquisa persistente.** Só há um cache chave-valor e dossiês Markdown. A busca no texto completo é ingênua (`pick_chunks`, `noul_core.py:1260`).
5. **Só instala quem tem terminal e Python 3.10+.** Uma pessoa leiga não consegue instalar.
6. **Falta rede de qualidade e de diagnóstico.** Não há lint, tipagem nem cobertura, e o código não usa `logging` em lugar nenhum.

Resultado esperado: a v2.0, com núcleo único em `src/noul/`, um registro de ferramentas que gera MCP, OpenAPI, CLI, site e SKILL, MCP conforme a especificação, instalação sem terminal, projeto de pesquisa persistente e ferramentas que entregam evidências para o LLM julgar.

## Decisões confirmadas

- **Dependências obrigatórias:** `mcp`, `pydantic>=2.7`, `httpx` e `pypdf`. O fallback stdlib (`noul/schemas/_compat.py`, 339 linhas) e o servidor JSON-RPC artesanal serão removidos. O usuário leigo não sente essa mudança, porque o executável e o `.mcpb` já trazem tudo embutido.
- **Entrega:** um branch e um PR por fase (`refactor/phase-0-foundation`, …), e cada PR precisa passar no CI antes de começar a fase seguinte. O mesmo vale para as fases 2a e 2b. Nenhum push ou merge será feito sem o seu pedido explícito.
- **Interface externa preservada:** nomes das ferramentas MCP, subcomandos da CLI, entry points (`noul`, `noul-mcp`, `noul-install`) e variáveis `NOUL_*`. A única quebra intencional é o formato de saída das ferramentas analíticas, na Fase 3, que justifica a versão 2.0.0.

## Arquitetura-alvo

```
src/noul/
  __init__.py          # __version__ único (fonte de verdade p/ pyproject dinâmico)
  config.py            # get_config/set_config/workspace (de noul_core.py:60-160)
  logging.py           # logging estruturado em stderr, NOUL_LOG_LEVEL, trace_id por chamada
  net/http.py          # cliente httpx: retry exponencial, Retry-After, limitador por host, gzip, _validate_public_url (SSRF)
  net/cache.py         # SQLite WAL (de noul_core._conn/cache_get/cache_set), TTL por tipo, prune
  providers/           # openalex, pubmed, s2, crossref, arxiv, europepmc, unpaywall (1 por módulo) + arxiv_client, pdf_parser, domain_classifier, artifact_radar (já existentes)
  search/              # orquestração multifonte, dedupe, ranking, tiers (de noul_core.search)
  evidence/            # classify_evidence, EV_W, evidence_meter
  library/             # fulltext, chunking por seção, BM25, downloads, export (bibtex/ris/csv/csl-json), store.py (projetos — Fase 3)
  analysis/            # heurísticas atuais de noul/tools/* reorganizadas por jornada: technical, comparative, review, authoring, gatekeeper, scaffold, hardware
  prompts/             # rubricas e clarificação (já existentes)
  schemas/             # modelos Pydantic (já existentes, sem _compat)
  registry.py          # @tool(name, input=Model, output=Model, annotations=..., profiles=...) — fonte única
  server/mcp.py        # FastMCP (SDK oficial): stdio + Streamable HTTP, tools/prompts/resources a partir do registry
  server/http.py       # middleware: Bearer, Host allowlist, CORS allowlist, bloqueio de escrita, /healthz, /openapi.json
  cli/                 # argparse gerado do registry + formatadores --pretty (de noul_cli._format_pretty_*)
  install/             # instalador (de install.py) + assistente interativo
```

Os arquivos da raiz `noul_core.py`, `noul_tools.py`, `noul_mcp.py`, `noul_cli.py` e `install.py` viram *shims* finos durante a Fase 1: reexportam de `noul.*` com `DeprecationWarning`. Eles serão removidos na 2.0.0.

## Fase 0 — Fundação de qualidade (PR 1)

- No `pyproject.toml`: configurar `[tool.ruff]` (lint e format), `[tool.mypy]` em modo gradual (estrito só em `noul/registry.py`, `noul/net/` e `noul/schemas/`) e `[tool.pytest.ini_options]` com `pytest-cov`. Criar o extra `[dev]`.
- Rodar a suíte `unittest` atual sob `pytest`, sem reescrever os testes (`tests/_support.py` continua igual).
- Em `.github/workflows/ci.yml`: adicionar jobs de lint, mypy e cobertura (registrando a linha de base) e a matriz `ubuntu/macos/windows × 3.10–3.14`. Manter o passo `scripts/build_site.py --check`.
- Criar `tests/golden/` com cerca de 100 resumos rotulados para `classify_evidence` e cerca de 100 para `domain_classifier`. Um teste mede precisão e revocação e falha se ficarem abaixo da linha de base registrada.
- Criar o `CLAUDE.md`, com arquitetura-alvo, comandos de teste e a regra "ferramenta nova = registro + teste offline + caso no golden set ou em evals".
- Criar `.claude/settings.json` com dois hooks: PostToolUse com `ruff format`/`ruff check --fix` em `*.py`, e PreToolUse bloqueando edição manual de `site/tools-reference.json`.
- Criar `.claude/agents/mcp-conformance-reviewer.md` e `security-reviewer.md`, ambos somente leitura.
- Mudar o classificador PyPI para `Development Status :: 4 - Beta` e atualizar `docs/ROADMAP.md` com o estado real.
- **Gate:** CI verde e linhas de base de cobertura e de precisão registradas no PR.

## Fase 1 — Núcleo unificado em `src/noul/` (PR 2)

- Migrar para o layout `src/`. No pyproject, `version` fica dinâmica a partir de `noul.__version__`, e os entry points passam a apontar para `noul.cli:main`, `noul.server.mcp:main` e `noul.install:main`.
- Quebrar `noul_core.py` em `config`, `net/http`, `net/cache`, `providers/*`, `search`, `evidence` e `library/*`, movendo funções sem mudar o comportamento. Reaproveitar sem alteração: `_validate_public_url`, `download_paper_pdf` (limite de 50 MB), `resolve_paper`, `citation_graph`, `related_papers`, `export`, `fulltext` e `search` com `concurrent.futures.wait` + `shutdown(cancel_futures=True)`.
- Criar `net/http.py` sobre httpx, com retry exponencial em 429/5xx, respeito a `Retry-After` e um limitador por host (S2, NCBI e arXiv com limites conservadores; NCBI usa um limite maior quando há `NCBI_API_KEY`). Aceitar chaves opcionais `OPENALEX_API_KEY`, `S2_API_KEY` e `NCBI_API_KEY`.
- Criar `registry.py`: as 41 ferramentas migram de `noul_tools.TOOLS` (`noul_tools.py:699`) para `@tool`, com modelos Pydantic de entrada e saída. A validação manual de `call()` (`noul_tools.py:1136`) passa a ser feita pelo Pydantic.
- Eliminar `_phase1()`, `NOUL_APP_DIR`, `noul/core.py` e todos os `sys.path.insert` do código de produção.
- Fazer `scripts/build_site.py` gerar, a partir do registro, `site/tools-reference.json`, a lista de ferramentas da `SKILL.md`, a do subagente (`agents/claude-noul-researcher.md`) e a tabela do README, entre marcadores `<!-- tools:start/end -->`. Isso corrige as duas ferramentas que faltam hoje na SKILL.
- Fazer a CLI gerar os subparsers a partir do registro, mantendo os nomes atuais dos subcomandos (mapeados em `cli/aliases.py`) e os formatadores `--pretty` existentes.
- Adotar `logging` com `trace_id` por chamada, sempre em stderr (nunca em stdout no modo stdio).
- Remover `noul/schemas/_compat.py` e o job de CI `NOUL_FORCE_PYDANTIC_FALLBACK`.
- Ajustar os imports dos testes para `noul.*` e criar `tests/test_registry.py`, que cobre a geração de schemas e a paridade CLI ↔ MCP. Criar também testes de retry/backoff com `httpx.MockTransport`.
- **Gate:** os 155 testes e os novos passam importando só `src/noul/`, os artefatos gerados batem com o `--check` e a cobertura não fica abaixo da linha de base.

## Fase 2a — Plataforma MCP (PR 3)

- Criar `server/mcp.py` com o FastMCP do SDK oficial, com transporte stdio e Streamable HTTP em `/mcp`. Ele se registra a partir do `registry`, com:
  - `outputSchema` e `structuredContent`, vindos dos modelos de saída;
  - *annotations*: `readOnlyHint` por padrão, `destructiveHint`/`idempotentHint` em `download_papers`, `save_research_dossier` e `configure_noul`, e `openWorldHint` nas ferramentas que acessam a rede;
  - *prompts* `noul-research`, `noul-review` e `noul-write`;
  - *resources* `noul://dossier/<slug>`, como hoje em `noul_mcp.resources_list`;
  - notificações de progresso na busca multifonte.
- Portar as proteções HTTP de `noul_mcp.py` (`_is_loopback_host`, Bearer com `hmac.compare_digest`, allowlist de CORS, `WRITE_TOOLS` bloqueadas sem token + `NOUL_HTTP_ALLOW_WRITE`) para um middleware em `server/http.py`. Os testes de segurança atuais continuam valendo.
- Manter `/openapi.json` para ChatGPT Actions, gerado do registro.
- Criar perfis de ferramentas com `NOUL_TOOL_PROFILE=research|review|write|all` (padrão `research`, cerca de 12 ferramentas) e flag `--profile`.
- Usar *elicitation*: quando faltar o e-mail, `configure_noul` e as ferramentas que precisam dele pedem o dado ao cliente. Se o cliente não suportar, mantém-se o aviso atual.
- **Gate:** testes de protocolo com o cliente do SDK `mcp` em memória e conexão manual validada em Claude Code, Claude Desktop, VS Code, Cursor e ChatGPT.

## Fase 2b — Instalação sem terminal (PR 4)

- Criar `.github/workflows/release.yml`, que na tag `v*` gera executáveis PyInstaller para Windows, macOS e Linux e publica no GitHub Releases. A assinatura de código e a notarização Apple usam segredos que **você** precisa providenciar (certificado Authenticode e conta Apple Developer). Sem eles, os binários saem sem assinatura e com aviso.
- Criar `packaging/mcpb/manifest.json` com servidor do tipo binário e `user_config` para e-mail e pasta de trabalho, e gerar `noul.mcpb` por sistema operacional no release.
- Criar uma página "Instalar" em `site/` que detecta o sistema operacional e oferece: o conector remoto (URL), o `.mcpb`, *deeplinks* de instalação MCP para VS Code e Cursor e o comando para desenvolvedores.
- Conector remoto: deploy do `Dockerfile` atualizado em Cloud Run, somente leitura e com autenticação OAuth do MCP. A conta GCP e o provedor OAuth dependem de **você**.
- Transformar `noul-install` sem argumentos em assistente interativo que detecta os clientes instalados. Com `--all`, mantém o comportamento atual.
- **Gate:** 5 pesquisadores leigos fazem a primeira busca em menos de 5 minutos, sem terminal. Esse teste é conduzido por você, e o resultado fica registrado no PR.

## Fase 3 — Coassistente de pesquisa (PR 5, release 2.0.0)

- **Contrato evidência × julgamento.** As ferramentas analíticas (`review_paper`, `review_paper_scientific`, `audit_manuscript_draft`, `audit_submission_readiness`, `detect_failure_modes`, `verify_paper_claims`, `parse_and_draft_rebuttal`) passam a devolver um modelo comum `AnalysisResult {method: "heuristic", signals[], excerpts[] (com seção/página), checklist[], rubric, confidence}`, sem veredito. O veredito vem do LLM do cliente, guiado pela rubrica; se o cliente suportar MCP *sampling*, a ferramenta pode pedir o veredito com `use_sampling=true`. As heurísticas de regex continuam como sinais.
- **Projeto de pesquisa.** Criar `library/store.py`, um SQLite com schema versionado (migrações simples) e as tabelas `projects`, `papers`, `project_papers(status: candidate|included|excluded, reason)`, `notes`, `extractions`, `searches` e `drafts`.
  - Novas ferramentas: `project_create`, `project_list`, `project_get`, `project_add_papers`, `project_set_status`, `project_note`.
  - Novo resource `noul://project/<id>`.
  - Os dossiês atuais viram uma exportação do projeto.
- **Recuperação.** Fazer chunking por seção (JATS do PMC e seções detectadas no PDF por `pdf_parser`) e trocar `pick_chunks` por BM25 em Python puro. As citações passam a trazer seção e página.
- **Segurança de conteúdo.** Todo texto externo devolvido pelas ferramentas vem marcado com `untrusted: true` e entre delimitadores, e a SKILL instrui o agente a tratá-lo como dado.
- **Skill em camadas.** `skills/noul-search/SKILL.md` vira um roteador curto, com `references/{search,review,writing,submission}.md` e `evals/` no formato do skill-creator (3 a 5 casos por jornada).
- Atualizar versão para 2.0.0, escrever o CHANGELOG e remover os *shims* da raiz.
- **Gate:** as evals das quatro jornadas superam a v1.0 (usada como baseline, fixada pela tag `v1.0.0`).

## Fase 4 — Ecossistema (PR 6, 2.1.0)

- Exportação CSL-JSON e Zotero (API web, chave opcional).
- Detecção de retratações via Crossref `update-to` e ligação entre preprint e versão publicada.
- UI própria ou extensão do VS Code: decisão adiada, só se o MCP não cobrir a necessidade.

## Arquivos críticos

`noul_core.py`, `noul_tools.py`, `noul_mcp.py`, `noul_cli.py`, `install.py`, `noul/core.py`, `noul/tools/*.py`, `noul/schemas/_compat.py`, `pyproject.toml`, `scripts/build_site.py`, `skills/noul-search/SKILL.md`, `agents/*.md`, `.claude-plugin/plugin.json` (o `mcpServers` passa a usar `noul-mcp`/`uvx`), `Dockerfile`, `.github/workflows/ci.yml`, `tests/_support.py`.

## Verificação

- Em cada PR: `ruff check . && ruff format --check . && mypy src && pytest --cov=noul` e `python scripts/build_site.py --check`.
- **Paridade (Fases 1 e 2a):** um teste que, para cada ferramenta, compara a saída via CLI (`noul <cmd> --json`) e via cliente MCP em memória, com rede simulada.
- **MCP real:** `npx @modelcontextprotocol/inspector noul-mcp` para listar tools, prompts e resources e conferir annotations e outputSchema. Depois, `noul-mcp --http 8765` com `curl` para testar 401 sem token, 403 com Host inválido e ferramenta de escrita bloqueada.
- **Fim a fim no Claude Code:** reinstalar o plugin a partir do branch e rodar a skill `noul-search` com "creatina e desempenho cognitivo", verificando busca, Evidence Meter, dossiê/projeto e download.
- **Instalação (Fase 2b):** testar o `.mcpb` em Windows e macOS limpos e o conector remoto adicionado no Claude.ai, mais o teste com 5 usuários leigos.
- **Regressão das heurísticas:** o golden set no CI não pode cair abaixo da linha de base.

## Dependências externas (precisam de você)

- Proteção do branch `master` no GitHub.
- Certificados de assinatura (Windows e Apple).
- Projeto GCP e provedor OAuth para o conector remoto.
- Recrutar os 5 testadores leigos.
- Rótulos do golden set: eu gero uma proposta e você revisa amostras.
