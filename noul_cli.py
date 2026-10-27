#!/usr/bin/env python3
"""CLI for humans and agents without MCP support. Prints JSON (or formatted Markdown with --pretty).
Usage:
  noul_cli.py search "query" [--year-min N] [--types rct,meta-analysis] [--sources a,b] [--limit N] [--offset N] [--pretty]
  noul_cli.py paper ID_OR_DOI
  noul_cli.py fulltext ID_OR_DOI [--question Q]
  noul_cli.py ask ID1,ID2 --question Q
  noul_cli.py graph ID_OR_DOI
  noul_cli.py related ID_OR_DOI [--relation all|similar|references|citing] [--types ...] [--year-min N]
  noul_cli.py download ID1,ID2 [--dir ./papers]
  noul_cli.py export ID1,ID2 [--format bibtex|ris|csv]
  noul_cli.py dossier save --topic "Topic" --file report.md [--ids ID1,ID2] [--dir ./research]
  noul_cli.py dossier list [--dir ./research]
  noul_cli.py dossier show SLUG [--dir ./research]
  noul_cli.py config [email:you@university.edu] [--email E] [--workspace W] [--download-dir D] [--research-dir R] [--clear]
  noul_cli.py meter '[{"id":"doi:10.1/x","stance":"supportive"}]'   (or `-` to read the JSON list from stdin)
  noul_cli.py mcp [--http [PORT]] [--host H]       (MCP server; same as noul-mcp)
  noul_cli.py install [targets] [--runner python|uvx|npx] [--dry-run] [--uninstall]   (same as noul-install)
"""
import argparse
import json
import sys
from pathlib import Path

import noul_tools as t
from noul_core import __version__

CONFIG_KEYS = {
    "email": "email",
    "workspace": "workspace",
    "download_dir": "download_dir",
    "download-dir": "download_dir",
    "downloads": "download_dir",
    "papers": "download_dir",
    "research_dir": "research_dir",
    "research-dir": "research_dir",
    "research": "research_dir",
}


def _parse_config_pairs(pairs):
    """`email:you@x.org workspace:~/w` -> kwargs for configure_noul. A bare value containing '@' is the email."""
    out = {}
    for pair in pairs or []:
        if pair.strip().lower() == "clear":
            out["clear"] = True
            continue
        key, sep, value = pair.partition(":")
        if sep and key.strip().lower() in CONFIG_KEYS:
            out[CONFIG_KEYS[key.strip().lower()]] = value.strip()
        elif "@" in pair and not sep:
            out["email"] = pair.strip()
        else:
            raise ValueError(f"Unknown setting '{pair}'. Use key:value with key in email, workspace, download_dir, research_dir")
    return out


def _load_meter_items(arg):
    raw = sys.stdin.read() if arg == "-" else arg
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"meter expects a JSON list of {{id, stance, evidence?}} objects: {e}") from None
    return data.get("items") if isinstance(data, dict) else data


