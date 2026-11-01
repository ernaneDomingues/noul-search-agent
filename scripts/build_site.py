#!/usr/bin/env python3
"""Deterministic static site builder and validator for Phase 6 (#28).

Reads the tool registry (`noul.registry`) and `noul.__version__`, generates `site/tools-reference.json`,
synchronizes the interactive MCP Tools Catalog inside `site/index.html`, and rewrites the generated tool lists
between `<!-- noul:tools:start -->` / `<!-- noul:tools:end -->` in the SKILL, agent templates and README.
Run with `--check` in CI to verify that `site/` artifacts are up-to-date."""

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # dev script: run from a checkout without installing

import noul  # noqa: E402
from noul import registry  # noqa: E402

SITE_DIR = ROOT / "site"
INDEX_HTML = SITE_DIR / "index.html"
TOOLS_JSON = SITE_DIR / "tools-reference.json"

CLI_MAPPING = {
    "search_papers": "noul search <query>",
    "clarify_search_scope": "noul clarify <query>",
    "get_paper": "noul paper <id>",
    "detect_paper_domain": "noul domain <id>",
    "scan_paper_artifacts": "noul artifacts <id>",
    "get_paper_references": "noul references <id>",
    "compare_papers": "noul compare <id1> <id2> ...",
    "get_thematic_insights": "noul insights <id1> <id2> ...",
    "generate_research_hypotheses": "noul hypotheses <id1> <id2> ...",
    "export_obsidian_notes": "noul export <ids> --format obsidian",
    "suggest_boolean_queries": "noul expand <query>",
    "extract_benchmark_tables": "noul benchmarks <id>",
    "estimate_hardware_specs": "noul hardware <id>",
    "verify_paper_claims": "noul ground <id>",
    "extract_paper_math": "noul math <id>",
    "analyze_ablation_tables": "noul ablation <id>",
    "extract_prompts_and_decoding": "noul prompts <id>",
    "detect_failure_modes": "noul failures <id>",
    "diff_arxiv_versions": "noul diff <arxiv_id>",
    "review_paper": "noul review <id>",
    "review_paper_scientific": "noul review <id>",
    "scaffold_implementation": "noul scaffold <id>",
    "scaffold_paper_code": "noul scaffold <id>",
    "recommend_citations": "noul cite <text_or_file>",
    "synthesize_related_work": "noul related-work <ids> --proposal ...",
    "generate_manuscript_blueprint": "noul draft structure --topic ...",
    "audit_manuscript_draft": "noul draft audit <draft>",
    "sanitize_bibtex": "noul bibtex sanitize <bib>",
    "parse_and_draft_rebuttal": "noul rebuttal parse <reviews>",
    "audit_submission_readiness": "noul gatekeeper audit <draft>",
    "get_fulltext": "noul fulltext <id>",
    "query_fulltexts": "noul ask <ids> --question ...",
    "citation_graph": "noul graph <id>",
    "related_papers": "noul related <id>",
    "download_papers": "noul download <ids>",
    "save_research_dossier": "noul dossier save --topic ...",
    "list_research_dossiers": "noul dossier list",
    "get_research_dossier": "noul dossier show <slug>",
    "export_papers": "noul export <ids>",
    "evidence_meter": "noul meter '<json>'",
    "configure_noul": "noul config",
}

DOC_START = "<!-- noul:tools:start -->"
DOC_END = "<!-- noul:tools:end -->"


def _inline_list(payload: dict) -> str:
    return ", ".join(f"`{t['name']}`" for t in payload["tools"])


def _readme_table(payload: dict) -> str:
    rows = ["", "| Ferramenta | O que faz | Perfil | Grava em disco |", "|---|---|---|---|"]
    flags = {t["name"]: t for t in registry.catalog()}

    def writes(name):
        if flags[name]["writes"]:
            return "sim"
        args = flags[name]["write_args"]
        return "só com " + ", ".join(f"`{a}`" for a in args) if args else "não"

    for t in payload["tools"]:
        first = re.split(r"(?<=[.!?])\s", t["description"], maxsplit=1)[0].replace("|", "\\|")
        profile = (
            "research (todos)"
            if "research" in flags[t["name"]]["profiles"]
            else ", ".join(flags[t["name"]]["profiles"])
        )
        rows.append(f"| `{t['name']}` | {first} | {profile} | {writes(t['name'])} |")
    return "\n".join(rows) + "\n"


