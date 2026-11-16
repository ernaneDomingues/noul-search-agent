"""The `noul` command table: one `Command` per (sub)command, holding its arguments, its handler and, when the
command has a Markdown view, its `--pretty` formatter. `noul.cli.main` builds the parser from this table."""

from __future__ import annotations

import argparse
import getpass
import inspect
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noul import config as _config
from noul import registry
from noul.cli import pretty as _pretty

Namespace = argparse.Namespace
Argument = tuple[tuple[str, ...], dict[str, Any]]

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
VENUES_HELP = "Target venue (neurips, icml, iclr, acl, ieee, nature, lancet, aer, general)"
WORKSPACE_DIR_HELP = "Inside the workspace (default: configured {})"
STATUSES = ("candidate", "included", "excluded")
EXPORT_STATUSES = ["included", "candidate", "excluded", "all"]


@dataclass(frozen=True)
class Command:
    """One CLI command. `path` is its words (`"search"`, `"project add"`); `run` returns the tool result, which
    is printed as JSON unless `--pretty` was given and the command has a `pretty` formatter."""

    path: str
    args: tuple[Argument, ...]
    run: Callable[[Namespace], Any]
    pretty: Callable[[Any, Namespace], str] | None = None
    help: str | None = None
    setup: Callable[[argparse.ArgumentParser], None] | None = None  # for what `args` cannot express


def arg(*flags: str, **options: Any) -> Argument:
    return flags, options


PRETTY = arg("--pretty", action="store_true")
ID = arg("id")
MANY_IDS = arg("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated)")
OUTPUT_FORMAT = arg("--format", dest="output_format", choices=["markdown", "latex"], default="markdown")


# -----------------------------------------------------------------------------------------------------------------
# Helpers


def _run(name, *args, **kwargs):
    """Run a tool through the registry (validation + logging), binding positional arguments by name."""
    bound = inspect.signature(registry.TOOLS[name][3]).bind_partial(*args, **kwargs)
    return registry.call(name, dict(bound.arguments))


def _split(value):
    """`id1,id2` -> [id1, id2]; None for an empty value (the tool's default applies)."""
    return [x.strip() for x in value.split(",") if x.strip()] if value else None


def _split_ids(values):
    """`id1 id2` and/or `id1,id2` -> [id1, id2]."""
    return [x.strip() for v in values or [] for x in str(v).split(",") if x.strip()]


def _split_semicolons(value):
    return [x.strip() for x in value.split(";") if x.strip()] if value else None


def _json_option(raw, flag):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{flag} must be a JSON object: {e}") from e


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
    """`email:you@x.org workspace:~/w` -> settings for `noul config`. A bare value containing '@' is the email."""
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


def _load_meter_items(value):
    raw = sys.stdin.read() if value == "-" else value
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"meter expects a JSON list of {{id, stance, evidence?}} objects: {e}") from None
    return data.get("items") if isinstance(data, dict) else data


def _formatted(formatter):
    """A `pretty` that only needs the result."""
    return lambda out, a: formatter(out)


def _field(key):
    """A `pretty` that prints the Markdown the tool already rendered under `key`."""
    return lambda out, a: out[key]


# -----------------------------------------------------------------------------------------------------------------
# Handlers and formatters of the commands that are more than one tool call


def _search(a):
    query, year_min, types, sources, sort = a.query, a.year_min, _split(a.types), _split(a.sources), a.sort
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
        project=a.project,
    )
    if refined:
        out["clarification"] = {k: refined[k] for k in ("refined_query", "recommended_filters", "applied_answers")}
    if a.export_obsidian:
        ids = [p["id"] for p in out["papers"] if p.get("id")]
        out["obsidian_export"] = (
            _run("export_obsidian_notes", ids, vault_dir=a.export_obsidian)
            if ids
            else {"vault_dir": None, "exported_count": 0, "notes": []}
        )
    return out


def _pretty_search(out, a):
    text = _pretty._format_pretty_search(out)
    if a.export_obsidian:
        exp = out["obsidian_export"]
        text += f"\n\n> Exported {exp['exported_count']} Obsidian note(s) to {exp['vault_dir']}"
    return text


