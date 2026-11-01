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
  noul export ID1,ID2 [--format bibtex|ris|csv|obsidian] [--vault-dir ./vault | --export-obsidian ./vault]
  noul dossier save --topic "Topic" --file report.md [--ids ID1,ID2] [--dir ./research]
  noul dossier list [--dir ./research]
  noul dossier show SLUG [--dir ./research]
  noul config [email:you@university.edu] [--email E] [--workspace W] [--download-dir D] [--research-dir R] [--clear]
  noul meter '[{"id":"doi:10.1/x","stance":"supportive"}]'   (or `-` to read the JSON list from stdin)
  noul mcp [--http [PORT]] [--host H]       (MCP server; same as noul-mcp)
  noul install [targets] [--runner python|uvx|npx] [--dry-run] [--uninstall]   (same as noul-install)
"""

import argparse
import inspect
import json
import sys
from pathlib import Path

from noul import __version__, registry
from noul import log as _log
from noul.cli.pretty import (
    _format_pretty_ablation,
    _format_pretty_benchmarks,
    _format_pretty_compare,
    _format_pretty_expand,
    _format_pretty_failures,
    _format_pretty_ground,
    _format_pretty_hardware,
    _format_pretty_insights,
    _format_pretty_math,
    _format_pretty_prompts,
    _format_pretty_search,
)

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


DOMAIN_HELP = "computer_science|biomedical|economics|general_stem (aliases: cs, bio, med, econ, stem)"


def _stdin_is_tty():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _interactive_clarification(query, domain=None):
    """Ask the clarification questions on stderr and read answers from stdin. Returns the refined scope or None
    when the query is already specific. Only called on an interactive terminal."""
    scope = _run("clarify_search_scope", query, domain=domain)
    if not scope["needs_clarification"]:
        return None
    print(f"Your query looks broad ({scope['domain']}). Answer to refine it (Enter to skip):", file=sys.stderr)
    answers = {}
    for q in scope["questions"]:
        print(f"\n{q['question']}", file=sys.stderr)
        for i, opt in enumerate(q["options"], 1):
            print(f"  {i}. {opt}", file=sys.stderr)
        sys.stderr.write("> ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:  # EOF
            break
        if line.strip():
            answers[q["id"]] = line.strip()
    if not answers:
        return None
    return _run("clarify_search_scope", query, answers=answers, domain=domain)


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
            raise ValueError(
                f"Unknown setting '{pair}'. Use key:value with key in email, workspace, download_dir, research_dir"
            )
    return out


def _load_meter_items(arg):
    raw = sys.stdin.read() if arg == "-" else arg
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"meter expects a JSON list of {{id, stance, evidence?}} objects: {e}") from None
    return data.get("items") if isinstance(data, dict) else data


def _split_ids(values):
    """`id1 id2` and/or `id1,id2` -> [id1, id2]."""
    return [x.strip() for v in values or [] for x in str(v).split(",") if x.strip()]


def _run(name, *args, **kwargs):
    """Run a tool through the registry (validation + logging), binding positional arguments by name."""
    bound = inspect.signature(registry.TOOLS[name][3]).bind_partial(*args, **kwargs)
    return registry.call(name, dict(bound.arguments))


def _double_click_install():
    """The executable opened with no arguments (double-click): run the install wizard, then keep the console
    window open so the result can be read."""
    from noul import install

    print(f"Noul {__version__} - instalador / installer\n")
    try:
        code = install.main([])
    except KeyboardInterrupt:
        code = 1
    try:
        input("\nPressione Enter para fechar / Press Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass
    return code


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

    ap = argparse.ArgumentParser(
        prog="noul", description="Noul Search: scientific literature search for humans and agents."
    )
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
    s.add_argument("--domain", help=DOMAIN_HELP)
    s.add_argument("--pretty", action="store_true")
    s.add_argument(
        "--interactive",
        action="store_true",
        help="ask 2-3 scope questions on a terminal when the query is broad, then run the refined search",
    )
    s.add_argument(
        "--skip-clarification",
        "--skip-clarify",
        dest="skip_clarification",
        action="store_true",
        help="never ask clarification questions (default for agents and non-interactive stdin)",
    )
    s.add_argument(
        "--export-obsidian",
        metavar="VAULT_DIR",
        help="also export the returned papers as Obsidian notes into VAULT_DIR (inside the workspace)",
    )

    cl = sub.add_parser("clarify")
    cl.add_argument("query")
    cl.add_argument("--domain", help=DOMAIN_HELP)
    cl.add_argument("--answers", help='JSON object, e.g. \'{"q1":"Benchmarks e avaliação empírica","q3":1}\'')

    ar = sub.add_parser("artifacts")
    ar.add_argument("id")
    ar.add_argument("--no-verify", action="store_true", help="skip the HTTP liveness check")
    ar.add_argument("--fulltext", action="store_true", help="also scan the open-access full text")

    rf = sub.add_parser("references")
    rf.add_argument("id")
    rf.add_argument("--limit", type=int, default=10)
    rf.add_argument("--direction", choices=["both", "backward", "forward"], default="both")
    rf.add_argument("--sort", choices=["influential", "citations", "year"], default="influential")

    cmp_p = sub.add_parser("compare", help="Comparative matrix of 1-10 papers")
    cmp_p.add_argument("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated)")
    cmp_p.add_argument("--fulltext", action="store_true", help="also mine the open-access full text")
    cmp_p.add_argument("--pretty", action="store_true")

    ins_p = sub.add_parser("insights", help="Thematic insights (consensuses, tensions, future work) of 1-20 papers")
    ins_p.add_argument("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated)")
    ins_p.add_argument("--topic")
    ins_p.add_argument("--pretty", action="store_true")

    hyp_p = sub.add_parser(
        "hypotheses", help="Cross-analyze >= 2 papers to detect research gaps and generate hypotheses"
    )
    hyp_p.add_argument("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated, at least 2)")
    hyp_p.add_argument("--topic")
    hyp_p.add_argument(
        "--max", dest="max_hypotheses", type=int, default=3, help="maximum hypotheses to generate (default: 3)"
    )
    hyp_p.add_argument("--pretty", action="store_true")

    exp_p = sub.add_parser("expand", help="Suggest 3 boolean queries (AND/OR/NOT) from a query and/or papers")
    exp_p.add_argument("query", nargs="?", default="")
    exp_p.add_argument("--ids", help="Paper IDs/DOIs (comma-separated) to mine terms from")
    exp_p.add_argument("--run", type=int, choices=[1, 2, 3], help="run suggestion N with search_papers")
    exp_p.add_argument("--limit", type=int, default=10)
    exp_p.add_argument("--pretty", action="store_true")

    bm_p = sub.add_parser("benchmarks", help="Extract and normalize the benchmark tables of a paper")
    bm_p.add_argument("id")
    bm_p.add_argument("--pretty", action="store_true")

    hw_p = sub.add_parser("hardware", help="Estimate inference VRAM (FP16/INT8/INT4) and hardware tier of a paper")
    hw_p.add_argument("id")
    hw_p.add_argument("--params-b", type=float, help="total parameters in billions (overrides detection)")
    hw_p.add_argument("--active-params-b", type=float, help="active parameters per token in billions (MoE)")
    hw_p.add_argument("--context-length", type=int, default=8192)
    hw_p.add_argument("--pretty", action="store_true")

    gr_p = sub.add_parser("ground", help="Verify claims against the paper's literal text (strict citations)")
    gr_p.add_argument("id")
    gr_p.add_argument("--claims", help='semicolon-separated claims, e.g. "claim 1; claim 2" (default: auto-extract)')
    gr_p.add_argument("--pretty", action="store_true")
    ma_p = sub.add_parser("math", help="Extract equations/algorithms as LaTeX and/or typed Python functions")
    ma_p.add_argument("id")
    ma_p.add_argument("--mode", choices=["latex", "python", "both"], default="latex")
    ma_p.add_argument("--pretty", action="store_true")
    ab_p = sub.add_parser("ablation", help="Rank ablated components by impact (delta, delta %%, marginal/dominant)")
    ab_p.add_argument("id")
    ab_p.add_argument("--pretty", action="store_true")
    pr_p = sub.add_parser("prompts", help="Extract prompt templates, decoding hyperparameters and JSON schemas")
    pr_p.add_argument("id")
    pr_p.add_argument("--pretty", action="store_true")
    fm_p = sub.add_parser(
        "failures", help="Detect failure modes, critical assumptions and baseline superiority scenarios"
    )
    fm_p.add_argument("id")
    fm_p.add_argument("--no-fulltext", action="store_true", help="analyse title/abstract only")
    fm_p.add_argument("--pretty", action="store_true")
    df_p = sub.add_parser("diff", help="Compare two arXiv revisions (e.g. v1 vs v2)")
    df_p.add_argument("arxiv_id")
    df_p.add_argument("--from", dest="from_version", default="v1", help="source version (default: v1)")
    df_p.add_argument("--to", dest="to_version", default="v2", help="target version (default: v2)")
    df_p.add_argument("--pretty", action="store_true")

    rev_p = sub.add_parser("review", help="Multi-persona domain-adaptive peer review of a paper")
    rev_p.add_argument("id")
    rev_p.add_argument("--domain", help=DOMAIN_HELP)
    rev_p.add_argument("--no-fulltext", action="store_true", help="analyse title/abstract only")
    rev_p.add_argument("--pretty", action="store_true")

    sc_p = sub.add_parser(
        "scaffold", help="Generate PyTorch, Statsmodels/R, or Clinical Protocol scaffold + unit tests"
    )
    sc_p.add_argument("id")
    sc_p.add_argument("--target", help="pytorch|statsmodels|r|protocol (default: auto from domain)")
    sc_p.add_argument("--domain", help=DOMAIN_HELP)
    sc_p.add_argument("--pretty", action="store_true")

    cite_p = sub.add_parser("cite", help="Contextual citation recommender and BibTeX infiller (#22)")
    cite_p.add_argument("draft_text", help="Draft paragraph or path to .md/.tex file inside workspace")
    cite_p.add_argument("--limit-per-claim", type=int, default=2, help="max papers recommended per claim (default: 2)")
    cite_p.add_argument("--style", choices=["latex", "markdown"], default="latex")
    cite_p.add_argument("--pretty", action="store_true")

    rw_p = sub.add_parser("related-work", help="Synthesize comparative Related Work section from papers (#24)")
    rw_p.add_argument("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated)")
    rw_p.add_argument("--proposal", required=True, help="Summary of your proposed contribution/innovation")
    rw_p.add_argument("--style", choices=["latex", "numeric"], default="latex")
    rw_p.add_argument("--format", dest="output_format", choices=["markdown", "latex"], default="markdown")
    rw_p.add_argument("--pretty", action="store_true")

    dr_p = sub.add_parser("draft", help="IMRaD manuscript blueprint generator and draft auditor (#23)")
    dr_sub = dr_p.add_subparsers(dest="draft_cmd", required=True)
    dr_struct = dr_sub.add_parser("structure", help="Generate IMRaD manuscript blueprint")
    dr_struct.add_argument("--topic", required=True, help="Manuscript topic or working title")
    dr_struct.add_argument(
        "--venue", default="neurips", help="Target venue (neurips, icml, iclr, acl, ieee, nature, lancet, aer, general)"
    )
    dr_struct.add_argument("--hypothesis", default="", help="Central hypothesis")
    dr_struct.add_argument("--findings", default="", help="Semicolon-separated key findings")
    dr_struct.add_argument("--format", dest="output_format", choices=["markdown", "latex"], default="markdown")
    dr_struct.add_argument("--pretty", action="store_true")

    dr_audit = dr_sub.add_parser("audit", help="Audit draft manuscript for weak claims, notation, and reproducibility")
    dr_audit.add_argument("draft_input", help="Path to .md/.tex draft in workspace or inline text")
    dr_audit.add_argument("--venue", default="neurips")
    dr_audit.add_argument("--format", dest="output_format", choices=["markdown", "latex"], default="markdown")
    dr_audit.add_argument("--pretty", action="store_true")

    bib_p = sub.add_parser("bibtex", help="BibTeX sanitizer and preprint-to-published resolver (#25)")
    bib_sub = bib_p.add_subparsers(dest="bibtex_cmd", required=True)
    bib_san = bib_sub.add_parser("sanitize", help="Sanitize .bib file/text and upgrade arXiv preprints")
    bib_san.add_argument("bibtex_input", help="Path to .bib file in workspace or raw BibTeX string")
    bib_san.add_argument("--update-preprints", dest="update_preprints", action="store_true", default=True)
    bib_san.add_argument("--no-update-preprints", dest="update_preprints", action="store_false")
    bib_san.add_argument("--output", dest="output_path", default=None, help="Output .bib path inside workspace")
    bib_san.add_argument("--pretty", action="store_true")

    reb_p = sub.add_parser("rebuttal", help="Peer-review Rebuttal Copilot (#26)")
    reb_sub = reb_p.add_subparsers(dest="rebuttal_cmd", required=True)
    reb_parse = reb_sub.add_parser("parse", help="Parse reviewer comments and draft structured rebuttal")
    reb_parse.add_argument("reviews_input", help="Path to reviews.txt/.md in workspace or raw reviews text")
    reb_parse.add_argument("--paper-id", default=None, help="Optional cached paper ID for grounding")
    reb_parse.add_argument("--draft", dest="draft_input", default=None, help="Optional path to manuscript draft")
    reb_parse.add_argument("--format", dest="output_format", choices=["markdown", "latex"], default="markdown")
    reb_parse.add_argument("--pretty", action="store_true")

    gk_p = sub.add_parser("gatekeeper", help="Pre-submission & desk-reject gatekeeper (#27)")
    gk_sub = gk_p.add_subparsers(dest="gatekeeper_cmd", required=True)
    gk_aud = gk_sub.add_parser("audit", help="Audit manuscript readiness (anonymity, venue checklist, overclaiming)")
    gk_aud.add_argument("draft_path", help="Path to .tex, .md, or .pdf inside workspace (or inline text)")
    gk_aud.add_argument("--venue", default="neurips", help="Target venue (default: neurips)")
    gk_aud.add_argument("--pretty", action="store_true")

    for n in ("paper", "graph", "fulltext", "domain"):
        x = sub.add_parser(n)
        x.add_argument("id")
        if n == "fulltext":
            x.add_argument("--question", default="")
        if n in ("paper", "domain"):
            x.add_argument("--domain", dest="domain_override", help=DOMAIN_HELP)

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
    e.add_argument("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated)")
    e.add_argument(
        "--format",
        default=None,
        choices=["bibtex", "ris", "csv", "obsidian"],
        help="default: bibtex (obsidian when --vault-dir/--export-obsidian is given)",
    )
    e.add_argument("--vault-dir", help="Obsidian vault folder inside the workspace (implies --format obsidian)")
    e.add_argument("--export-obsidian", metavar="VAULT_DIR", help="alias of --vault-dir")

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
    cfg_p.add_argument(
        "pairs", nargs="*", metavar="KEY:VALUE", help="e.g. email:you@university.edu workspace:~/research"
    )
    cfg_p.add_argument("--email")
    cfg_p.add_argument("--workspace")
    cfg_p.add_argument("--download-dir")
    cfg_p.add_argument("--research-dir")
    cfg_p.add_argument("--clear", action="store_true", help="Delete the config file (env vars still apply)")

    m_p = sub.add_parser("meter", help="Deterministic Evidence Meter from classified papers")
    m_p.add_argument("items", help='JSON list like [{"id":"...","stance":"supportive"}], or - to read stdin')

    tool_p = sub.add_parser("tool", help="Run any MCP tool by name with JSON arguments (same contract as MCP)")
    tool_p.add_argument("name", nargs="?", help="Tool name (omit with --list)")
    tool_p.add_argument(
        "--args", default="{}", help='JSON object of arguments, e.g. \'{"query": "..."}\', or - for stdin'
    )
    tool_p.add_argument("--list", action="store_true", help="List the tool names and exit")

    sub.add_parser("mcp", help="Run the MCP server (stdio, or --http [PORT])", add_help=False)
    sub.add_parser("install", help="Install into Claude, Copilot, Codex, Gemini, Antigravity", add_help=False)

    a = ap.parse_args(argv)

    def split(v):
        return [x.strip() for x in v.split(",") if x.strip()] if v else None

    _log.configure()
    try:
        if a.cmd == "tool":
            if a.list or not a.name:
                out = {"tools": list(registry.TOOLS)}
            else:
                raw = sys.stdin.read() if a.args == "-" else a.args
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"--args must be a JSON object: {e}") from e
                out = registry.call(a.name, args)
        elif a.cmd == "search":
            query, year_min, types, sources, sort = a.query, a.year_min, split(a.types), split(a.sources), a.sort
            refined = None
            if a.interactive and not a.skip_clarification:
                if _stdin_is_tty():
                    refined = _interactive_clarification(a.query, domain=a.domain)
                else:
                    print("note: --interactive ignored (stdin is not a terminal)", file=sys.stderr)
            if refined:
                # Explicit command-line filters always win over the clarification answers.
                f = refined["recommended_filters"]
                query = refined["refined_query"]
                year_min = year_min or f.get("year_min")
                types = types or f.get("study_types")
                sources = sources or f.get("sources")
                sort = sort or f.get("sort")
            out = _run(
                "search_papers",
                query,
                sources,
                year_min,
                types,
                a.min_citations,
                a.open_access,
                sort,
                a.limit,
                a.offset,
                domain=a.domain,
            )
            if refined:
                out["clarification"] = {
                    k: refined[k] for k in ("refined_query", "recommended_filters", "applied_answers")
                }
            if a.export_obsidian:
                ids = [p["id"] for p in out["papers"] if p.get("id")]
                out["obsidian_export"] = (
                    _run("export_obsidian_notes", ids, vault_dir=a.export_obsidian)
                    if ids
                    else {"vault_dir": None, "exported_count": 0, "notes": []}
                )
            if getattr(a, "pretty", False):
                print(_format_pretty_search(out))
                if a.export_obsidian:
                    exp = out["obsidian_export"]
                    print(f"\n> Exported {exp['exported_count']} Obsidian note(s) to {exp['vault_dir']}")
                return 0
        elif a.cmd == "clarify":
            answers = None
            if a.answers:
                try:
                    answers = json.loads(a.answers)
                except json.JSONDecodeError as e:
                    raise ValueError(f"--answers must be a JSON object: {e}") from e
            out = _run("clarify_search_scope", a.query, answers=answers, domain=a.domain)
        elif a.cmd == "paper":
            out = _run("get_paper", a.id, domain=a.domain_override)
        elif a.cmd == "references":
            out = _run("get_paper_references", a.id, limit=a.limit, direction=a.direction, sort_by=a.sort)
        elif a.cmd == "compare":
            out = _run("compare_papers", _split_ids(a.ids), inspect_fulltext=a.fulltext)
            if a.pretty:
                print(_format_pretty_compare(out))
                return 0
        elif a.cmd == "insights":
            out = _run("get_thematic_insights", _split_ids(a.ids), topic=a.topic)
            if a.pretty:
                print(_format_pretty_insights(out))
                return 0
        elif a.cmd == "hypotheses":
            out = _run(
                "generate_research_hypotheses", _split_ids(a.ids), topic=a.topic, max_hypotheses=a.max_hypotheses
            )
            if a.pretty:
                print(out["markdown_report"])
                return 0
        elif a.cmd == "expand":
            out = _run("suggest_boolean_queries", a.query, paper_ids=_split_ids([a.ids]) if a.ids else None)
            if a.run:
                chosen = out["suggestions"][a.run - 1]
                res = _run("search_papers", chosen["boolean_query"], limit=a.limit)
                out = {"applied_suggestion": chosen, "expansion": out, "search": res}
                if a.pretty:
                    print(f"> Running [{a.run}] {chosen['boolean_query']}\n")
                    print(_format_pretty_search(res))
                    return 0
            elif a.pretty:
                print(_format_pretty_expand(out))
                return 0
        elif a.cmd == "benchmarks":
            out = _run("extract_benchmark_tables", a.id)
            if a.pretty:
                print(_format_pretty_benchmarks(out))
                return 0
        elif a.cmd == "hardware":
            out = _run(
                "estimate_hardware_specs",
                a.id,
                total_params_b=a.params_b,
                active_params_b=a.active_params_b,
                context_length=a.context_length,
            )
            if a.pretty:
                print(_format_pretty_hardware(out))
                return 0
        elif a.cmd == "ground":
            claims = [x.strip() for x in a.claims.split(";") if x.strip()] if a.claims else None
            out = _run("verify_paper_claims", a.id, claims=claims)
            if a.pretty:
                print(_format_pretty_ground(out))
                return 0
        elif a.cmd == "math":
            out = _run("extract_paper_math", a.id, mode=a.mode)
            if a.pretty:
                print(_format_pretty_math(out))
                return 0
        elif a.cmd == "ablation":
            out = _run("analyze_ablation_tables", a.id)
            if a.pretty:
                print(_format_pretty_ablation(out))
                return 0
        elif a.cmd == "prompts":
            out = _run("extract_prompts_and_decoding", a.id)
            if a.pretty:
                print(_format_pretty_prompts(out))
                return 0
        elif a.cmd == "failures":
            out = _run("detect_failure_modes", a.id, inspect_fulltext=not a.no_fulltext)
            if a.pretty:
                print(_format_pretty_failures(out))
                return 0
        elif a.cmd == "diff":
            out = _run("diff_arxiv_versions", a.arxiv_id, from_version=a.from_version, to_version=a.to_version)
            if a.pretty:
                print(out["markdown_diff"])
                return 0
        elif a.cmd == "review":
            out = _run("review_paper", a.id, domain=a.domain, inspect_fulltext=not a.no_fulltext)
            if a.pretty:
                print(out["rendered_markdown"])
                return 0
        elif a.cmd == "scaffold":
            out = _run("scaffold_implementation", a.id, target=a.target, domain=a.domain)
            if a.pretty:
                lang = out.get("language", "python")
                lines = [
                    f"# Code & Protocol Scaffold: {out.get('title') or out['paper_id']}",
                    f"Domain: `{out['domain']}` | Target: `{out['target']}` | Framework: `{out['framework']}` | Syntax valid: `{out['syntax_valid']}`",
                    "",
                    "## Source Code",
                    f"```{lang}",
                    out["source_code"].rstrip(),
                    "```",
                    "",
                    "## Unit Tests",
                    f"```{lang}",
                    out["unit_tests"].rstrip(),
                    "```",
                ]
                print("\n".join(lines))
                return 0
        elif a.cmd == "cite":
            out = _run("recommend_citations", a.draft_text, max_per_claim=a.limit_per_claim, citation_style=a.style)
            if a.pretty:
                lines = [
                    "# Contextual Citation Infiller Report",
                    f"Claims detected: `{out['claims_detected']}` | Format: `{out['input_format']}`",
                    "",
                    "## Annotated Text",
                    out["annotated_text"],
                    "",
                    "## Recommendations & Justifications",
                ]
                for rec in out.get("recommendations") or []:
                    lines.append(f'- **Claim:** "{rec["claim_sentence"]}"')
                    for j in rec.get("justifications") or []:
                        lines.append(f"  - {j}")
                if out.get("bibtex_block"):
                    lines += ["", "## BibTeX", "```bibtex", out["bibtex_block"], "```"]
                print("\n".join(lines))
                return 0
        elif a.cmd == "related-work":
            out = _run(
                "synthesize_related_work",
                _split_ids(a.ids),
                proposal=a.proposal,
                citation_style=a.style,
                output_format=a.output_format,
            )
            if a.pretty:
                print(out["rendered_related_work"])
                if out.get("bibtex_block"):
                    print("\n```bibtex\n" + out["bibtex_block"] + "\n```")
                return 0
        elif a.cmd == "draft":
            if a.draft_cmd == "structure":
                findings = [x.strip() for x in a.findings.split(";") if x.strip()] if a.findings else None
                out = _run(
                    "generate_manuscript_blueprint",
                    a.topic,
                    venue=a.venue,
                    hypothesis=a.hypothesis,
                    key_findings=findings,
                    output_format=a.output_format,
                )
                if a.pretty:
                    print(out["rendered_template"])
                    return 0
            elif a.draft_cmd == "audit":
                out = _run("audit_manuscript_draft", a.draft_input, venue=a.venue, output_format=a.output_format)
                if a.pretty:
                    print(out["rendered_report"])
                    return 0
        elif a.cmd == "bibtex":
            if a.bibtex_cmd == "sanitize":
                out = _run(
                    "sanitize_bibtex", a.bibtex_input, update_preprints=a.update_preprints, output_path=a.output_path
                )
                if a.pretty:
                    print(out["sanitized_bibtex"])
                    return 0
        elif a.cmd == "rebuttal":
            if a.rebuttal_cmd == "parse":
                out = _run(
                    "parse_and_draft_rebuttal",
                    a.reviews_input,
                    paper_id=a.paper_id,
                    draft_input=a.draft_input,
                    output_format=a.output_format,
                )
                if a.pretty:
                    print(out["latex_rebuttal"] if a.output_format == "latex" else out["markdown_rebuttal"])
                    return 0
        elif a.cmd == "gatekeeper":
            if a.gatekeeper_cmd == "audit":
                out = _run("audit_submission_readiness", a.draft_path, venue=a.venue)
                if a.pretty:
                    print(out["rendered_markdown"])
                    return 0
        elif a.cmd == "artifacts":
            out = _run("scan_paper_artifacts", a.id, verify_http=not a.no_verify, inspect_fulltext=a.fulltext)
        elif a.cmd == "domain":
            out = _run("detect_paper_domain", a.id, domain=a.domain_override)
        elif a.cmd == "graph":
            out = _run("citation_graph", a.id)
        elif a.cmd == "fulltext":
            out = _run("get_fulltext", a.id, a.question)
        elif a.cmd == "ask":
            out = _run("query_fulltexts", split(a.ids), a.question, a.chunks)
        elif a.cmd == "related":
            out = _run("related_papers", a.id, a.relation, split(a.types), a.year_min, a.limit)
        elif a.cmd == "download":
            out = _run("download_papers", split(a.ids), a.dir)
        elif a.cmd == "export":
            vault = a.vault_dir or a.export_obsidian
            if a.format == "obsidian" or vault:
                out = _run("export_obsidian_notes", _split_ids(a.ids), vault_dir=vault)
            else:
                out = _run("export_papers", _split_ids(a.ids), a.format or "bibtex")
        elif a.cmd == "dossier":
            if a.dossier_cmd == "save":
                md = Path(a.file).read_text(encoding="utf-8") if a.file else a.text
                out = _run("save_research_dossier", a.topic, md, split(a.ids), a.dir)
            elif a.dossier_cmd == "list":
                out = _run("list_research_dossiers", a.dir)
            elif a.dossier_cmd == "show":
                out = _run("get_research_dossier", a.slug, a.dir)
        elif a.cmd == "config":
            kwargs = _parse_config_pairs(a.pairs)
            for key in ("email", "workspace", "download_dir", "research_dir"):
                if getattr(a, key) is not None:
                    kwargs[key] = getattr(a, key)
            if a.clear:
                kwargs["clear"] = True
            out = _run("configure_noul", **kwargs)
        elif a.cmd == "meter":
            out = _run("evidence_meter", _load_meter_items(a.items))
    except Exception as ex:
        print(json.dumps({"error": str(ex)}), file=sys.stderr)
        return 1

    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