# file -> renderer of the text placed between DOC_START and DOC_END
DOC_TARGETS = {
    ROOT / "skills/noul-search/SKILL.md": _inline_list,
    ROOT / "agents/AGENTS.snippet.md": _inline_list,
    ROOT / "agents/claude-noul-researcher.md": _inline_list,
    ROOT / "README.md": _readme_table,
}


def render_docs(payload: dict) -> dict:
    """{path: (current_text, expected_text)} for every generated tool list."""
    out = {}
    pattern = re.compile(re.escape(DOC_START) + r".*?" + re.escape(DOC_END), re.S)
    for path, render in DOC_TARGETS.items():
        current = path.read_text(encoding="utf-8")
        if DOC_START not in current or DOC_END not in current:
            raise SystemExit(f"error: markers {DOC_START} / {DOC_END} missing from {path.relative_to(ROOT)}")
        block = DOC_START + render(payload) + DOC_END
        out[path] = (current, pattern.sub(lambda _, b=block: b, current, count=1))
    return out


INSTALL_HTML = SITE_DIR / "install.html"
SITE_CONFIG = SITE_DIR / "config.json"


def deeplinks(connector_url):
    """One-click install links for the remote connector: VS Code (URL-encoded JSON) and Cursor (base64 config)."""
    import base64
    import urllib.parse

    vscode_cfg = json.dumps({"name": "noul", "type": "http", "url": connector_url}, separators=(",", ":"))
    cursor_cfg = base64.b64encode(json.dumps({"url": connector_url}, separators=(",", ":")).encode()).decode()
    return {
        "vscode": "vscode:mcp/install?" + urllib.parse.quote(vscode_cfg, safe=""),
        "cursor": "cursor://anysphere.cursor-deeplink/mcp/install?name=noul&config="
        + urllib.parse.quote(cursor_cfg, safe=""),
    }


