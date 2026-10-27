# Plano de Implementação Detalhado (Guia de Execução para o GitHub Copilot)

> **Instruções para o Agente Executor (GitHub Copilot):**
> Execute as etapas abaixo na ordem **Etapa 1 → Etapa 6**.
> Ao final de cada etapa, rode `python3 -m unittest discover -s tests -p "test_*.py" -v` e garanta que **100% dos testes passem** antes de avançar para a próxima etapa. Preserve o princípio de **zero dependências obrigatórias** no core (apenas stdlib do Python 3.10+).

---

## Etapa 1 — Correções de Precisão Científica, Rede e Cache em `noul_core.py` (P0-2, P0-3, P0-4, P0-5, P0-8, P0-9, P0-10, P0-11, P0-14)

### 1.1. Variáveis de Ambiente e Configuração Persistente (`config.json`) — [P0-5, P0-11]
- **Problema atual**: `DB_PATH` e `UNPAYWALL_EMAIL` são genéricas; `_email()` retorna `"research@noul.local"` (falso, rejeitado pelo OpenAlex).
- **O que implementar em `noul_core.py`**:
  1. Definir `CACHE_DIR`:
     ```python
     def _cache_dir() -> Path:
         d = Path(os.environ.get("NOUL_HOME") or Path.home() / ".cache" / "noul-search-agent")
         d.mkdir(parents=True, exist_ok=True)
         return d
     ```
  2. Definir `_db_path()` lendo `os.environ.get("NOUL_DB_PATH") or os.environ.get("DB_PATH")` com fallback para `str(_cache_dir() / "cache.db")`.
  3. Criar gerenciamento de configuração persistente em `_cache_dir() / "config.json"`:
     ```python
     def get_config() -> dict:
         cfg_file = _cache_dir() / "config.json"
         cfg = {}
         if cfg_file.exists():
             try:
                 cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
             except Exception:
                 cfg = {}
         email, source = _resolve_email(cfg)
         workspace = cfg.get("workspace") or os.environ.get("NOUL_WORKSPACE") or os.getcwd()
         return {
             "email": email,
             "email_source": source,
             "unpaywall_enabled": bool(email),
             "workspace": str(Path(workspace).expanduser().resolve()),
             "download_dir": cfg.get("download_dir") or "./papers",
             "research_dir": cfg.get("research_dir") or "./research",
         }

     def set_config(email=None, workspace=None, download_dir=None, research_dir=None, clear=False) -> dict:
         cfg_file = _cache_dir() / "config.json"
         if clear:
             if cfg_file.exists():
                 cfg_file.unlink()
             return get_config()
         cfg = {}
         if cfg_file.exists():
             try:
                 cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
             except Exception:
                 cfg = {}
         if email is not None:
             cleaned = re.sub(r"^email:\s*", "", str(email).strip(), flags=re.I).strip()
             if cleaned and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned):
                 raise HTTPError(400, f"Invalid email address: {cleaned}")
             cfg["email"] = cleaned or None
         if workspace is not None:
             cfg["workspace"] = str(workspace).strip()
         if download_dir is not None:
             cfg["download_dir"] = str(download_dir).strip()
         if research_dir is not None:
             cfg["research_dir"] = str(research_dir).strip()
         cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
         return get_config()
     ```
  4. Implementar `_resolve_email(cfg=None)` que retorna `(email: str | None, source: str)`:
     - 1º: `cfg.get("email")` → `(email, "config")`
     - 2º: `os.environ.get("NOUL_EMAIL") or os.environ.get("UNPAYWALL_EMAIL")` (se contiver `@` e não terminar em `.local`) → `(email, "env")`
     - 3º: Se nenhuma variável/config existir,retornar `(None, "none")`. **Nunca** retornar `research@noul.local`.
  5. Atualizar `UA` e chamadas ao OpenAlex/Crossref:
     - `UA = "noul-search-agent/1.1.0 (+https://github.com/ernanedomingues/noul-search-agent)"` (se `_email()` existir, anexar `; mailto:{email}`).
     - Nas queries do OpenAlex e Crossref, **só incluir o parâmetro `mailto` se `_email()` retornar um e-mail real** (`if _email(): params["mailto"] = _email()`).

