"""Ablation tables: the contribution of each removed component, with alerts on marginal or implausible gains (`analyze_ablation_tables`)."""

import re

from noul.providers import pdf_parser
from noul.tools.technical import common as _common
from noul.tools.technical import tables as _tables

MARGINAL_PCT = 1.0


MAX_PLAUSIBLE_PCT = 300.0


DOMINANT_SHARE = 0.6


_ABL_VARIANT = re.compile(
    r"^\s*(?:w/o|w/|without|no|minus|remove[sd]?|removing|drop(?:ped)?|replace[sd]?|only|with)\b|^\s*[-+−–]\s*\S"
    r"|^\s*\([A-H]\)(?:\s|$)",  # lettered variant groups: "(A) 1 512 ..." in "Model variations" tables
    re.I,
)


_ABL_FULL = re.compile(r"\b(?:full(?:\s+model)?|ours|complete|proposed|final|all\s+components)\b", re.I)


_ABL_BASE = re.compile(r"^\s*(?:baseline|base(?:\s+model)?|vanilla|backbone)\b", re.I)


_ABL_LINE = re.compile(
    r"^[ \t]*(?:[*•][ \t]+)?(?P<label>[^:=\n]{2,80}?)[ \t]*[:=][ \t]*(?P<num>[-+−]?\d+(?:\.\d+)?)[ \t]*%?"
    r"[ \t]*(?:\((?P<metric>[^()\n]{1,40})\))?[ \t]*[.;,]?[ \t]*$",
    re.M,
)


def _abl_role(label):
    if _ABL_BASE.search(label):
        return "baseline"
    if _ABL_VARIANT.search(label):
        return "variant"
    if _ABL_FULL.search(label):
        return "full"
    return None


def _list_ablations(text):
    """`Full model: 75.3` / `w/o KL loss: 71.2` lines grouped into consecutive blocks."""
    blocks, cur, last_end = [], [], -1
    for m in _ABL_LINE.finditer(text):
        label = m.group("label").strip()
        if label.startswith("- ") and _abl_role(label[2:]):
            label = label[2:].strip()
        if cur and text[last_end : m.start()].strip():
            blocks.append(cur)
            cur = []
        cur.append(
            (
                m.start(),
                label,
                _tables.parse_number(m.group("num").replace("−", "-")),
                _tables.normalize_metric_name(m.group("metric")) if m.group("metric") else "score",
            )
        )
        last_end = m.end()
    if cur:
        blocks.append(cur)
    out = []
    for block in blocks:
        roles = [_abl_role(lbl) for _, lbl, _, _ in block]
        if len(block) < 2 or "variant" not in roles or not ({"full", "baseline"} & set(roles)):
            continue
        metrics = {}
        for _, lbl, val, metric in block:
            metrics.setdefault(lbl, {})[metric] = val
        out.append(
            {
                "table_id": f"list_{len(out) + 1}",
                "label": "Ablation list",
                "caption": "",
                "format": "list",
                "start": block[0][0],
                "normalized_metrics": metrics,
            }
        )
    return out


def _is_ablation(table):
    labels = list(table["normalized_metrics"])
    roles = [_abl_role(lbl) for lbl in labels]
    if len(labels) < 2:
        return False
    if re.search(r"ablat|component|contribution|variations?\b|variants?\b", table.get("caption") or "", re.I):
        return True
    return "variant" in roles and ({"full", "baseline"} & set(roles) or roles.count("variant") >= 2)


