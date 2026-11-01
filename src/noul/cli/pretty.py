"""Markdown (`--pretty`) renderers for CLI results."""


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


def _format_pretty_expand(res):
    lines = [
        f"# Boolean query suggestions for: {res.get('original_query') or '(papers)'}",
        "",
        f"Acronyms: {', '.join(res['extracted_acronyms']) or '-'}",
        f"Emerging terms: {', '.join(res['emerging_terms']) or '-'}",
        "",
    ]
    for s in res["suggestions"]:
        lines += [f"[{s['index']}] {s['strategy']}", f"    {s['boolean_query']}", f"    {s['rationale']}"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_compare(res):
    lines = [
        "# Comparative matrix",
        "",
        res["markdown_table"],
        "",
        "## Convergence",
        res["convergence_summary"],
        "",
        "## Divergence",
        res["divergence_summary"],
    ]
    if res.get("warnings"):
        lines += ["", "> Warnings: " + "; ".join(res["warnings"])]
    return "\n".join(lines)


def _format_pretty_insights(res):
    lines = [f"# Thematic insights: {res['topic']}"]
    for title, key in (
        ("Consolidated consensuses", "consolidated_consensuses"),
        ("Methodological tensions", "methodological_tensions"),
        ("Emerging future work", "emerging_future_work"),
    ):
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
        lines += ["", "| " + " | ".join(_md_cell(h) for h in tb["headers"]) + " |", "|" + " --- |" * len(tb["headers"])]
        lines += ["| " + " | ".join(_md_cell(row.get(h)) for h in tb["headers"]) + " |" for row in tb["rows"]]
    best = res["normalized_summary"]["best_by_metric"]
    if best:
        lines += ["", "## Best per metric"] + [f"- {m}: {b['model']} = {b['value']}" for m, b in best.items()]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_hardware(res):
    active = f" ({res['active_parameters_billion']}B active)" if res.get("active_parameters_billion") else ""
    lines = [
        f"# Hardware feasibility: {res.get('title') or res['paper_id']}",
        f"Model: {res['total_parameters_billion']}B parameters{active} | Architecture: {res['architecture_type']}"
        f" | Tier: **{res['operational_tier']}**",
        f"Reported training hardware: {res.get('reported_training_hardware') or 'not reported'}",
        "",
        f"| Precision | Weights (GB) | KV cache @ {res['context_length']} tokens (GB) | Min. VRAM (GB) |",
        "| --- | --- | --- | --- |",
    ]
    for prec in ("fp16", "int8", "int4"):
        m = res[prec]
        lines.append(
            f"| {prec.upper()} | {m['weights_gb']} | {m['kv_cache_8k_context_gb']} | {m['recommended_min_vram_gb']} |"
        )
    lines += [""] + [f"- {n}" for n in res["notes"]]
    return "\n".join(lines)


def _format_pretty_ground(res):
    return res["rendered_markdown"]


def _format_pretty_math(res):
    lines = [
        f"# Math & algorithms: {res.get('title') or res['paper_id']}",
        f"Equations: {res['equation_count']} | Algorithms: {res['algorithm_count']} | Mode: {res['mode']}",
    ]
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
    lines += [
        "",
        "| Component | Metric | Reference | Variant | Δ | Δ% | Marginal |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {_md_cell(e['component'])} | {_md_cell(e['metric'])} | {e['baseline_value']} | {e['variant_value']} "
        f"| {e['delta']:+g} | {e['delta_percent']:+.2f}% | {'yes' if e['is_marginal'] else ''} |"
        for e in res["ablations"]
    ]
    if res["warnings_and_alerts"]:
        lines += ["", "## Alerts"] + [f"- {w}" for w in res["warnings_and_alerts"]]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_prompts(res):
    lines = [
        f"# Prompts & decoding: {res.get('title') or res['paper_id']}",
        f"Variables: {', '.join(res['variables']) or 'none'}",
    ]
    for p in res["prompts"]:
        lines += ["", f"## {p['index']}. {p['label']} ({p['type']}, page {p['page']})", "", "```text", p["text"], "```"]
    if res["hyperparameters"]:
        lines += ["", "## Decoding hyperparameters"] + [f"- {k}: {v}" for k, v in res["hyperparameters"].items()]
    if res["json_schemas"]:
        lines += ["", "## JSON schemas"]
        for js in res["json_schemas"]:
            lines += ["", "```json", js, "```"]
    lines += [f"> {w}" for w in res.get("warnings", [])]
    return "\n".join(lines)


def _format_pretty_failures(res):
    return res["rendered_markdown"]