### 1.2. Fechamento Explícito do SQLite (`cache_get` / `cache_set`) — [P0-8]
- **Problema atual**: `with sqlite3.connect(...) as c:` apenas faz commit/rollback da transação, mas **não fecha** o file descriptor da conexão.
- **Correção**: Usar `contextlib.closing(sqlite3.connect(_db_path(), timeout=15))` em `_conn()`:
  ```python
  from contextlib import contextmanager

  @contextmanager
  def _conn():
      conn = sqlite3.connect(_db_path(), timeout=15)
      try:
          conn.execute("PRAGMA journal_mode=WAL")
          conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)")
          yield conn
          conn.commit()
      finally:
          conn.close()
  ```

### 1.3. Regex Estrita de DOI (Corrigir Bug de IDs arXiv de Outubro `2410.12345`) — [P0-2]
- **Problema atual**: `"10." in ident` casa com `2410.12345` e `arxiv:2410.12345`, tratando artigos arXiv de outubro como DOI.
- **Correção**:
  ```python
  DOI_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)$", re.I)

  def _norm_doi(doi):
      if not doi:
          return None
      m = DOI_RE.match(str(doi).strip())
      return m.group(1).lower() if m else None
  ```
  - Em `resolve_paper(identifier)`: testar `if ident.lower().startswith("arxiv:") or re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", ident):` **antes** de testar DOI, e usar `_norm_doi(ident)` em vez de `"10." in ident`.

### 1.4. Corrigir Classificação de Periódicos/Impacto (Sem Falso "Q1") — [P0-3]
- **Problema atual**: `any(tv in v for tv in top_venues)` faz `"science"` casar com `"Journal of Food Science"` e `"cell"` casar com `"Stem Cells International"`, além de rotular como `"Q1"`.
- **Correção**:
  - Usar um `set` de nomes completos normalizados (`EXACT_ELITE_VENUES`) comparados por igualdade exata (`v_norm in EXACT_ELITE_VENUES`), nunca por substring curta (`"cell"`, `"science"`, `"nature"`).
  - Substituir os rótulos `"Q1"`, `"Q1/Q2"`, `"Q2/Q3"`, `"Q3/Q4"` por bandas transparentes baseadas em citação/periódico: `"top"`, `"high"`, `"standard"`, `"emerging"`.

### 1.5. Refinar Classificador de Desenho de Estudo (`classify_evidence`) — [P0-4]
- **Problema atual**: Qualquer menção a `"meta-analyses"` (ex.: *"future meta-analyses are needed"*) vira `meta-analysis`; `"non-randomized clinical trial"` vira `rct`.
- **Correção em `classify_evidence(title="", abstract="", pub_types=None)`**:
  1. **Checar `pub_types` estruturados primeiro** (vindos do PubMed `PublicationType` ou OpenAlex/S2):
     - `"meta-analysis"` em `pub_types` → `"meta-analysis"`
     - `"systematic review"` em `pub_types` → `"systematic-review"`
     - `"randomized controlled trial"` em `pub_types` → `"rct"`
  2. **Remover contextos negativos do texto antes de testar regexes positivas**:
     ```python
     NEG_PATTERNS = re.compile(
         r"\b(?:non[-\s]?randomi[sz]ed|not\s+randomi[sz]ed|unrandomi[sz]ed|"
         r"future\s+(?:meta[-\s]?analys\w+|systematic\s+reviews?|rcts?|randomized\s+trials?)|"
         r"need\s+for\s+(?:meta[-\s]?analys\w+|randomized\s+trials?)|"
         r"no\s+(?:meta[-\s]?analys\w+|randomized\s+trials?)|"
         r"lack\s+of\s+(?:meta[-\s]?analys\w+|randomized\s+trials?)|"
         r"protocol\s+for\s+a\s+(?:systematic\s+review|randomized))\b",
         re.I,
     )
     ```
     Substituir matches de `NEG_PATTERNS` por `" "` antes de rodar as regexes de `meta-analysis`, `systematic-review` e `rct`.
  3. **Priorizar o `title`** sobre o `abstract`: se o `title` indicar claramente `"cross-sectional"`, `"cohort"`, `"case-control"`, `"in vitro"`, `"mice"`, não permitir que uma menção secundária no abstract sobrescreva para `meta-analysis` ou `rct`.