def _clarify(a):
    answers = _json_option(a.answers, "--answers") if a.answers else None
    return _run("clarify_search_scope", a.query, answers=answers, domain=a.domain)


def _expand(a):
    out = _run("suggest_boolean_queries", a.query, paper_ids=_split_ids([a.ids]) if a.ids else None)
    if not a.run:
        return out
    chosen = out["suggestions"][a.run - 1]
    res = _run("search_papers", chosen["boolean_query"], limit=a.limit)
    return {"applied_suggestion": chosen, "expansion": out, "search": res}


def _pretty_expand(out, a):
    if not a.run:
        return _pretty._format_pretty_expand(out)
    query = out["applied_suggestion"]["boolean_query"]
    return f"> Running [{a.run}] {query}\n\n" + _pretty._format_pretty_search(out["search"])


def _pretty_scaffold(out, a):
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
    return "\n".join(lines)


def _pretty_cite(out, a):
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
    return "\n".join(lines)


def _pretty_related_work(out, a):
    text = out["rendered_related_work"]
    if out.get("bibtex_block"):
        text += "\n\n```bibtex\n" + out["bibtex_block"] + "\n```"
    return text


def _export(a):
    vault = a.vault_dir or a.export_obsidian
    if a.project and (vault or a.format == "obsidian"):
        raise ValueError("Obsidian export takes paper ids, not --project")
    if a.format == "obsidian" or vault:
        return _run("export_obsidian_notes", _split_ids(a.ids), vault_dir=vault)
    return _run(
        "export_papers", ids=_split_ids(a.ids) or None, format=a.format or "bibtex", project=a.project, status=a.status
    )


def _dossier_save(a):
    markdown = Path(a.file).read_text(encoding="utf-8") if a.file else a.text
    return _run("save_research_dossier", a.topic, markdown, _split(a.ids), a.dir)


def _project_delete(a):
    out = _run("project_delete", a.project)  # the preview
    if a.yes:
        return _run("project_delete", a.project, True, out["preview_token"])
    for key in ("next_step", "preview_token"):  # for a client model's second call
        out.pop(key, None)
    print("Nothing deleted: run the command again with --yes to delete this project.", file=sys.stderr)
    return out


def _project_show(a):
    if a.pretty:
        from noul import api as _api

        return _api.project_markdown(a.project)
    return _run("project_get", a.project)


def _config_command(a):
    kwargs = _parse_config_pairs(a.pairs)
    for key in ("email", "workspace", "download_dir", "research_dir"):
        if getattr(a, key) is not None:
            kwargs[key] = getattr(a, key)
    if a.clear:
        kwargs["clear"] = True
    zotero = a.zotero_api_key_stdin or a.zotero_remove_key or a.zotero_user_id is not None
    if kwargs.get("clear") and (zotero or len(kwargs) > 1):
        raise ValueError("--clear deletes the settings given with it; nothing changed")
    if a.zotero_user_id is not None:
        kwargs["zotero_user_id"] = a.zotero_user_id
    if a.zotero_remove_key:
        kwargs["zotero_api_key"] = ""
    if a.zotero_api_key_stdin:
        # No echo at a terminal. An empty value here is a failed pipe, not a request to remove the key.
        key = getpass.getpass("Zotero API key: ") if sys.stdin.isatty() else sys.stdin.readline()
        if not key.strip():
            raise ValueError("No Zotero API key on stdin; nothing changed")
        kwargs["zotero_api_key"] = key.strip()
    if zotero or kwargs.get("clear") or "workspace" in kwargs:
        # Not through the tool, which keeps these out of reach of a model that read a hostile paper.
        out = _config.set_config(**kwargs)
    else:
        out = _run("configure_noul", **kwargs)
    for changed, variable in (
        (a.zotero_remove_key or a.zotero_api_key_stdin, "NOUL_ZOTERO_API_KEY"),
        (a.zotero_user_id is not None, "NOUL_ZOTERO_USER_ID"),
        ("workspace" in kwargs, "NOUL_WORKSPACE"),
    ):
        if changed and os.environ.get(variable):
            print(f"warning: {variable} is set and takes precedence over the stored value", file=sys.stderr)
    return out