def _format_pretty_search(res):
    lines = [f"# Search: {res.get('query')} ({len(res.get('papers') or [])} shown of {res.get('total_found', 0)})"]
    if res.get("warnings"):
        lines.append("> Warnings: " + "; ".join(res["warnings"]))
    lines.append("")
    for p in res.get("papers") or []:
        oa = " [OA PDF]" if p.get("open_access") else ""
        authors = ", ".join(p.get("authors") or [])
        lines.append(
            f"[{p.get('n', '?')}] **{p.get('title')}** ({p.get('year') or 'n.d.'}){oa}\n"
            f"    - ID: `{p.get('id')}` | DOI: `{p.get('doi') or 'N/A'}` | Design: `{p.get('evidence')}` | "
            f"Citations: {p.get('citations', 0)} | Venue: {p.get('journal') or 'N/A'}\n"
            f"    - Authors: {authors}\n"
            f"    - Abstract: {(p.get('abstract') or '')[:320]}..."
        )
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "mcp":
        import noul_mcp

        return noul_mcp.main(argv[1:]) or 0
    if argv and argv[0] == "install":
        import install

        return install.main(argv[1:])

    ap = argparse.ArgumentParser(prog="noul", description="Noul Search: scientific literature search for humans and agents.")
    ap.add_argument("--pretty", action="store_true", help="Human-friendly Markdown output")
    ap.add_argument("--version", action="version", version=f"noul-search-agent {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--year-min", type=int)
    s.add_argument("--types")
    s.add_argument("--sources")
    s.add_argument("--min-citations", type=int)
    s.add_argument("--open-access", action="store_true")
    s.add_argument("--sort", choices=["relevance", "date", "citations"])
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--pretty", action="store_true")

    for n in ("paper", "graph", "fulltext"):
        x = sub.add_parser(n)
        x.add_argument("id")
        if n == "fulltext":
            x.add_argument("--question", default="")

    ask_p = sub.add_parser("ask")
    ask_p.add_argument("ids", help="Comma-separated paper IDs or DOIs")
    ask_p.add_argument("--question", required=True)
    ask_p.add_argument("--chunks", type=int, default=4)

    rel_p = sub.add_parser("related")
    rel_p.add_argument("id")
    rel_p.add_argument("--relation", default="all", choices=["all", "similar", "references", "citing"])
    rel_p.add_argument("--types")
    rel_p.add_argument("--year-min", type=int)
    rel_p.add_argument("--limit", type=int, default=15)

    dl_p = sub.add_parser("download")
    dl_p.add_argument("ids", help="Comma-separated paper IDs or DOIs")
    dl_p.add_argument("--dir", default=None, help="Inside the workspace (default: configured download_dir)")

    e = sub.add_parser("export")
    e.add_argument("ids")
    e.add_argument("--format", default="bibtex", choices=["bibtex", "ris", "csv"])

    dos = sub.add_parser("dossier")
    dos_sub = dos.add_subparsers(dest="dossier_cmd", required=True)
    d_save = dos_sub.add_parser("save")
    d_save.add_argument("--topic", required=True)
    d_save.add_argument("--text", default="", help="Markdown synthesis string")
    d_save.add_argument("--file", default="", help="Path to Markdown file")
    d_save.add_argument("--ids", default="", help="Comma-separated paper IDs")
    d_save.add_argument("--dir", default=None, help="Inside the workspace (default: configured research_dir)")

    d_list = dos_sub.add_parser("list")
    d_list.add_argument("--dir", default=None, help="Inside the workspace (default: configured research_dir)")

    d_show = dos_sub.add_parser("show")
    d_show.add_argument("slug")
    d_show.add_argument("--dir", default=None, help="Inside the workspace (default: configured research_dir)")

    cfg_p = sub.add_parser("config", help="View or update persistent settings (no arguments = show)")
    cfg_p.add_argument("pairs", nargs="*", metavar="KEY:VALUE", help="e.g. email:you@university.edu workspace:~/research")
    cfg_p.add_argument("--email")
    cfg_p.add_argument("--workspace")
    cfg_p.add_argument("--download-dir")
    cfg_p.add_argument("--research-dir")
    cfg_p.add_argument("--clear", action="store_true", help="Delete the config file (env vars still apply)")

    m_p = sub.add_parser("meter", help="Deterministic Evidence Meter from classified papers")
    m_p.add_argument("items", help='JSON list like [{"id":"...","stance":"supportive"}], or - to read stdin')

    sub.add_parser("mcp", help="Run the MCP server (stdio, or --http [PORT])", add_help=False)
    sub.add_parser("install", help="Install into Claude, Copilot, Codex, Gemini, Antigravity", add_help=False)

    a = ap.parse_args(argv)
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()] if v else None

    try:
        if a.cmd == "search":
            out = t.search_papers(
                a.query, split(a.sources), a.year_min, split(a.types), a.min_citations,
                a.open_access, a.sort, a.limit, a.offset
            )
            if getattr(a, "pretty", False):
                print(_format_pretty_search(out))
                return 0
        elif a.cmd == "paper":
            out = t.get_paper(a.id)
        elif a.cmd == "graph":
            out = t.citation_graph(a.id)
        elif a.cmd == "fulltext":
            out = t.get_fulltext(a.id, a.question)
        elif a.cmd == "ask":
            out = t.query_fulltexts(split(a.ids), a.question, a.chunks)
        elif a.cmd == "related":
            out = t.related_papers(a.id, a.relation, split(a.types), a.year_min, a.limit)
        elif a.cmd == "download":
            out = t.download_papers(split(a.ids), a.dir)
        elif a.cmd == "export":
            out = t.export_papers(split(a.ids), a.format)
        elif a.cmd == "dossier":
            if a.dossier_cmd == "save":
                md = Path(a.file).read_text(encoding="utf-8") if a.file else a.text
                out = t.save_research_dossier(a.topic, md, split(a.ids), a.dir)
            elif a.dossier_cmd == "list":
                out = t.list_research_dossiers(a.dir)
            elif a.dossier_cmd == "show":
                out = t.get_research_dossier(a.slug, a.dir)
        elif a.cmd == "config":
            kwargs = _parse_config_pairs(a.pairs)
            for key in ("email", "workspace", "download_dir", "research_dir"):
                if getattr(a, key) is not None:
                    kwargs[key] = getattr(a, key)
            if a.clear:
                kwargs["clear"] = True
            out = t.configure_noul(**kwargs)
        elif a.cmd == "meter":
            out = t.evidence_meter(_load_meter_items(a.items))
    except Exception as ex:
        print(json.dumps({"error": str(ex)}), file=sys.stderr)
        return 1

    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
