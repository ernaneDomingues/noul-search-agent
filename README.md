# noul-search-agent

Agente autônomo de pesquisa científica, inteligência documental e copiloto de escrita acadêmica (*Co-Scientist*) multidisciplinar com classificação de nível de evidência, **Evidence Meter (Noul Meter)** determinístico, extração profunda de tabelas/equações/VRAM/ablação/prompts, revisão crítica *multi-persona* adaptável por domínio, geração poliglota de código e protocolos (`PyTorch`, `Statsmodels`, `R`, `CONSORT/STROBE`), copiloto de autoria e submissão (`Citation Infiller`, `Related Work Synthesizer`, `IMRaD Blueprint & Auditor`, `BibTeX Sanitizer`, `Rebuttal Copilot` e `Submission Gatekeeper`), download de PDFs Open Access e dossiês de pesquisa em Markdown. Funciona como **skill + subagente + servidor MCP (41 tools) + servidor HTTP/OpenAPI + CLI** para **Antigravity**, **Claude Code**, **Claude Desktop**, **GitHub Copilot**, **Codex CLI**, **Gemini CLI** e **ChatGPT Actions**.

- **Portal & Documentação Pública**: [https://ernanedomingues.github.io/noul-search-agent/](https://ernanedomingues.github.io/noul-search-agent/)

Zero chaves de API obrigatórias e nenhum LLM próprio: o LLM do agente que você já usa classifica a postura dos estudos e redige a síntese citada. Requer Python 3.10+ com `pydantic` e `pypdf`; o caminho recomendado é o [`uv`](https://docs.astral.sh/uv/), que instala o Python e as dependências automaticamente.

## Componentes
| Arquivo | Função |
|---|---|
| `src/noul/search/`, `src/noul/providers/` | Motor de busca (`OpenAlex`, `PubMed`, `Semantic Scholar`, `Crossref`, `arXiv`, `Europe PMC`, `Unpaywall`), um módulo por fonte, deduplicação e ranqueamento; classificador de domínio, radar de artefatos, parser de PDF/tabelas e cliente temporal do arXiv |
| `src/noul/evidence/`, `src/noul/library/`, `src/noul/net/` | Classificação de desenho de estudo e faixas de impacto; texto completo, grafo de citações, downloads e exportação; HTTP com proteção SSRF e cache SQLite |
| `src/noul/api.py` | **41 ferramentas MCP compartilhadas** (registro `TOOLS` e `call()`) usadas pelo MCP, pelo HTTP/OpenAPI e pela CLI |
| `src/noul/tools/`, `src/noul/schemas/`, `src/noul/prompts/` | Ferramentas das Fases 1 a 6, contratos Pydantic v2 e rubricas (`NeurIPS/ICLR`, `CONSORT/STROBE/PRISMA`, `Econometria Causal`) |
| `src/noul/server/legacy.py` | Servidor MCP (`stdio`) e servidor HTTP endurecido (`--http`) com MCP em `/mcp`, OpenAPI em `/openapi.json`, `/healthz` e MCP Resources (`noul://dossier/<slug>`) |
| `src/noul/cli/` | CLI (`noul`, ou `python -m noul`) para terminal (`--pretty`, `search --interactive`) ou agentes sem MCP; também expõe `mcp` e `install` |
| `src/noul/install.py` | Instalador idempotente em nível de usuário (`noul-install`) |
| `noul_core.py`, `noul_tools.py`, `noul_mcp.py`, `noul_cli.py`, `install.py` | *Shims* de compatibilidade (obsoletos; removidos na 2.0.0) |
| `site/` & `scripts/build_site.py` | Landing Page responsiva (Modo Claro/Escuro), simulador de terminal interativo e catálogo sincronizado das 41 ferramentas MCP publicado via GitHub Pages (`.github/workflows/deploy-pages.yml`) |
| `skills/noul-search/SKILL.md` | Fluxo de pesquisa, auditoria técnica, revisão por pares, autoria científica e conversa sobre os artigos |
| `agents/`, `commands/` | Subagentes/extensões (Claude, Copilot, Codex, Gemini) e o comando `/noul-search-config` |
| `.claude-plugin/`, `plugin.json`, `pyproject.toml`, `package.json` + `bin/noul.js`, `Dockerfile` | Distribuição: plugin do Claude Code / Antigravity, PyPI (`pip`/`pipx`/`uvx`), npm (`npx`) e contêiner (Cloud Run) |

## Instalação

Escolha **uma** das opções:

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
| `NOUL_WORKSPACE` | Workspace onde downloads/dossiês podem ser gravados |
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

### 7. Contêiner / Google Cloud Run
```bash
docker build -t noul-search-agent .
docker run -p 8080:8080 -e NOUL_HTTP_TOKEN=troque-me -e NOUL_CORS_ORIGINS=https://chatgpt.com noul-search-agent
gcloud run deploy noul-search --source . --set-secrets NOUL_HTTP_TOKEN=noul-token:latest
```

## Desenvolvimento
Ambiente: `uv venv .venv && uv pip install -e ".[dev]"`. Qualidade: `ruff check .`, `ruff format --check .`, `mypy` e `python -m pytest --cov` (testes offline, com piso de cobertura). Golden sets dos classificadores: `python tests/test_golden_sets.py --report`. Pacotes: `uv build` (wheel/sdist) e `npm pack`. Orientações para agentes de código: [`CLAUDE.md`](CLAUDE.md); plano da v2.0: [`docs/REFACTOR_PLAN.md`](docs/REFACTOR_PLAN.md).

Roadmap de melhorias e de distribuição: [`docs/ROADMAP.md`](docs/ROADMAP.md) · plano de implementação: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Limitações e Aviso Legal
- O **Evidence Meter (Noul Meter)** é um indicador heurístico da distribuição de estudos encontrados, não uma metanálise estatística.
- Resumos de literatura na área da saúde têm finalidade exclusivamente informativa e acadêmica, não constituindo aconselhamento médico.
- Projeto independente inspirado em princípios de Medicina Baseada em Evidências e ferramentas acadêmicas como Consensus e Elicit, sem qualquer afiliação ou endosso da Consensus NLP Inc.