def render_install_html(cfg: dict) -> str:
    """The "Instalar" page: zero-install connector first, then Claude Desktop, VS Code/Cursor, executables, devs."""
    esc = html.escape
    url = (cfg.get("connector_url") or "").strip()
    rel = cfg["release_base"].rstrip("/")
    repo = cfg["repository"].rstrip("/")
    if url:
        links = deeplinks(url)
        connector = f"""
          <div class="code-box"><div class="code-box-header"><span>URL do conector</span>
            <button class="copy-btn" type="button" data-copy-target="connector-url">Copiar</button></div>
            <pre id="connector-url" class="mono">{esc(url)}</pre></div>
          <ol>
            <li><strong>Claude.ai ou Claude Desktop:</strong> Configurações → Conectores → <em>Adicionar conector personalizado</em> → cole a URL → Adicionar.</li>
            <li><strong>ChatGPT:</strong> Settings → Apps &amp; Connectors → Advanced settings → ative o <em>Developer mode</em> → Create → cole a URL → <em>No authentication</em>.</li>
            <li>Pergunte, por exemplo: <em>"O que a literatura diz sobre creatina e memória?"</em></li>
          </ol>"""
        editors = f"""
          <p>Um clique adiciona o conector público ao editor (sem instalar nada):</p>
          <a class="btn btn-primary" href="{esc(links["vscode"])}">Adicionar ao VS Code</a>
          <a class="btn btn-secondary" href="{esc(links["cursor"])}">Adicionar ao Cursor</a>"""
    else:
        connector = """
          <p class="connector-pending">O conector público ainda não está no ar. Enquanto isso, use uma das opções abaixo.</p>"""
        editors = """
          <p class="connector-pending">Os botões de um clique aparecem aqui quando o conector público estiver no ar.</p>"""
    downloads = [
        ("windows", "Windows (x64)", "noul-windows-x64.exe"),
        ("macos", "macOS (Apple Silicon)", "noul-macos-arm64"),
        ("linux", "Linux (x64)", "noul-linux-x64"),
    ]
    exe_items = "\n".join(
        f'            <li data-os-target="{o}"><a href="{rel}/{f}">{label}</a></li>' for o, label, f in downloads
    )
    mcpb_items = "\n".join(
        f'            <li data-os-target="{o}"><a href="{rel}/noul-{p}.mcpb">{label}</a></li>'
        for (o, label, _), p in zip(downloads, ("win32", "darwin", "linux"), strict=True)
    )
    return f"""<!DOCTYPE html>
<html lang="pt-BR" data-theme="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Instalar o Noul</title>
  <meta name="description" content="Use o Noul no Claude, ChatGPT, VS Code, Cursor ou no terminal: do jeito mais simples para o seu ambiente." />
  <link rel="icon" type="image/svg+xml" href="favicon.svg" />
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <!-- Generated by scripts/build_site.py from site/config.json - do not edit by hand. -->
  <header class="site-header">
    <div class="container nav-bar">
      <a href="index.html" class="brand"><img src="favicon.svg" alt="Noul" /><span>Noul Search Agent</span></a>
      <button id="theme-toggle" class="theme-toggle" type="button" aria-label="Alternar tema">☀️ Modo Claro</button>
    </div>
  </header>
  <main>
    <section class="section">
      <div class="container">
        <h1 class="section-title">Instalar o Noul</h1>
        <p class="section-lead">Escolha onde você já conversa com uma IA. O caminho mais simples vem primeiro; nenhum exige terminal.
          <span class="install-note">Sistema detectado: <span id="detected-os">—</span>.</span></p>
        <div class="install-grid">
          <article class="card install-card" id="connector">
            <span class="card-tag">Recomendado • sem instalar nada</span>
            <h3>Claude.ai, Claude Desktop ou ChatGPT</h3>
            <p>Cole o endereço do conector público (busca e leitura de artigos, só leitura).</p>{connector}
          </article>
          <article class="card install-card" id="claude-desktop">
            <span class="card-tag">Claude Desktop • completo</span>
            <h3>Extensão para o Claude Desktop</h3>
            <p>Inclui downloads de PDFs, dossiês, revisão e escrita. Baixe e dê <strong>dois cliques</strong>; o Claude Desktop pede só o seu e-mail (opcional) e a pasta do Noul.</p>
            <a class="btn btn-primary" href="{rel}/noul.mcpb">Baixar noul.mcpb</a>
            <p class="install-note">Se o Claude Desktop recusar o arquivo acima, use a versão para o seu sistema:</p>
            <ul class="download-list">
{mcpb_items}
            </ul>
          </article>
          <article class="card install-card" id="editors">
            <span class="card-tag">VS Code • Cursor</span>
            <h3>No seu editor</h3>{editors}
            <p class="install-note">Versão completa no editor: baixe o executável abaixo e rode <code>noul install</code>, que detecta os seus aplicativos.</p>
          </article>
          <article class="card install-card" id="executables">
            <span class="card-tag">Executável • sem Python</span>
            <h3>Programa único</h3>
            <p>Para usar no terminal ou instalar em vários aplicativos de uma vez (<code>noul install</code>).</p>
            <ul class="download-list">
{exe_items}
            </ul>
            <p class="install-note"><strong>Windows:</strong> se aparecer "O Windows protegeu o computador", clique em <em>Mais informações → Executar assim mesmo</em>.
              <strong>macOS:</strong> clique com o botão direito → <em>Abrir</em> (ou Ajustes → Privacidade e Segurança → <em>Abrir Mesmo Assim</em>).
              Os arquivos ainda não são assinados digitalmente; confira-os com o <a href="{rel}/SHA256SUMS">SHA256SUMS</a>.</p>
          </article>
          <article class="card install-card" id="developers">
            <span class="card-tag">Desenvolvedores</span>
            <h3>Plugin, uv ou código-fonte</h3>
            <div class="code-box"><div class="code-box-header"><span>Claude Code</span>
              <button class="copy-btn" type="button" data-copy-target="cmd-plugin">Copiar</button></div>
              <pre id="cmd-plugin" class="mono">/plugin marketplace add ernaneDomingues/noul-search-agent
/plugin install noul-search-agent@noul</pre></div>
            <div class="code-box"><div class="code-box-header"><span>Todos os agentes (requer uv)</span>
              <button class="copy-btn" type="button" data-copy-target="cmd-uv">Copiar</button></div>
              <pre id="cmd-uv" class="mono">git clone {esc(repo)} &amp;&amp; cd noul-search-agent
uv run noul-install</pre></div>
          </article>
        </div>
      </div>
    </section>
  </main>
  <footer class="site-footer"><div class="container"><p><strong>Noul Search Agent</strong> • Licença Apache-2.0 • <a href="{esc(repo)}">Código-fonte</a></p></div></footer>
  <script src="app.js"></script>
  <script src="install.js"></script>
</body>
</html>
"""