def _ablation_entries(table):
    from noul.schemas.technical import AblationEntry

    metrics = table["normalized_metrics"]
    labels = list(metrics)
    roles = {lbl: _abl_role(lbl) for lbl in labels}
    full = next((lbl for lbl in labels if roles[lbl] == "full"), None)
    base = next((lbl for lbl in labels if roles[lbl] == "baseline"), None)
    ref = full or base or labels[0]
    gain_ref = base if full and base else None
    entries = []
    for lbl in labels:
        if lbl in (ref, gain_ref):
            continue
        for metric, b in metrics[ref].items():
            v = metrics[lbl].get(metric)
            if not (_tables._is_number(b) and _tables._is_number(v)):
                continue
            delta = round(v - b, 4)
            pct = round(delta / abs(b) * 100.0, 2) if b else 0.0
            if abs(pct) > MAX_PLAUSIBLE_PCT:
                continue  # a misaligned column (e.g. "train steps" read as a metric), not an ablation effect
            entries.append(
                AblationEntry(
                    component=lbl,
                    metric=metric,
                    baseline_value=float(b),
                    variant_value=float(v),
                    delta=delta,
                    delta_percent=pct,
                    is_marginal=abs(pct) < MARGINAL_PCT,
                ).model_dump()
            )
    entries.sort(key=lambda e: -abs(e["delta_percent"]))
    reference = {
        "component": ref,
        "role": "full_model" if ref == full else "baseline" if ref == base else "first_row",
        "values": dict(metrics[ref]),
        "baseline": {"component": gain_ref, "values": dict(metrics[gain_ref])} if gain_ref else None,
    }
    return entries, reference


def _ablation_alerts(entries, reference, table_label):
    alerts = []
    marginal = {}
    for e in entries:
        if e["is_marginal"]:
            marginal.setdefault(e["component"], []).append(f"{e['metric']} Δ% = {e['delta_percent']:+.2f}%")
    for comp, info in marginal.items():
        alerts.append(
            f"[{table_label}] Marginal component: '{comp}' changes the result by less than 1% "
            f"({'; '.join(info)}); the contribution may be within experimental noise."
        )
    by_metric = {}
    for e in entries:
        by_metric.setdefault(e["metric"], []).append(e)
    base = reference["baseline"]
    for metric, group in by_metric.items():
        if base and _tables._is_number(base["values"].get(metric)):
            total = abs(reference["values"][metric] - base["values"][metric])
            basis = f"total gain {reference['component']} vs {base['component']}"
        elif len(group) >= 2:
            total = sum(abs(e["delta"]) for e in group)
            basis = "sum of the changes of all components"
        else:
            continue
        if not total:
            continue
        top = max(group, key=lambda e: abs(e["delta"]))
        share = abs(top["delta"]) / total
        if share >= DOMINANT_SHARE:
            alerts.append(
                f"[{table_label}] Dominant component: '{top['component']}' accounts for {share:.0%} "
                f"of the gain in {metric} ({basis} = {total:.4g}); the other components play a secondary role."
            )
    return alerts


def analyze_ablation_tables(paper_id):
    """Rank ablated components by impact (Δ / Δ%) and flag marginal or dominant contributions."""
    corpus = pdf_parser.get_paper_corpus(_common._paper_id(paper_id))
    text = corpus["text"]
    tables = [
        t
        for t in _tables.parse_benchmark_tables(text, corpus["pages"], corpus["captions"])
        if t["table_id"] != "inline_metrics"
    ]
    masked = list(text)
    for tb in tables:
        s, e = tb.pop("_span")
        tb["start"] = s
        masked[s:e] = " " * (e - s)
    lists = _list_ablations("".join(masked))
    for lst in lists:
        lst["page"] = pdf_parser.page_at(corpus["pages"], lst["start"])
    candidates = sorted([t for t in tables if _is_ablation(t)] + lists, key=lambda t: t["start"])
    ablations, analyzed, alerts, primary = [], [], [], None
    for tb in candidates:
        entries, reference = _ablation_entries(tb)
        if not entries:
            continue
        primary = primary or reference
        label = tb.get("label") or tb["table_id"]
        for e in entries:
            e["table_id"] = tb["table_id"]
        ablations.extend(entries)
        alerts.extend(_ablation_alerts(entries, reference, label))
        analyzed.append(
            {
                "table_id": tb["table_id"],
                "label": label,
                "caption": tb.get("caption") or "",
                "format": tb.get("format"),
                "page": tb.get("page"),
                "reference": reference,
                "variant_count": len({e["component"] for e in entries}),
            }
        )
    ablations.sort(key=lambda e: -abs(e["delta_percent"]))
    warnings = list(corpus["warnings"])
    if not ablations:
        warnings.append("No ablation tables or 'Full model / w/o ...' result lists detected.")
    return {
        "paper_id": corpus["paper_id"],
        "title": corpus["title"],
        "source": corpus["source"],
        "ablations": ablations,
        "reference": primary,
        "tables_analyzed": analyzed,
        "warnings_and_alerts": alerts,
        "warnings": warnings,
    }