### 1.6. Timeout Real no `ThreadPoolExecutor` de `search()` — [P0-9]
- **Problema atual**: `with ThreadPoolExecutor(...) as ex:` chama `ex.shutdown(wait=True)` ao sair do bloco `with`, bloqueando na thread mais lenta mesmo após timeout.
- **Correção**:
  ```python
  ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen))
  futs = {ex.submit(_run_source, s): s for s in chosen}
  done, not_done = concurrent.futures.wait(futs.keys(), timeout=18)
  for fut in done:
      src = futs[fut]
      try:
          raw_papers.extend(fut.result())
          active_sources.append(src)
      except Exception as e:
          warnings.append(f"{src}: {type(e).__name__}: {e}")
  for fut in not_done:
      src = futs[fut]
      fut.cancel()
      warnings.append(f"{src}: TimeoutError: source exceeded 18s limit")
  ex.shutdown(wait=False, cancel_futures=True)
  ```

### 1.7. Proteção SSRF, Limite de Tamanho de Download e Limpeza de HTML em `fulltext` / `download` — [P0-10]
- **O que implementar em `noul_core.py`**:
  1. **`_validate_public_url(url)`**:
     - Aceitar apenas esquemas `http` e `https`.
     - Resolver o hostname via `socket.getaddrinfo` e bloquear qualquer IP que seja `is_private`, `is_loopback`, `is_link_local`, `is_multicast`, `is_reserved` (via módulo stdlib `ipaddress`).
  2. **Limite de bytes em `_http_get(..., max_bytes=50 * 1024 * 1024)`**:
     - Ler em blocos de `64 KB` (`resp.read(65536)`) abortando se ultrapassar `max_bytes`.
  3. **Limpeza de HTML e detecção de paywall em `fulltext()`**:
     - Se a resposta não começar com `%PDF`, antes de aceitar como texto completo, remover blocos `<script>...</script>`, `<style>...</style>`, `<nav>...</nav>`, `<header>...</header>`, `<footer>...</footer>`.
     - Rejeitar se o texto limpo tiver menos de `2500` caracteres (páginas de login/landing page de periódicos costumam ter < 2500 chars úteis).

### 1.8. Adicionar `europepmc` como Fonte de Busca Real em `SOURCES` — [P0-14]
- Adicionar `"europepmc"` em `SOURCES = ("openalex", "pubmed", "s2", "crossref", "arxiv", "europepmc")`.
- Implementar `_search_europepmc(query, limit=20, year_min=None, open_access=False)` consultando `https://www.ebi.ac.uk/europepmc/webservices/rest/search` (`resultType=core&format=json`) e convertendo cada item para o dicionário padrão de artigo.

---

## Etapa 2 — Confinamento de Workspace, Opt-in de App Externo e Novas Tools em `noul_tools.py` (P0-1, P0-6, P0-13 + `/noul-search-config` + `evidence_meter`)

### 2.1. `NOUL_APP_DIR` Apenas quando Explícito — [P0-6]
- Em `noul_tools.py`, substituir a busca implícita em `HERE.parent / "noul-search-app"` por:
  ```python
  _explicit_app_dir = os.environ.get("NOUL_APP_DIR")
  if _explicit_app_dir and (Path(_explicit_app_dir) / "noul" / "core.py").exists():
      sys.path.insert(0, _explicit_app_dir)
      from noul import core as c
  else:
      import noul_core as c
  ```
- Não usar `os.environ.setdefault("DB_PATH", ...)` nem `os.environ.setdefault("UNPAYWALL_EMAIL", ...)`.

