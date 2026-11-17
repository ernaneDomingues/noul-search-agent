#!/usr/bin/env python3
"""CLI for humans and agents without MCP support. Prints JSON (or formatted Markdown with --pretty).
Usage:
  noul search "query" [--year-min N] [--types rct,meta-analysis] [--sources a,b] [--limit N] [--offset N] [--domain D] [--pretty]
                    [--interactive] [--skip-clarification | --skip-clarify] [--export-obsidian ./vault]
  noul clarify "query" [--domain D] [--answers '{"q1":"...","q2":2}']
  noul paper ID_OR_DOI [--domain D]
  noul domain ID_OR_DOI [--domain D]
  noul artifacts ID_OR_DOI [--no-verify] [--fulltext]
  noul references ID_OR_DOI [--limit 10] [--direction both|backward|forward] [--sort influential|citations|year]
  noul compare ID1 ID2 ID3 | ID1,ID2,ID3 [--fulltext] [--pretty]
  noul insights ID1 ID2 ... | ID1,ID2 [--topic "..."] [--pretty]
  noul expand ["query"] [--ids ID1,ID2] [--run 1|2|3] [--limit N] [--pretty]
  noul benchmarks ID_OR_DOI [--pretty]
  noul hardware ID_OR_DOI [--params-b F] [--active-params-b F] [--context-length N] [--pretty]
  noul ground ID_OR_DOI [--claims "claim 1; claim 2"] [--pretty]
  noul math ID_OR_DOI [--mode latex|python|both] [--pretty]
  noul ablation ID_OR_DOI [--pretty]
  noul prompts ID_OR_DOI [--pretty]
  noul fulltext ID_OR_DOI [--question Q]
  noul ask ID1,ID2 --question Q
  noul graph ID_OR_DOI
  noul related ID_OR_DOI [--relation all|similar|references|citing] [--types ...] [--year-min N]
  noul download ID1,ID2 [--dir ./papers]
  noul export [ID1,ID2] [--project SLUG [--status included|candidate|excluded|all]]
              [--format bibtex|ris|csv|csl-json|obsidian] [--vault-dir ./vault | --export-obsidian ./vault]
  noul zotero [ID1,ID2] [--project SLUG [--status ...]] [--collection NAME_OR_KEY]
  noul dossier save --topic "Topic" --file report.md [--ids ID1,ID2] [--dir ./research]
  noul dossier list [--dir ./research]
  noul dossier show SLUG [--dir ./research]
  noul project create "Title" [--question Q] | list [--all] | show PROJECT
  noul project archive PROJECT [--undo] | delete SLUG [--yes]
  noul project add PROJECT ID1,ID2 [--status candidate|included|excluded] [--reason R]
  noul project status PROJECT ID1,ID2 included|excluded|candidate --reason R
  noul project note PROJECT "text" [--paper ID]
  noul config [email:you@university.edu] [--email E] [--workspace W] [--download-dir D] [--research-dir R] [--clear]
              [--zotero-user-id ID] [--zotero-api-key-stdin | --zotero-remove-key]
  noul meter '[{"id":"doi:10.1/x","stance":"supportive"}]'   (or `-` to read the JSON list from stdin)
  noul mcp [--http [PORT]] [--host H]       (MCP server; same as noul-mcp)
  noul install [targets] [--runner python|uvx] [--dry-run] [--uninstall]   (same as noul-install)
"""

import argparse
import json
import sys

from noul import __version__
from noul import log as _log
from noul.cli import commands as _commands


def _double_click_install():
    """The executable opened with no arguments (double-click): run the install wizard, then keep the console
    window open so the result can be read."""
    from noul import install

    print(f"Noul {__version__} installer\n")
    try:
        code = install.main([])
    except KeyboardInterrupt:
        code = 1
    try:
        input("\nPress Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass
    return code


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="noul", description="Noul Search: scientific literature search for humans and agents."
    )
    ap.add_argument("--pretty", dest="global_pretty", action="store_true", help="Human-friendly Markdown output")
    ap.add_argument("--version", action="version", version=f"noul-search-agent {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _commands.add_commands(sub)
    # Listed for --help only: main() hands these two to their own entry points before parsing.
    sub.add_parser("mcp", help="Run the MCP server (stdio, or --http [PORT])", add_help=False)
    sub.add_parser("install", help="Install into Claude, Copilot, Codex, Gemini, Antigravity", add_help=False)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "mcp":
        from noul.server import mcp as noul_mcp

        return noul_mcp.main(argv[1:]) or 0
    if argv and argv[0] == "install":
        from noul import install

        return install.main(argv[1:])
    if not argv and getattr(sys, "frozen", False):
        return _double_click_install()

    ap = build_parser()
    a = ap.parse_args(argv)
    command = getattr(a, "_command", None)
    if command is None:  # `mcp` / `install` after a global option
        ap.error(f"'{a.cmd}' must be the first argument: noul {a.cmd} ...")
    # A subcommand's own --pretty default would overwrite the global flag, so the two are merged here.
    a.pretty = bool(getattr(a, "pretty", False) or a.global_pretty)

    _log.configure()
    try:
        out = command.run(a)
        text = command.pretty(out, a) if a.pretty and command.pretty is not None else None
    except Exception as ex:
        print(json.dumps({"error": str(ex)}), file=sys.stderr)
        return 1
    if text is not None:
        print(text)
        return 0

    for warning in getattr(out, "warnings", ()):  # stderr: stdout stays a parseable export file
        print(f"warning: {warning}", file=sys.stderr)
    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