START_MARKER = "<!-- TOOLS_CATALOG_START -->"
END_MARKER = "<!-- TOOLS_CATALOG_END -->"


def build_tools_payload() -> dict:
    tools_list = []
    for item in registry.catalog():
        name, desc = item["name"], item["description"]
        props, req = item["inputSchema"]["properties"], item["inputSchema"]["required"]
        tools_list.append(
            {
                "name": name,
                "description": desc,
                "required": list(req),
                "Optional": [k for k in props if k not in req],
                "cli_equivalent": CLI_MAPPING.get(name, f"noul {name}"),
            }
        )
    return {
        "version": noul.__version__,
        "tool_count": len(tools_list),
        "tools": tools_list,
    }


def render_catalog_html(payload: dict) -> str:
    cards = ['        <div class="tools-grid">']
    for item in payload["tools"]:
        name = html.escape(item["name"])
        desc = html.escape(item["description"])
        req = ", ".join(item["required"]) or "none"
        opt = ", ".join(item["Optional"]) or "none"
        cli_cmd = html.escape(item["cli_equivalent"])
        cards.append(
            f'          <article class="tool-card" data-tool="{name}">\n'
            f'            <h4 class="mono">{name}</h4>\n'
            f"            <p>{desc}</p>\n"
            f'            <div class="tool-meta mono">\n'
            f"              <div><strong>Required:</strong> {html.escape(req)} | <strong>Optional:</strong> {html.escape(opt)}</div>\n"
            f"              <div><strong>CLI:</strong> <code>{cli_cmd}</code></div>\n"
            f"            </div>\n"
            f"          </article>"
        )
    cards.append("        </div>")
    return "\n".join(cards)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the Noul static documentation site.")
    parser.add_argument(
        "--check", action="store_true", help="Verify that site files are up-to-date without modifying them."
    )
    args = parser.parse_args(argv)

    if not INDEX_HTML.is_file():
        print(f"error: missing {INDEX_HTML}", file=sys.stderr)
        return 1

    payload = build_tools_payload()
    expected_json = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    catalog_block = f"{START_MARKER}\n{render_catalog_html(payload)}\n        {END_MARKER}"

    current_html = INDEX_HTML.read_text(encoding="utf-8")
    if START_MARKER not in current_html or END_MARKER not in current_html:
        print("error: markers TOOLS_CATALOG_START / TOOLS_CATALOG_END missing from site/index.html", file=sys.stderr)
        return 1

    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.S)
    updated_html = pattern.sub(lambda _: catalog_block, current_html)

    if args.check:
        if not TOOLS_JSON.is_file():
            print("error: site/tools-reference.json does not exist (run scripts/build_site.py)", file=sys.stderr)
            return 1
        if TOOLS_JSON.read_text(encoding="utf-8") != expected_json:
            print("error: site/tools-reference.json is out of sync with the tool registry", file=sys.stderr)
            return 1
        if updated_html != current_html:
            print("error: site/index.html tools catalog is out of sync with the tool registry", file=sys.stderr)
            return 1
        expected_install = render_install_html(json.loads(SITE_CONFIG.read_text(encoding="utf-8")))
        if not INSTALL_HTML.is_file() or INSTALL_HTML.read_text(encoding="utf-8") != expected_install:
            print("error: site/install.html is out of sync with site/config.json", file=sys.stderr)
            return 1
        stale = [p.relative_to(ROOT).as_posix() for p, (cur, exp) in render_docs(payload).items() if cur != exp]
        if stale:
            print(f"error: generated tool lists are stale in: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"ok: site/ and generated tool lists are in sync ({payload['tool_count']} MCP tools documented).")
        return 0

    TOOLS_JSON.write_text(expected_json, encoding="utf-8")
    INDEX_HTML.write_text(updated_html, encoding="utf-8")
    INSTALL_HTML.write_text(render_install_html(json.loads(SITE_CONFIG.read_text(encoding="utf-8"))), encoding="utf-8")
    for path, (current, expected) in render_docs(payload).items():
        if current != expected:
            path.write_text(expected, encoding="utf-8")
    print(f"Built site/tools-reference.json and updated site/index.html ({payload['tool_count']} tools).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