### 2.2. Confinamento de Caminhos (`_safe_output_dir`) — [P0-1, P0-13]
- Criar em `noul_tools.py` a função `_safe_output_dir(requested_dir, default_subdir)`:
  ```python
  def _safe_output_dir(requested_dir=None, default_subdir="papers") -> Path:
      cfg = c.get_config()
      workspace = Path(cfg["workspace"]).resolve()
      raw = requested_dir or cfg.get(f"{default_subdir}_dir") or f"./{default_subdir}"
      p = Path(raw).expanduser()
      target = (workspace / p).resolve() if not p.is_absolute() else p.resolve()
      # Garantir que target esteja dentro de workspace (ou NOUL_ALLOW_ANY_PATH=1 apenas para testes unitários específicos)
      if os.environ.get("NOUL_ALLOW_ANY_PATH") != "1":
          try:
              target.relative_to(workspace)
          except ValueError:
              raise c.HTTPError(403, f"Path '{target}' is outside the allowed workspace '{workspace}'")
      return target
  ```
- Usar `_safe_output_dir` em `download_papers`, `save_research_dossier`, `list_research_dossiers` e `get_research_dossier`.

### 2.3. Nova Tool `configure_noul` (Suporte ao `/noul-search-config`)
- Adicionar em `noul_tools.py`:
  ```python
  def configure_noul(email=None, workspace=None, download_dir=None, research_dir=None, clear=False):
      """View or update persistent Noul configuration (email for Unpaywall/Polite Pool, workspace, default folders)."""
      return c.set_config(email=email, workspace=workspace, download_dir=download_dir, research_dir=research_dir, clear=bool(clear))
  ```
- Registrar `"configure_noul"` no dicionário `TOOLS`.

### 2.4. Nova Tool Determinística `evidence_meter` (P2 do Roadmap)
- Adicionar em `noul_tools.py`:
  ```python
  def evidence_meter(items):
      """Compute reproducible raw and design-weighted Evidence Meter (Noul Meter) percentages from LLM stance classifications.
      Each item in `items`: {"id": str, "stance": "supportive"|"inconclusive"|"contrary"}."""
      if not items:
          raise c.HTTPError(400, "Provide at least one classified paper in `items`")
      raw_counts = {"supportive": 0, "inconclusive": 0, "contrary": 0}
      weighted = {"supportive": 0.0, "inconclusive": 0.0, "contrary": 0.0}
      breakdown = []
      for it in items:
          stance = (it.get("stance") or "").lower().strip()
          # Map aliases
          if stance in ("yes", "positive", "favoravel", "favorável"):
              stance = "supportive"
          elif stance in ("no", "negative", "contrario", "contrário"):
              stance = "contrary"
          elif stance in ("possibly", "mixed", "neutral", "inconclusivo"):
              stance = "inconclusive"
          if stance not in raw_counts:
              continue
          pid = it.get("id")
          p = c.cache_get("paper:" + str(pid)) if pid else None
          ev = (p or {}).get("evidence") or it.get("evidence") or "other"
          w = float(c.EV_W.get(ev, 0.3))
          raw_counts[stance] += 1
          weighted[stance] += w
          breakdown.append({"id": pid, "stance": stance, "evidence": ev, "weight": w})
      total_n = sum(raw_counts.values())
      total_w = sum(weighted.values()) or 1.0
      if total_n == 0:
          raise c.HTTPError(400, "No valid stances provided")
      return {
          "total_papers": total_n,
          "raw_counts": raw_counts,
          "raw_percentages": {k: round(v * 100.0 / total_n, 1) for k, v in raw_counts.items()},
          "weighted_percentages": {k: round(v * 100.0 / total_w, 1) for k, v in weighted.items()},
          "breakdown": breakdown,
      }
  ```
- Registrar `"evidence_meter"` em `TOOLS`.

### 2.5. Validação de Schema em `noul_tools.call(name, args)` — [P0-12]
- Antes de invocar `TOOLS[name][3](**args)`, validar:
  1. Se `args` é um `dict`;
  2. Se todas as chaves listadas em `required` estão presentes e não são `None`;
  3. Se todas as chaves passadas pertencem a `properties` (rejeitando parâmetros desconhecidos com `HTTPError(400, ...)`).

---

## Etapa 3 — Segurança HTTP, Conformidade MCP e Suporte a GCP Cloud Run em `noul_mcp.py` (P0-1, P0-12)

### 3.1. Negociação de `protocolVersion` no MCP — [P0-12]
- Em `noul_mcp.py`:
  ```python
  SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
  ```
  - No handler de `initialize`: se o `protocolVersion` solicitado pelo cliente estiver em `SUPPORTED_PROTOCOLS`, usar o solicitado; caso contrário, responder com `SUPPORTED_PROTOCOLS[0]`.