def _config_key_options(parser):
    zotero_key = parser.add_mutually_exclusive_group()
    zotero_key.add_argument(
        "--zotero-api-key-stdin",
        action="store_true",
        help="Read the Zotero API key from stdin (a command-line argument would stay in the shell history)",
    )
    zotero_key.add_argument("--zotero-remove-key", action="store_true", help="Remove the stored Zotero API key")
    parser.add_argument("--clear", action="store_true", help="Delete the config file (env vars still apply)")


def _tool(a):
    if a.list or not a.name:
        return {"tools": list(registry.TOOLS)}
    return registry.call(a.name, _json_option(sys.stdin.read() if a.args == "-" else a.args, "--args"))


# -----------------------------------------------------------------------------------------------------------------
# The table (in the order `noul --help` lists the commands)

GROUPS: dict[str, str | None] = {
    "draft": "IMRaD manuscript blueprint generator and draft auditor",
    "bibtex": "BibTeX sanitizer and preprint-to-published resolver",
    "rebuttal": "Peer-review Rebuttal Copilot",
    "gatekeeper": "Pre-submission & desk-reject gatekeeper",
    "dossier": None,
    "project": "Persistent research projects (question, papers with status, notes)",
}

COMMANDS: tuple[Command, ...] = (
    Command(
        "search",
        (
            arg("query"),
            arg("--year-min", type=int),
            arg("--types"),
            arg("--sources"),
            arg("--min-citations", type=int),
            arg("--open-access", action="store_true"),
            arg("--sort", choices=["relevance", "date", "citations"]),
            arg("--limit", type=int, default=15, help="papers per page, 1-40 (default: 15)"),
            arg("--offset", type=int, default=0, help="results to skip, >= 0"),
            arg("--domain", help=DOMAIN_HELP),
            arg("--project", help="Log the search in this research project and add the results as candidates"),
            PRETTY,
            arg(
                "--interactive",
                action="store_true",
                help="ask 2-3 scope questions on a terminal when the query is broad, then run the refined search",
            ),
            arg(
                "--skip-clarification",
                "--skip-clarify",
                dest="skip_clarification",
                action="store_true",
                help="never ask clarification questions (default for agents and non-interactive stdin)",
            ),
            arg(
                "--export-obsidian",
                metavar="VAULT_DIR",
                help="also export the returned papers as Obsidian notes into VAULT_DIR (inside the workspace)",
            ),
        ),
        _search,
        _pretty_search,
    ),
    Command(
        "clarify",
        (
            arg("query"),
            arg("--domain", help=DOMAIN_HELP),
            arg("--answers", help='JSON object, e.g. \'{"q1":"Benchmarks and empirical evaluation","q3":1}\''),
        ),
        _clarify,
    ),
    Command(
        "artifacts",
        (
            ID,
            arg("--no-verify", action="store_true", help="skip the HTTP liveness check"),
            arg("--fulltext", action="store_true", help="also scan the open-access full text"),
        ),
        lambda a: _run("scan_paper_artifacts", a.id, verify_http=not a.no_verify, inspect_fulltext=a.fulltext),
    ),
    Command(
        "references",
        (
            ID,
            arg("--limit", type=int, default=10),
            arg("--direction", choices=["both", "backward", "forward"], default="both"),
            arg("--sort", choices=["influential", "citations", "year"], default="influential"),
        ),
        lambda a: _run("get_paper_references", a.id, limit=a.limit, direction=a.direction, sort_by=a.sort),
    ),
    Command(
        "compare",
        (MANY_IDS, arg("--fulltext", action="store_true", help="also mine the open-access full text"), PRETTY),
        lambda a: _run("compare_papers", _split_ids(a.ids), inspect_fulltext=a.fulltext),
        _formatted(_pretty._format_pretty_compare),
        help="Comparative matrix of 1-10 papers",
    ),
    Command(
        "insights",
        (MANY_IDS, arg("--topic"), PRETTY),
        lambda a: _run("get_thematic_insights", _split_ids(a.ids), topic=a.topic),
        _formatted(_pretty._format_pretty_insights),
        help="Thematic insights (consensuses, tensions, future work) of 1-20 papers",
    ),
    Command(
        "hypotheses",
        (
            arg("ids", nargs="+", help="Paper IDs/DOIs (space- or comma-separated, at least 2)"),
            arg("--topic"),
            arg(
                "--max",
                dest="max_hypotheses",
                type=int,
                default=3,
                help="maximum hypotheses to generate, 1-10 (default: 3)",
            ),
            PRETTY,
        ),
        lambda a: _run(
            "generate_research_hypotheses", _split_ids(a.ids), topic=a.topic, max_hypotheses=a.max_hypotheses
        ),
        _field("markdown_report"),
        help="Cross-analyze >= 2 papers to detect research gaps and generate hypotheses",
    ),
    Command(
        "expand",
        (
            arg("query", nargs="?", default=""),
            arg("--ids", help="Paper IDs/DOIs (comma-separated) to mine terms from"),
            arg("--run", type=int, choices=[1, 2, 3], help="run suggestion N with search_papers"),
            arg("--limit", type=int, default=10),
            PRETTY,
        ),
        _expand,
        _pretty_expand,
        help="Suggest 3 boolean queries (AND/OR/NOT) from a query and/or papers",
    ),
    Command(
        "benchmarks",
        (ID, PRETTY),
        lambda a: _run("extract_benchmark_tables", a.id),
        _formatted(_pretty._format_pretty_benchmarks),
        help="Extract and normalize the benchmark tables of a paper",
    ),
    Command(
        "hardware",
        (
            ID,
            arg("--params-b", type=float, help="total parameters in billions (overrides detection)"),
            arg("--active-params-b", type=float, help="active parameters per token in billions (MoE)"),
            arg("--context-length", type=int, default=8192),
            PRETTY,
        ),
        lambda a: _run(
            "estimate_hardware_specs",
            a.id,
            total_params_b=a.params_b,
            active_params_b=a.active_params_b,
            context_length=a.context_length,
        ),
        _formatted(_pretty._format_pretty_hardware),
        help="Estimate inference VRAM (FP16/INT8/INT4) and hardware tier of a paper",
    ),
    Command(
        "ground",
        (
            ID,
            arg("--claims", help='semicolon-separated claims, e.g. "claim 1; claim 2" (default: auto-extract)'),
            PRETTY,
        ),
        lambda a: _run("verify_paper_claims", a.id, claims=_split_semicolons(a.claims)),
        _formatted(_pretty._format_pretty_ground),
        help="Verify claims against the paper's literal text (strict citations)",
    ),
    Command(
        "math",
        (ID, arg("--mode", choices=["latex", "python", "both"], default="latex"), PRETTY),
        lambda a: _run("extract_paper_math", a.id, mode=a.mode),
        _formatted(_pretty._format_pretty_math),
        help="Extract equations/algorithms as LaTeX and/or typed Python functions",
    ),
    Command(
        "ablation",
        (ID, PRETTY),
        lambda a: _run("analyze_ablation_tables", a.id),
        _formatted(_pretty._format_pretty_ablation),
        help="Rank ablated components by impact (delta, delta %%, marginal/dominant)",
    ),
    Command(
        "prompts",
        (ID, PRETTY),
        lambda a: _run("extract_prompts_and_decoding", a.id),
        _formatted(_pretty._format_pretty_prompts),
        help="Extract prompt templates, decoding hyperparameters and JSON schemas",
    ),
    Command(
        "failures",
        (ID, arg("--no-fulltext", action="store_true", help="analyse title/abstract only"), PRETTY),
        lambda a: _run("detect_failure_modes", a.id, inspect_fulltext=not a.no_fulltext),
        _formatted(_pretty._format_pretty_failures),
        help="Detect failure modes, critical assumptions and baseline superiority scenarios",
    ),
    Command(
        "diff",
        (
            arg("arxiv_id"),
            arg("--from", dest="from_version", default="v1", help="source version (default: v1)"),
            arg("--to", dest="to_version", default="v2", help="target version (default: v2)"),
            PRETTY,
        ),
        lambda a: _run("diff_arxiv_versions", a.arxiv_id, from_version=a.from_version, to_version=a.to_version),
        _field("markdown_diff"),
        help="Compare two arXiv revisions (e.g. v1 vs v2)",
    ),
    Command(
        "review",
        (
            ID,
            arg("--domain", help=DOMAIN_HELP),
            arg("--no-fulltext", action="store_true", help="analyse title/abstract only"),
            PRETTY,
        ),
        lambda a: _run("review_paper", a.id, domain=a.domain, inspect_fulltext=not a.no_fulltext),
        _field("rendered_markdown"),
        help="Multi-persona domain-adaptive peer review of a paper",
    ),
    Command(
        "scaffold",
        (
            ID,
            arg("--target", help="pytorch|statsmodels|r|protocol (default: auto from domain)"),
            arg("--domain", help=DOMAIN_HELP),
            PRETTY,
        ),
        lambda a: _run("scaffold_implementation", a.id, target=a.target, domain=a.domain),
        _pretty_scaffold,
        help="Generate PyTorch, Statsmodels/R, or Clinical Protocol scaffold + unit tests",
    ),
    Command(
        "cite",
        (
            arg("draft_text", help="Draft paragraph or path to .md/.tex file inside workspace"),
            arg("--limit-per-claim", type=int, default=2, help="max papers recommended per claim (default: 2)"),
            arg("--style", choices=["latex", "markdown"], default="latex"),
            PRETTY,
        ),
        lambda a: _run("recommend_citations", a.draft_text, max_per_claim=a.limit_per_claim, citation_style=a.style),
        _pretty_cite,
        help="Contextual citation recommender and BibTeX infiller",
    ),
    Command(
        "related-work",
        (
            MANY_IDS,
            arg("--proposal", required=True, help="Summary of your proposed contribution/innovation"),
            arg("--style", choices=["latex", "numeric"], default="latex"),
            OUTPUT_FORMAT,
            PRETTY,
        ),
        lambda a: _run(
            "synthesize_related_work",
            _split_ids(a.ids),
            proposal=a.proposal,
            citation_style=a.style,
            output_format=a.output_format,
        ),
        _pretty_related_work,
        help="Synthesize comparative Related Work section from papers",
    ),
    Command(
        "draft structure",
        (
            arg("--topic", required=True, help="Manuscript topic or working title"),
            arg("--venue", default="neurips", help=VENUES_HELP),
            arg("--hypothesis", default="", help="Central hypothesis"),
            arg("--findings", default="", help="Semicolon-separated key findings"),
            OUTPUT_FORMAT,
            PRETTY,
        ),
        lambda a: _run(
            "generate_manuscript_blueprint",
            a.topic,
            venue=a.venue,
            hypothesis=a.hypothesis,
            key_findings=_split_semicolons(a.findings),
            output_format=a.output_format,
        ),
        _field("rendered_template"),
        help="Generate IMRaD manuscript blueprint",
    ),
    Command(
        "draft audit",
        (
            arg("draft_input", help="Path to .md/.tex draft in workspace or inline text"),
            arg("--venue", default="neurips"),
            OUTPUT_FORMAT,
            PRETTY,
        ),
        lambda a: _run("audit_manuscript_draft", a.draft_input, venue=a.venue, output_format=a.output_format),
        _field("rendered_report"),
        help="Audit draft manuscript for weak claims, notation, and reproducibility",
    ),
    Command(
        "bibtex sanitize",
        (
            arg("bibtex_input", help="Path to .bib file in workspace or raw BibTeX string"),
            arg("--update-preprints", dest="update_preprints", action="store_true", default=True),
            arg("--no-update-preprints", dest="update_preprints", action="store_false"),
            arg("--output", dest="output_path", default=None, help="Output .bib path inside workspace"),
            PRETTY,
        ),
        lambda a: _run(
            "sanitize_bibtex", a.bibtex_input, update_preprints=a.update_preprints, output_path=a.output_path
        ),
        _field("sanitized_bibtex"),
        help="Sanitize .bib file/text and upgrade arXiv preprints",
    ),
    Command(
        "rebuttal parse",
        (
            arg("reviews_input", help="Path to reviews.txt/.md in workspace or raw reviews text"),
            arg("--paper-id", default=None, help="Optional cached paper ID for grounding"),
            arg("--draft", dest="draft_input", default=None, help="Optional path to manuscript draft"),
            OUTPUT_FORMAT,
            PRETTY,
        ),
        lambda a: _run(
            "parse_and_draft_rebuttal",
            a.reviews_input,
            paper_id=a.paper_id,
            draft_input=a.draft_input,
            output_format=a.output_format,
        ),
        lambda out, a: out["latex_rebuttal"] if a.output_format == "latex" else out["markdown_rebuttal"],
        help="Parse reviewer comments and draft structured rebuttal",
    ),
    Command(
        "gatekeeper audit",
        (
            arg("draft_path", help="Path to .tex, .md, or .pdf inside workspace (or inline text)"),
            arg("--venue", default="neurips", help="Target venue (default: neurips)"),
            PRETTY,
        ),
        lambda a: _run("audit_submission_readiness", a.draft_path, venue=a.venue),
        _field("rendered_markdown"),
        help="Audit manuscript readiness (anonymity, venue checklist, overclaiming)",
    ),
    Command(
        "paper",
        (ID, arg("--domain", dest="domain_override", help=DOMAIN_HELP)),
        lambda a: _run("get_paper", a.id, domain=a.domain_override),
    ),
    Command("graph", (ID,), lambda a: _run("citation_graph", a.id)),
    Command("fulltext", (ID, arg("--question", default="")), lambda a: _run("get_fulltext", a.id, a.question)),
    Command(
        "domain",
        (ID, arg("--domain", dest="domain_override", help=DOMAIN_HELP)),
        lambda a: _run("detect_paper_domain", a.id, domain=a.domain_override),
    ),
    Command(
        "ask",
        (
            arg("ids", help="Comma-separated paper IDs or DOIs"),
            arg("--question", required=True),
            arg("--chunks", type=int, default=4, help="passages per paper, 1-10 (default: 4)"),
        ),
        lambda a: _run("query_fulltexts", _split(a.ids), a.question, a.chunks),
    ),
    Command(
        "related",
        (
            ID,
            arg("--relation", default="all", choices=["all", "similar", "references", "citing"]),
            arg("--types"),
            arg("--year-min", type=int),
            arg("--limit", type=int, default=15, help="1-40 (default: 15)"),
        ),
        lambda a: _run("related_papers", a.id, a.relation, _split(a.types), a.year_min, a.limit),
    ),
    Command(
        "download",
        (
            arg("ids", help="Comma-separated paper IDs or DOIs"),
            arg("--dir", default=None, help=WORKSPACE_DIR_HELP.format("download_dir")),
        ),
        lambda a: _run("download_papers", _split(a.ids), a.dir),
    ),
    Command(
        "export",
        (
            arg("ids", nargs="*", help="Paper IDs/DOIs (space- or comma-separated)"),
            arg("--project", help="Export this research project instead of IDs"),
            arg("--status", choices=EXPORT_STATUSES, help="With --project"),
            arg(
                "--format",
                default=None,
                choices=["bibtex", "ris", "csv", "csl-json", "obsidian"],
                help="default: bibtex (obsidian when --vault-dir/--export-obsidian is given)",
            ),
            arg("--vault-dir", help="Obsidian vault folder inside the workspace (implies --format obsidian)"),
            arg("--export-obsidian", metavar="VAULT_DIR", help="alias of --vault-dir"),
        ),
        _export,
    ),
    Command(
        "zotero",
        (
            arg("ids", nargs="*", help="Paper IDs/DOIs (space- or comma-separated)"),
            arg("--project", help="Send this research project instead of IDs"),
            arg("--status", choices=EXPORT_STATUSES, help="With --project"),
            arg("--collection", help="Existing collection, by exact name or key"),
        ),
        lambda a: _run(
            "zotero_push", ids=_split_ids(a.ids) or None, project=a.project, status=a.status, collection=a.collection
        ),
        help="Create papers in your Zotero library (needs credentials: see noul config)",
    ),
    Command(
        "dossier save",
        (
            arg("--topic", required=True),
            arg("--text", default="", help="Markdown synthesis string"),
            arg("--file", default="", help="Path to Markdown file"),
            arg("--ids", default="", help="Comma-separated paper IDs"),
            arg("--dir", default=None, help=WORKSPACE_DIR_HELP.format("research_dir")),
        ),
        _dossier_save,
    ),
    Command(
        "dossier list",
        (arg("--dir", default=None, help=WORKSPACE_DIR_HELP.format("research_dir")),),
        lambda a: _run("list_research_dossiers", a.dir),
    ),
    Command(
        "dossier show",
        (arg("slug"), arg("--dir", default=None, help=WORKSPACE_DIR_HELP.format("research_dir"))),
        lambda a: _run("get_research_dossier", a.slug, a.dir),
    ),
    Command(
        "project create",
        (arg("title"), arg("--question", default=None)),
        lambda a: _run("project_create", a.title, a.question),
    ),
    Command(
        "project list",
        (arg("--all", action="store_true", help="Include archived projects"),),
        lambda a: _run("project_list", a.all),
    ),
    Command(
        "project archive",
        (arg("project"), arg("--undo", action="store_true", help="Restore an archived project")),
        lambda a: _run("project_archive", a.project, not a.undo),
        help="Hide a project from the list (nothing is deleted)",
    ),
    Command(
        "project delete",
        (
            arg("project", help="The exact project slug"),
            arg("--yes", action="store_true", help="Delete; without it, only show what would be deleted"),
        ),
        _project_delete,
        help="Permanently delete a project (statuses, notes, search history)",
    ),
    Command(
        "project show",
        (arg("project"), arg("--pretty", action="store_true", help="Markdown summary")),
        _project_show,
    ),
    Command(
        "project add",
        (
            arg("project"),
            arg("ids", help="Comma-separated paper ids / DOIs"),
            arg("--status", choices=STATUSES, default="candidate"),
            arg("--reason", default=None),
        ),
        lambda a: _run("project_add_papers", a.project, _split(a.ids), a.status, a.reason),
    ),
    Command(
        "project status",
        (
            arg("project"),
            arg("ids", help="Comma-separated paper ids"),
            arg("status", choices=STATUSES),
            arg("--reason", required=True),
        ),
        lambda a: _run("project_set_status", a.project, _split(a.ids), a.status, a.reason),
    ),
    Command(
        "project note",
        (arg("project"), arg("text"), arg("--paper", default=None)),
        lambda a: _run("project_note", a.project, a.text, a.paper),
    ),
    Command(
        "config",
        (
            arg("pairs", nargs="*", metavar="KEY:VALUE", help="e.g. email:you@university.edu workspace:~/research"),
            arg("--email"),
            arg("--workspace"),
            arg("--download-dir"),
            arg("--research-dir"),
            arg("--zotero-user-id"),
        ),
        _config_command,
        help="View or update persistent settings (no arguments = show)",
        setup=_config_key_options,
    ),
    Command(
        "meter",
        (arg("items", help='JSON list like [{"id":"...","stance":"supportive"}], or - to read stdin'),),
        lambda a: _run("evidence_meter", _load_meter_items(a.items)),
        help="Deterministic Evidence Meter from classified papers",
    ),
    Command(
        "tool",
        (
            arg("name", nargs="?", help="Tool name (omit with --list)"),
            arg("--args", default="{}", help='JSON object of arguments, e.g. \'{"query": "..."}\', or - for stdin'),
            arg("--list", action="store_true", help="List the tool names and exit"),
        ),
        _tool,
        help="Run any MCP tool by name with JSON arguments (same contract as MCP)",
    ),
)


def add_commands(subparsers: Any) -> None:
    """Register every command of the table on `subparsers`; a parsed namespace carries its `Command` as
    `_command`. Grouped commands (`project add`) get a sub-subparser whose choice is stored as `<group>_cmd`."""
    groups: dict[str, Any] = {}
    for command in COMMANDS:
        group, _, leaf = command.path.rpartition(" ")
        options = {"help": command.help} if command.help is not None else {}
        if group and group not in groups:
            group_help = {"help": GROUPS[group]} if GROUPS[group] is not None else {}
            parent = subparsers.add_parser(group, **group_help)
            groups[group] = parent.add_subparsers(dest=f"{group}_cmd", required=True)
        parser = (groups[group] if group else subparsers).add_parser(leaf, **options)
        for flags, kwargs in command.args:
            parser.add_argument(*flags, **kwargs)
        if command.setup is not None:
            command.setup(parser)
        parser.set_defaults(_command=command)
