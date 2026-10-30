#!/usr/bin/env python3
"""CLI for humans and agents without MCP support. Prints JSON (or formatted Markdown with --pretty).
Usage:
  noul_cli.py search "query" [--year-min N] [--types rct,meta-analysis] [--sources a,b] [--limit N] [--offset N] [--domain D] [--pretty]
                    [--interactive] [--skip-clarification | --skip-clarify] [--export-obsidian ./vault]
  noul_cli.py clarify "query" [--domain D] [--answers '{"q1":"...","q2":2}']
  noul_cli.py paper ID_OR_DOI [--domain D]
  noul_cli.py domain ID_OR_DOI [--domain D]
  noul_cli.py artifacts ID_OR_DOI [--no-verify] [--fulltext]
  noul_cli.py references ID_OR_DOI [--limit 10] [--direction both|backward|forward] [--sort influential|citations|year]
  noul_cli.py compare ID1 ID2 ID3 | ID1,ID2,ID3 [--fulltext] [--pretty]
  noul_cli.py insights ID1 ID2 ... | ID1,ID2 [--topic "..."] [--pretty]
  noul_cli.py expand ["query"] [--ids ID1,ID2] [--run 1|2|3] [--limit N] [--pretty]
  noul_cli.py benchmarks ID_OR_DOI [--pretty]
  noul_cli.py hardware ID_OR_DOI [--params-b F] [--active-params-b F] [--context-length N] [--pretty]
  noul_cli.py ground ID_OR_DOI [--claims "claim 1; claim 2"] [--pretty]
  noul_cli.py math ID_OR_DOI [--mode latex|python|both] [--pretty]
  noul_cli.py ablation ID_OR_DOI [--pretty]
  noul_cli.py prompts ID_OR_DOI [--pretty]
  noul_cli.py fulltext ID_OR_DOI [--question Q]
  noul_cli.py ask ID1,ID2 --question Q
  noul_cli.py graph ID_OR_DOI
  noul_cli.py related ID_OR_DOI [--relation all|similar|references|citing] [--types ...] [--year-min N]
  noul_cli.py download ID1,ID2 [--dir ./papers]
  noul_cli.py export ID1,ID2 [--format bibtex|ris|csv|obsidian] [--vault-dir ./vault | --export-obsidian ./vault]
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


DOMAIN_HELP = "computer_science|biomedical|economics|general_stem (aliases: cs, bio, med, econ, stem)"


def _stdin_is_tty():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _interactive_clarification(query, domain=None):
    """Ask the clarification questions on stderr and read answers from stdin. Returns the refined scope or None
    when the query is already specific. Only called on an interactive terminal."""
    scope = t.clarify_search_scope(query, domain=domain)
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
    return t.clarify_search_scope(query, answers=answers, domain=domain)


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
            f"Citations: {p.get('citations', 0)} | Venue: {p.get('journal') or 'N/A'} | Domain: {p.get('domain') or 'N/A'}\n"
            f"    - Authors: {authors}\n"
            f"    - Abstract: {(p.get('abstract') or '')[:320]}..."
        )
    return "\n".join(lines)


def _split_ids(values):
    """`id1 id2` and/or `id1,id2` -> [id1, id2]."""
    return [x.strip() for v in values or [] for x in str(v).split(",") if x.strip()]


def _format_pretty_expand(res):
    lines = [f"# Boolean query suggestions for: {res.get('original_query') or '(papers)'}", "",
             f"Acronyms: {', '.join(res['extracted_acronyms']) or '-'}",
             f"Emerging terms: {', '.join(res['emerging_terms']) or '-'}", ""]
    for s in res["suggestions"]:
        lines += [f"[{s['index']}] {s['strategy']}", f"    {s['boolean_query']}", f"    {s['rationale']}"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_compare(res):
    lines = ["# Comparative matrix", "", res["markdown_table"], "",
             "## Convergence", res["convergence_summary"], "", "## Divergence", res["divergence_summary"]]
    if res.get("warnings"):
        lines += ["", "> Warnings: " + "; ".join(res["warnings"])]
    return "\n".join(lines)


def _format_pretty_insights(res):
    lines = [f"# Thematic insights: {res['topic']}"]
    for title, key in (("Consolidated consensuses", "consolidated_consensuses"),
                       ("Methodological tensions", "methodological_tensions"),
                       ("Emerging future work", "emerging_future_work")):
        lines += ["", f"## {title}"] + [f"- {x}" for x in res[key]]
    lines += ["", "## References"] + [f"- {tag}: {', '.join(ids)}" for tag, ids in res["citations_map"].items()]
    if res.get("warnings"):
        lines += ["", "> Warnings: " + "; ".join(res["warnings"])]
    return "\n".join(lines)


def _md_cell(value):
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _format_pretty_benchmarks(res):
    lines = [f"# Benchmark tables: {res.get('title') or res['paper_id']}", f"Source: {res['source']}"]
    for tb in res["tables"]:
        lines += ["", f"## {tb['label']} (page {tb['page']}, {tb['format']})"]
        if tb["caption"]:
            lines.append(f"_{tb['caption']}_")
        lines += ["", "| " + " | ".join(_md_cell(h) for h in tb["headers"]) + " |",
                  "|" + " --- |" * len(tb["headers"])]
        lines += ["| " + " | ".join(_md_cell(row.get(h)) for h in tb["headers"]) + " |" for row in tb["rows"]]
    best = res["normalized_summary"]["best_by_metric"]
    if best:
        lines += ["", "## Best per metric"] + [f"- {m}: {b['model']} = {b['value']}" for m, b in best.items()]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_hardware(res):
    active = f" ({res['active_parameters_billion']}B active)" if res.get("active_parameters_billion") else ""
    lines = [f"# Hardware feasibility: {res.get('title') or res['paper_id']}",
             f"Model: {res['total_parameters_billion']}B parameters{active} | Architecture: {res['architecture_type']}"
             f" | Tier: **{res['operational_tier']}**",
             f"Reported training hardware: {res.get('reported_training_hardware') or 'not reported'}", "",
             f"| Precision | Weights (GB) | KV cache @ {res['context_length']} tokens (GB) | Min. VRAM (GB) |",
             "| --- | --- | --- | --- |"]
    for prec in ("fp16", "int8", "int4"):
        m = res[prec]
        lines.append(f"| {prec.upper()} | {m['weights_gb']} | {m['kv_cache_8k_context_gb']} | "
                     f"{m['recommended_min_vram_gb']} |")
    lines += [""] + [f"- {n}" for n in res["notes"]]
    return "\n".join(lines)


_STATUS_ICON = {"verified": "✅", "partially_supported": "⚠️", "extrapolated": "❌"}


def _format_pretty_ground(res):
    s = res["summary"]
    lines = [f"# Claim grounding: {res.get('title') or res['paper_id']}",
             f"Verified: {s['verified']} | Partially supported: {s['partially_supported']} | "
             f"Extrapolated: {s['extrapolated']}", ""]
    for cl in res["claims"]:
        lines += [f"- {_STATUS_ICON.get(cl['status'], '')} **{cl['status']}** — {cl['claim']}",
                  f"  {cl['citation_tag']}"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_math(res):
    lines = [f"# Math & algorithms: {res.get('title') or res['paper_id']}",
             f"Equations: {res['equation_count']} | Algorithms: {res['algorithm_count']} | Mode: {res['mode']}"]
    for it in res["items"]:
        lines += ["", f"## {it['index']}. {it['label']} ({it['kind']})"]
        if it["latex"]:
            lines += ["", it["latex"]]
        if it["python_code"]:
            lines += ["", "```python", it["python_code"].rstrip(), "```"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_ablation(res):
    lines = [f"# Ablation analysis: {res.get('title') or res['paper_id']}"]
    ref = res.get("reference")
    if ref:
        vals = ", ".join(f"{k} = {v}" for k, v in ref["values"].items())
        lines.append(f"Reference: {ref['component']} ({vals})")
    lines += ["", "| Component | Metric | Reference | Variant | Δ | Δ% | Marginal |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {_md_cell(e['component'])} | {_md_cell(e['metric'])} | {e['baseline_value']} | {e['variant_value']} "
              f"| {e['delta']:+g} | {e['delta_percent']:+.2f}% | {'yes' if e['is_marginal'] else ''} |"
              for e in res["ablations"]]
    if res["warnings_and_alerts"]:
        lines += ["", "## Alerts"] + [f"- {w}" for w in res["warnings_and_alerts"]]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_prompts(res):
    lines = [f"# Prompts & decoding: {res.get('title') or res['paper_id']}",
             f"Variables: {', '.join(res['variables']) or 'none'}"]
    for p in res["prompts"]:
        lines += ["", f"## {p['index']}. {p['label']} ({p['type']}, page {p['page']})", "", "```text",
                  p["text"], "```"]
    if res["hyperparameters"]:
        lines += ["", "## Decoding hyperparameters"] + [f"- {k}: {v}" for k, v in res["hyperparameters"].items()]
    if res["json_schemas"]:
        lines += ["", "## JSON schemas"]
        for js in res["json_schemas"]:
            lines += ["", "```json", js, "```"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_failures(res):
    lines = [
        f"# Failure modes & critical assumptions: {res.get('title') or res['paper_id']}",
        f"Domain: `{res.get('domain', 'general_stem')}`",
        "",
        "## Failure Modes",
    ]
    for fm in res.get("failure_modes") or []:
        lines.append(
            f"- **[{fm['category']}]** (severity: `{fm['severity']}`) — {fm['description']}"
            + (f" _(trigger: {fm['trigger_condition']})_" if fm.get("trigger_condition") else "")
        )
    if not res.get("failure_modes"):
        lines.append("- None detected.")
    lines += ["", "## Critical Assumptions"]
    for a in res.get("critical_assumptions") or []:
        lines.append(f"- {a}")
    if not res.get("critical_assumptions"):
        lines.append("- None detected.")
    lines += ["", "## Baseline Superiority Scenarios"]
    for b in res.get("baseline_superiority_scenarios") or []:
        lines.append(f"- {b}")
    if not res.get("baseline_superiority_scenarios"):
        lines.append("- None detected.")
    if res.get("operational_limits"):
        lines += ["", "## Operational Limits"]
        for k, v in res["operational_limits"].items():
            lines.append(f"- `{k}`: {v}")
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
    s.add_argument("--domain", help=DOMAIN_HELP)
    s.add_argument("--pretty", action="store_true")
    s.add_argument("--interactive", action="store_true",
                   help="ask 2-3 scope questions on a terminal when the query is broad, then run the refined search")
    s.add_argument("--skip-clarification", "--skip-clarify", dest="skip_clarification", action="store_true",
                   help="never ask clarification questions (default for agents and non-interactive stdin)")
    s.add_argument("--export-obsidian", metavar="VAULT_DIR",
                   help="also export the returned papers as Obsidian notes into VAULT_DIR (inside the workspace)")

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
    fm_p = sub.add_parser("failures", help="Detect failure modes, critical assumptions and baseline superiority scenarios")
    fm_p.add_argument("id")
    fm_p.add_argument("--no-fulltext", action="store_true", help="analyse title/abstract only")
    fm_p.add_argument("--pretty", action="store_true")
    df_p = sub.add_parser("diff", help="Compare two arXiv revisions (e.g. v1 vs v2)")
    df_p.add_argument("arxiv_id")
    df_p.add_argument("--from", dest="from_version", default="v1", help="source version (default: v1)")
    df_p.add_argument("--to", dest="to_version", default="v2", help="target version (default: v2)")
    df_p.add_argument("--pretty", action="store_true")

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
    e.add_argument("--format", default=None, choices=["bibtex", "ris", "csv", "obsidian"],
                   help="default: bibtex (obsidian when --vault-dir/--export-obsidian is given)")
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
            out = t.search_papers(
                query, sources, year_min, types, a.min_citations,
                a.open_access, sort, a.limit, a.offset, domain=a.domain
            )
            if refined:
                out["clarification"] = {k: refined[k] for k in ("refined_query", "recommended_filters", "applied_answers")}
            if a.export_obsidian:
                ids = [p["id"] for p in out["papers"] if p.get("id")]
                out["obsidian_export"] = (t.export_obsidian_notes(ids, vault_dir=a.export_obsidian) if ids else
                                          {"vault_dir": None, "exported_count": 0, "notes": []})
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
                    raise ValueError(f"--answers must be a JSON object: {e}")
            out = t.clarify_search_scope(a.query, answers=answers, domain=a.domain)
        elif a.cmd == "paper":
            out = t.get_paper(a.id, domain=a.domain_override)
        elif a.cmd == "references":
            out = t.get_paper_references(a.id, limit=a.limit, direction=a.direction, sort_by=a.sort)
        elif a.cmd == "compare":
            out = t.compare_papers(_split_ids(a.ids), inspect_fulltext=a.fulltext)
            if a.pretty:
                print(_format_pretty_compare(out))
                return 0
        elif a.cmd == "insights":
            out = t.get_thematic_insights(_split_ids(a.ids), topic=a.topic)
            if a.pretty:
                print(_format_pretty_insights(out))
                return 0
        elif a.cmd == "expand":
            out = t.suggest_boolean_queries(a.query, paper_ids=_split_ids([a.ids]) if a.ids else None)
            if a.run:
                chosen = out["suggestions"][a.run - 1]
                res = t.search_papers(chosen["boolean_query"], limit=a.limit)
                out = {"applied_suggestion": chosen, "expansion": out, "search": res}
                if a.pretty:
                    print(f"> Running [{a.run}] {chosen['boolean_query']}\n")
                    print(_format_pretty_search(res))
                    return 0
            elif a.pretty:
                print(_format_pretty_expand(out))
                return 0
        elif a.cmd == "benchmarks":
            out = t.extract_benchmark_tables(a.id)
            if a.pretty:
                print(_format_pretty_benchmarks(out))
                return 0
        elif a.cmd == "hardware":
            out = t.estimate_hardware_specs(a.id, total_params_b=a.params_b, active_params_b=a.active_params_b,
                                            context_length=a.context_length)
            if a.pretty:
                print(_format_pretty_hardware(out))
                return 0
        elif a.cmd == "ground":
            claims = [x.strip() for x in a.claims.split(";") if x.strip()] if a.claims else None
            out = t.verify_paper_claims(a.id, claims=claims)
            if a.pretty:
                print(_format_pretty_ground(out))
                return 0
        elif a.cmd == "math":
            out = t.extract_paper_math(a.id, mode=a.mode)
            if a.pretty:
                print(_format_pretty_math(out))
                return 0
        elif a.cmd == "ablation":
            out = t.analyze_ablation_tables(a.id)
            if a.pretty:
                print(_format_pretty_ablation(out))
                return 0
        elif a.cmd == "prompts":
            out = t.extract_prompts_and_decoding(a.id)
            if a.pretty:
                print(_format_pretty_prompts(out))
                return 0
        elif a.cmd == "failures":
            out = t.detect_failure_modes(a.id, inspect_fulltext=not a.no_fulltext)
            if a.pretty:
                print(_format_pretty_failures(out))
                return 0
        elif a.cmd == "diff":
            out = t.diff_arxiv_versions(a.arxiv_id, from_version=a.from_version, to_version=a.to_version)
            if a.pretty:
                print(out["markdown_diff"])
                return 0
        elif a.cmd == "artifacts":
            out = t.scan_paper_artifacts(a.id, verify_http=not a.no_verify, inspect_fulltext=a.fulltext)
        elif a.cmd == "domain":
            out = t.detect_paper_domain(a.id, domain=a.domain_override)
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
            vault = a.vault_dir or a.export_obsidian
            if a.format == "obsidian" or vault:
                out = t.export_obsidian_notes(_split_ids(a.ids), vault_dir=vault)
            else:
                out = t.export_papers(_split_ids(a.ids), a.format or "bibtex")
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