### 3.2. Blindagem de Segurança do Servidor HTTP (`serve_http`) e Cloud Run — [P0-1]
- **Alterações obrigatórias em `noul_mcp.py`**:
  1. Usar `ThreadingHTTPServer` (de `http.server`) em vez de `HTTPServer`.
  2. Configurar host e porta para funcionar tanto localmente (loopback seguro por padrão) quanto no Cloud Run:
     - Se `--host` for passado ou `os.environ.get("K_SERVICE")` / `os.environ.get("NOUL_HTTP_HOST")` existir, usar esse host (`0.0.0.0` no Cloud Run; `127.0.0.1` por padrão no desktop local).
     - Porta padrão: `int(os.environ.get("PORT", 8765))`.
  3. **Remover `Access-Control-Allow-Origin: *`**:
     - Só enviar cabeçalhos CORS se a origem da requisição (`Origin`) estiver explicitamente listada em `os.environ.get("NOUL_CORS_ORIGINS", "")`.
     - Se uma requisição trouxer cabeçalho `Origin` que **não** está na allowlist `NOUL_CORS_ORIGINS`, bloquear imediatamente com `403 Forbidden` (impede ataques CSRF/drive-by de sites abertos no navegador!).
  4. **Proteção contra DNS Rebinding (Validação do Header `Host`)**:
     - Quando rodando em `127.0.0.1` / `localhost`, validar que o header `Host` seja exclusivamente `127.0.0.1:<port>` ou `localhost:<port>`. Caso contrário, retornar `403 Forbidden`.
  5. **Autenticação Bearer (`NOUL_HTTP_TOKEN`)**:
     - Se `os.environ.get("NOUL_HTTP_TOKEN")` estiver definido, exigir `Authorization: Bearer <token>` em todos os endpoints (exceto `GET /healthz`).
  6. **Modo Somente-Leitura por Padrão via HTTP (`WRITE_TOOLS`)**:
     - Ferramentas que gravam no disco ou alteram configuração (`download_papers`, `save_research_dossier`, `configure_noul`) ficam **bloqueadas por padrão no servidor HTTP**, a menos que `os.environ.get("NOUL_HTTP_ALLOW_WRITE") == "1"` **e** um `NOUL_HTTP_TOKEN` válido tenha sido configurado.
  7. **Status `202 Accepted` para notificações MCP em `POST /mcp`**:
     - Se `handle(body)` retornar `None` (notificação JSON-RPC sem `id`), responder com HTTP `202` e corpo vazio.
  8. **Endpoints Cloud Run (`GET /healthz` e `GET /openapi.json` dinâmico)**:
     - `GET /healthz` → `200 OK` `{"status": "ok", "version": "1.1.0"}`.
     - `GET /openapi.json` → detectar `proto = self.headers.get("X-Forwarded-Proto", "http")` e `host = self.headers.get("Host", f"localhost:{port}")` para preencher `servers: [{"url": f"{proto}://{host}"}]`.

---

## Etapa 4 — Instalador Multiplataforma (`install.py`) e CLI (`noul_cli.py`) (P0-7)

### 4.1. Correções do `install.py` para Windows, Backups e Runners (`uvx`/`npx`) — [P0-7]
- Em `install.py`:
  1. Usar `sys.executable` como comando Python padrão (nunca `"python3"` fixo, que falha no Windows), ou permitir `--runner uvx` (`command = "uvx"`, `args = ["noul-search-agent", "mcp"]`) e `--runner npx` (`command = "npx"`, `args = ["-y", "noul-search-agent", "mcp"]`).
  2. No TOML do Codex (`~/.codex/config.toml`), usar **strings literais TOML com aspas simples** (`'...'`) para que barras invertidas do Windows (`C:\Users\...`) não gerem sequências de escape inválidas:
     ```python
     toml = f"[mcp_servers.noul]\ncommand = '{cmd}'\nargs = [{', '.join(repr(a) for a in args)}]"
     ```
  3. Antes de sobrescrever qualquer arquivo de configuração existente (`.claude.json`, `claude_desktop_config.json`, `config.toml`, `mcp_config.json`), criar uma cópia `.bak` se ainda não existir.
  4. Em `json_mcp(path, entry)`: tratar `json.JSONDecodeError` com mensagem clara indicando o arquivo corrompido, sem apagar o arquivo do usuário.
  5. Instalar também o comando `/noul-search-config` em `~/.claude/commands/noul-search-config.md`.
  6. Encapsular a lógica do script em uma função `main(argv=None)` para que possa ser chamada como entrypoint do `pyproject.toml` (`noul-install`).

