# noul-search-agent

Agente de pesquisa científica multidisciplinar com classificação de nível de evidência, **Evidence Meter (Noul Meter)** determinístico, descoberta de artigos similares/citados, download de PDFs Open Access e dossiês de pesquisa em Markdown. Funciona como **skill + subagente + servidor MCP + servidor HTTP/OpenAPI + CLI** para **Antigravity**, **Claude Code**, **Claude Desktop**, **GitHub Copilot**, **Codex CLI**, **Gemini CLI** e **ChatGPT Actions**.

Zero chaves de API obrigatórias e zero dependências externas (usa apenas a biblioteca padrão do Python 3.10+; `pypdf` é opcional para extração local de texto de PDFs): o LLM do próprio agente classifica a postura dos estudos e redige a síntese citada.

## Componentes
| Arquivo | Função |
|---|---|
| `noul_core.py` | Motor de busca (`OpenAlex`, `PubMed`, `Semantic Scholar`, `Crossref`, `arXiv`, `Europe PMC`, `Unpaywall`), deduplicação, classificação de desenho de estudo, faixas de impacto, grafo de citações, download de PDFs com proteção SSRF e configuração persistente |
| `noul_tools.py` | Ferramentas compartilhadas (`search_papers`, `get_paper`, `get_fulltext`, `query_fulltexts`, `citation_graph`, `related_papers`, `download_papers`, `save_research_dossier`, `list_research_dossiers`, `get_research_dossier`, `export_papers`, `evidence_meter`, `configure_noul`) com validação de argumentos e confinamento de caminhos ao workspace |
| `noul_mcp.py` | Servidor MCP (`stdio`) e servidor HTTP endurecido (`--http`) com MCP em `/mcp`, OpenAPI em `/openapi.json`, `/healthz` e MCP Resources (`noul://dossier/<slug>`) |
| `noul_cli.py` | CLI (`noul`) para terminal (`--pretty`) ou agentes sem MCP; também expõe `mcp` e `install` |
| `skills/noul-search/SKILL.md` | Fluxo de pesquisa e conversa sobre os artigos (Evidence Meter, tabela de estudos, síntese citada, limitações) |
| `agents/`, `commands/` | Subagentes/extensões (Claude, Copilot, Codex, Gemini) e o comando `/noul-search-config` |
| `install.py` | Instalador idempotente em nível de usuário (`noul-install`) |
| `.claude-plugin/`, `pyproject.toml`, `package.json` + `bin/noul.js`, `Dockerfile` | Distribuição: plugin do Claude Code, PyPI (`pip`/`pipx`/`uvx`), npm (`npx`) e contêiner (Cloud Run) |

## Instalação

Escolha **uma** das opções:

```bash
# Claude Code (plugin: skill + subagente + /noul-search-config + MCP)
/plugin marketplace add ernaneDomingues/noul-search-agent
/plugin install noul-search-agent@noul

# PyPI (após publicação) — instala o pacote e configura todos os agentes
pipx install noul-search-agent && noul-install --all
uvx noul-search-agent install --all          # agentes passam a iniciar o MCP via `uvx`

# npm (após publicação) — usa o Python do sistema, sem dependências
npx -y noul-search-agent install --all       # agentes passam a iniciar o MCP via `npx`

# A partir do código-fonte (ou antes da publicação: pipx install git+https://github.com/ernaneDomingues/noul-search-agent)
python3 install.py --all                     # todos os ambientes suportados
python3 install.py antigravity claude        # ou ambientes específicos
python3 install.py --all --dry-run           # apenas mostra o que faria
python3 install.py --all --uninstall         # remove tudo de forma limpa
```

O instalador usa o interpretador atual (`sys.executable`, funciona no Windows) ou `--runner uvx|npx`, grava strings TOML seguras para caminhos do Windows, cria um `.bak` antes de alterar pela primeira vez um arquivo de configuração existente e não toca em arquivos JSON corrompidos (sai com código 1). O plugin do Claude Code requer `python3` (3.10+) no `PATH`.

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
| `NOUL_APP_DIR` | Usa explicitamente o núcleo de um `noul-search-app` externo |
| `NOUL_INSTALL_RUNNER` | Runner padrão do instalador (`python`, `uvx`, `npx`) |
| `NOUL_PYTHON` | Interpretador usado pelo launcher `npx` |

Opcional: `pip install "noul-search-agent[pdf]"` (ou `pip install pypdf`) habilita a extração local de texto dos PDFs.

## Exemplos de Uso (CLI e Conversa)

Os exemplos usam `noul` (instalado via pip/pipx). Com uvx, troque por `uvx noul-search-agent`; a partir do código-fonte, troque por `python3 noul_cli.py`.

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
noul-mcp --http 8765          # ou: python3 noul_mcp.py --http 8765
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
Testes (offline): `python3 -m unittest discover -s tests -p "test_*.py" -v`. Pacotes: `uv build` (wheel/sdist) e `npm pack`.

Roadmap de melhorias e de distribuição: [`docs/ROADMAP.md`](docs/ROADMAP.md) · plano de implementação: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Limitações e Aviso Legal
- O **Evidence Meter (Noul Meter)** é um indicador heurístico da distribuição de estudos encontrados, não uma metanálise estatística.
- Resumos de literatura na área da saúde têm finalidade exclusivamente informativa e acadêmica, não constituindo aconselhamento médico.
- Projeto independente inspirado em princípios de Medicina Baseada em Evidências e ferramentas acadêmicas como Consensus e Elicit, sem qualquer afiliação ou endosso da Consensus NLP Inc.