### 4.2. Subcomandos `config` e `meter` em `noul_cli.py`
- Adicionar em `noul_cli.py`:
  - `noul_cli.py config [email:voce@exemplo.com] [--email E] [--workspace W] [--download-dir D] [--research-dir R] [--clear]`
  - `noul_cli.py meter '[{"id":"...","stance":"supportive"}]'`

---

## Etapa 5 — Empacotamento (`pyproject.toml`, `package.json`, `bin/noul.js`, `.claude-plugin/plugin.json`, `Dockerfile`)

1. **Criar `pyproject.toml`**:
   - Nome: `noul-search-agent`, versão `1.1.0`, `requires-python = ">=3.10"`, `dependencies = []`.
   - `optional-dependencies`: `pdf = ["pypdf>=4.0"]`.
   - `py-modules = ["noul_core", "noul_tools", "noul_mcp", "noul_cli", "install"]`.
   - `[project.scripts]`: `noul = "noul_cli:main"`, `noul-mcp = "noul_mcp:main"`, `noul-install = "install:main"`.
2. **Criar `package.json` + `bin/noul.js`**:
   - Permitir execução via `npx noul-search-agent` (`mcp`, `install`, `search`, `config`, etc.) detectando `python3` / `python` no sistema.
3. **Criar `.claude-plugin/plugin.json` e `commands/noul-search-config.md`**:
   - Manifesto de plugin e comando `/noul-search-config`.
4. **Criar `Dockerfile` e `.dockerignore`**:
   - Base `python:3.12-slim`, instalando `pypdf`, configurando `NOUL_HOME=/tmp/noul-cache`, `PORT=8080` e expondo o modo HTTP seguro para o Google Cloud Run.

---

## Etapa 6 — Suíte de Testes de Regressão Offline (`tests/test_agent.py`)

Adicionar testes unitários 100% offline em `tests/test_agent.py` cobrindo todos os itens P0:
1. `test_p0_1_http_security_cors_and_write_block`:
   - Iniciar `noul_mcp.py` HTTP ou testar o handler verificando que:
     - Requisição com `Origin: https://evil.example` recebe `403`;
     - `POST /tools/save_research_dossier` e `POST /tools/download_papers` via HTTP sem `NOUL_HTTP_ALLOW_WRITE=1` são bloqueados;
     - `output_dir="/tmp/outside"` fora de `NOUL_WORKSPACE` lança erro `403`.
2. `test_p0_2_october_arxiv_id_not_confused_with_doi`:
   - Verificar que `_norm_doi("2410.12345")` e `_norm_doi("arxiv:2410.12345")` retornam `None`, enquanto `_norm_doi("10.1038/s41586-020-2649-2")` retorna `"10.1038/s41586-020-2649-2"`.
3. `test_p0_3_journal_impact_no_false_q1`:
   - Verificar que `"Journal of Food Science"`, `"Procedia Computer Science"` e `"Stem Cells International"` com 0 citações **não** recebem `"top"` nem `"Q1"`.
4. `test_p0_4_classifier_negative_contexts`:
   - Verificar que `"A cross-sectional study. Future meta-analyses are needed."` é classificado como `"cohort"` (e **não** `"meta-analysis"`), e `"A non-randomized clinical trial of X"` **não** é classificado como `"rct"`.
5. `test_p0_10_ssrf_protection`:
   - Verificar que `_validate_public_url("http://127.0.0.1/secret")` e `_validate_public_url("http://169.254.169.254/latest/meta-data/")` lançam erro.
6. `test_config_and_deterministic_evidence_meter`:
   - Testar `configure_noul(email="email:user@university.edu")` e `evidence_meter(...)`.
