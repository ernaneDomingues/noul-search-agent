"""Benchmark tables: Markdown, LaTeX and plain-text tables parsed into rows of normalised metrics (`extract_benchmark_tables`)."""

import re
from typing import Any

from noul.providers import pdf_parser
from noul.schemas.technical import BenchmarkTable
from noul.tools.technical import common as _common

_LATEX_WRAPPERS = re.compile(
    r"\\(?:textbf|underline|textit|emph|mathbf|mathrm|textsc|textrm|text|bm|boldsymbol|uline)\s*\{([^{}]*)\}"
)


_LATEX_MULTI = re.compile(r"\\multi(?:column|row)\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{([^{}]*)\}")


_LATEX_RULES = re.compile(
    r"\\(?:hline|toprule|midrule|bottomrule|addlinespace|cline\s*\{[^{}]*\}|"
    r"cmidrule(?:\s*\([^)]*\))?\s*\{[^{}]*\})"
)


def clean_cell(value):
    """Strip Markdown/LaTeX decoration from a table cell (`**x**`, `\\textbf{x}`, `$\\pm$`, `\\%`, ...)."""
    s = str(value if value is not None else "")
    s = _LATEX_RULES.sub(" ", s)
    s = _LATEX_MULTI.sub(r"\1", s)
    for _ in range(4):
        new = _LATEX_WRAPPERS.sub(r"\1", s)
        if new == s:
            break
        s = new
    s = (
        s.replace("\\pm", "±")
        .replace("\\%", "%")
        .replace("\\&", "&")
        .replace("\\times", "×")
        .replace("$", "")
        .replace("~", " ")
    )
    s = re.sub(r"\\cite[pt]?\*?\{[^{}]*\}", "", s)
    s = re.sub(r"(\*\*|__|`)", "", s)
    s = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", s)
    return re.sub(r"\s+", " ", s).strip()


_NUM_CELL = re.compile(
    r"^[~≈<>]?\s*(?P<num>[-+−–]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[-+−–]?\.\d+)(?P<exp>[eE][-+]?\d{1,3})?\s*"
    r"(?P<unit>%|ms|s|x|×|pp|pts?)?\s*(?:(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?\s*%?)?\s*(?:\([^)]*\))?\s*[*†‡]*$",
    re.I,
)


_EMPTY_CELLS = {"", "-", "–", "—", "n/a", "na", "n.a.", "--", "×"}


def parse_number(value):
    """`"84.5%"` -> 84.5, `"84.5 ± 0.3"` -> 84.5, `"42"` -> 42, `"12.4ms"` -> 12.4, `"-"` -> None; else the string."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    s = clean_cell(value)
    if s.lower() in _EMPTY_CELLS:
        return None
    m = _NUM_CELL.match(s)
    if not m:
        return s
    num = m.group("num").replace(",", "").replace("−", "-").replace("–", "-")
    if m.group("exp"):  # scientific notation (FLOPs): never K/M/B suffixes, which collide with "7B"-style names
        return float(num + m.group("exp"))
    return float(num) if "." in num else int(num)


# Anchored on the left and bounded: an unanchored `\d+` here is quadratic on long digit runs (regex DoS).
_SCI = re.compile(r"(?<![\d.])(\d{1,6}(?:\.\d{1,6})?)\s*[·×x]\s*10\s*\^?\s*([-+]?\d{1,3})\b")


def _sci_to_e(line):
    """`2.3 · 10^19` / `2.3·1019`-style cells (PDF superscripts lost) -> `2.3e19`, one token."""
    return _SCI.sub(lambda m: f"{m.group(1)}e{m.group(2)}", line)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


METRIC_SYNONYMS = {
    "mmlu": "MMLU",
    "gsm8k": "GSM8K",
    "humaneval": "HumanEval",
    "mbpp": "MBPP",
    "math": "MATH",
    "hellaswag": "HellaSwag",
    "arcc": "ARC-Challenge",
    "arcchallenge": "ARC-Challenge",
    "truthfulqa": "TruthfulQA",
    "bleu": "BLEU",
    "rougel": "ROUGE-L",
    "f1": "F1",
    "f1score": "F1",
    "auc": "AUC",
    "auroc": "AUC",
    "accuracy": "Accuracy",
    "acc": "Accuracy",
    "perplexity": "Perplexity",
    "ppl": "Perplexity",
    "latency": "Latency_ms",
    "throughput": "Throughput",
    "flops": "FLOPs",
    "trainingcost": "Training_Cost",
    "trainingcostflops": "Training_Cost",
    "params": "Params",
    "parameters": "Params",
}


LOWER_IS_BETTER = {"Perplexity", "Latency_ms", "FLOPs", "Training_Cost", "Params"}


def normalize_metric_name(name):
    """Map metric/benchmark synonyms to canonical keys (`gsm-8k` -> `GSM8K`, `human-eval` -> `HumanEval`, ...)."""
    raw = clean_cell(name)
    base = re.sub(r"\([^)]*\)|\[[^\]]*\]|[↑↓]", " ", raw)
    base = re.sub(r"\s+", " ", base).strip()
    key = re.sub(r"[^a-z0-9]", "", base.lower())
    return METRIC_SYNONYMS.get(key, base or raw)


def _dedupe(names):
    seen: dict[Any, Any]
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


def _split_md_row(line):
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [c.replace("\\|", "|").strip() for c in cells]


_MD_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _markdown_tables(text):
    block: list[Any]
    out, block, start = [], [], None
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            if not block:
                start = pos
            block.append(stripped)
        else:
            if block:
                out.append((start, pos, block))
            block = []
        pos += len(line)
    if block:
        out.append((start, pos, block))
    tables = []
    for start, end, rows in out:
        body = [r for r in rows if not _MD_SEP.match(r)]
        if len(body) >= 2 and len(body) < len(rows):
            parsed = [_split_md_row(r) for r in body]
            tables.append({"start": start, "end": end, "headers": parsed[0], "rows": parsed[1:], "format": "markdown"})
    return tables


_TABULAR = re.compile(r"\\begin\{tabular(\*?)\}")


def _latex_tables(text):
    tables = []
    for m in _TABULAR.finditer(text):
        end_m = re.compile(r"\\end\{tabular\*?\}").search(text, m.end())
        if not end_m:
            continue
        i = m.end()
        for _ in range(2 if m.group(1) else 1):  # tabular* has {width}{colspec}
            while i < len(text) and text[i] in " \t\n":
                i += 1
            grp = pdf_parser.balanced(text, i)
            if grp:
                i = grp[1]
        body = _LATEX_RULES.sub(" ", text[i : end_m.start()])
        rows = []
        for raw in re.split(r"\\\\(?:\s*\[[^\]]*\])?", body):
            if not raw.strip():
                continue
            cells = [clean_cell(c) for c in re.split(r"(?<!\\)&", raw)]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2:
            tables.append(
                {"start": m.start(), "end": end_m.end(), "headers": rows[0], "rows": rows[1:], "format": "latex"}
            )
    return tables


_HDR_SPLIT = re.compile(r"\t+|\s{2,}|\s*\|\s*")


def _trailing_numbers(line):
    """'Llama-2 7B  45.3  14.6' -> ('Llama-2 7B', ['45.3', '14.6'])."""
    tokens = re.sub(r"\s*(±|\+/-)\s*", r"\1", _sci_to_e(line.strip())).split()
    n = 0
    while n < len(tokens) and _is_number(parse_number(tokens[-1 - n])):
        n += 1
    return " ".join(tokens[: len(tokens) - n]), tokens[len(tokens) - n :]


_GROUP_MARK = re.compile(r"^\s*\(?[A-H]\)\s*$")


def _plain_tables(text, captions, taken):
    tables = []
    for cap in captions:
        if cap["kind"] != "text" or any(s <= cap["offset"] < e for s, e in taken):
            continue
        lines, pos = [], cap["end"] + 1
        for line in text[pos : pos + 4000].split("\n"):
            lines.append((pos, line))
            pos += len(line) + 1
        rows: list[Any]
        header, rows, start, end, blanks, pre, group = None, [], None, None, 0, 0, None
        for off, line in lines:
            if not line.strip():
                if rows:
                    break
                blanks += 1
                if blanks > 2:
                    break
                continue
            if any(s <= off < e for s, e in taken) or line.strip().startswith(("|", "\\")):
                break
            label, nums = _trailing_numbers(line)
            if rows and not nums and _GROUP_MARK.match(line):
                group = line.strip()  # "(A)" alone on a line: the rows below belong to this variant group
                continue
            if not label and nums and rows:
                # Continuation row, e.g. "4 128 128 5.00" under "(A)" or under "(A) 1 512 512 5.29".
                prev = group or rows[-1][0].removesuffix(" (cont.)")
                label = (
                    prev if prev != rows[-1][0] and group and not rows[-1][0].startswith(group) else f"{prev} (cont.)"
                )
            # One-number rows ("Parser ... discriminative 88.3") need a header above and a label that is not prose.
            short = len(label.split()) <= 12 and not re.search(r"[.;:!?]\s*$", label)
            if label and (len(nums) >= 2 or (len(nums) == 1 and header is not None and short)):
                rows.append((label, nums))
                start = off if start is None else start
                end = off + len(line)
            elif not rows and line.split() and pre < 3:
                # Caption continuation or a (multi-line) header: keep the last such line as the header.
                header = line.strip()
                start = off if start is None else start
                pre += 1
            else:
                break
        if not rows:
            continue
        width = max(len(nums) for _, nums in rows)
        headers = None
        if header:
            cells = [c for c in _HDR_SPLIT.split(header) if c]
            if len(cells) == width + 1:
                headers = cells
            elif len(cells) == width:
                headers = ["Model"] + cells
            else:
                words = header.split()
                if len(words) >= width:
                    headers = [" ".join(words[: len(words) - width]) or "Model"] + words[len(words) - width :]
        headers = headers or ["Model"] + [f"col_{i}" for i in range(1, width + 1)]
        raw_rows = [[label] + nums for label, nums in rows]
        tables.append(
            {"start": start, "end": end, "headers": headers, "rows": raw_rows, "format": "text", "caption_ref": cap}
        )
    return tables


_INLINE_NAME = (
    r"MMLU|GSM-?8K|Human-?Eval|MBPP|MATH|HellaSwag|ARC[-_ ]?(?:Challenge|c)\b|TruthfulQA|BLEU|"
    r"ROUGE-?L|F1(?:-score)?|AUROC|AUC|accuracy|perplexity|PPL|latency|throughput"
)


_NUMV = r"\d+(?:\.\d+)?\s*(?:%|ms|x|×)?"


_INLINE_PAIRS = (
    re.compile(
        rf"(?<![A-Za-z])(?P<name>{_INLINE_NAME})(?![A-Za-z])\s*(?:[:=]|score of|of|at)\s*(?P<val>{_NUMV})", re.I
    ),
    re.compile(
        rf"(?P<val>{_NUMV})\s*(?:(?:accuracy|score|points|pass@1)\s+)?on\s+(?:the\s+)?"
        rf"(?P<name>{_INLINE_NAME})(?![A-Za-z])",
        re.I,
    ),
)


def _inline_pairs(text):
    found = {}
    for rx in _INLINE_PAIRS:
        for m in rx.finditer(text):
            name = normalize_metric_name(m.group("name"))
            if name == "MATH" and m.group("name") != "MATH":
                continue
            val = parse_number(m.group("val"))
            if _is_number(val) and name not in found:
                found[name] = (m.start(), val)
    return sorted(found.items(), key=lambda kv: kv[1][0])


def _assign_caption(table, captions, used, prev_end):
    if table.get("caption_ref"):
        used.add(id(table["caption_ref"]))
        return table["caption_ref"]
    best = None
    for cap in captions:
        if id(cap) in used:
            continue
        before = prev_end <= cap["offset"] <= table["start"] and table["start"] - cap["offset"] <= 600
        after = table["end"] <= cap["offset"] <= table["end"] + 300
        if before or after:
            dist = abs(table["start"] - cap["offset"]) if before else cap["offset"] - table["end"]
            if best is None or dist < best[0]:
                best = (dist, cap)
    if best:
        used.add(id(best[1]))
        return best[1]
    return None


def _build_table(table_id, caption, headers, raw_rows):
    first = clean_cell(headers[0]) if headers else ""
    norm_headers = _dedupe([first or "Model"] + [normalize_metric_name(h) for h in headers[1:]])
    label_key = norm_headers[0]
    labels = [clean_cell(r[0]) if r else "" for r in raw_rows]
    transposed = first.lower() in ("benchmark", "benchmarks", "dataset", "task", "metric", "metrics") or (
        labels
        and sum(normalize_metric_name(x) in METRIC_SYNONYMS.values() for x in labels) * 2 > len(labels)
        and not any(h in METRIC_SYNONYMS.values() for h in norm_headers[1:])
    )
    metrics: dict[Any, Any]
    seen: dict[Any, Any]
    rows, metrics, seen = [], {}, {}
    for cells, label in zip(raw_rows, labels, strict=False):
        cells = list(cells[: len(norm_headers)]) + [None] * (len(norm_headers) - len(cells))
        if transposed:
            label = normalize_metric_name(label)
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label} ({seen[label]})"
        row = {label_key: label}
        for h, cell in zip(norm_headers[1:], cells[1:], strict=False):
            row[h] = parse_number(cell) if cell is not None else None
        rows.append(row)
        for h in norm_headers[1:]:
            if _is_number(row[h]):
                if transposed:
                    metrics.setdefault(h, {})[label] = row[h]
                else:
                    metrics.setdefault(label, {})[h] = row[h]
    return BenchmarkTable(
        table_id=table_id, caption=caption, headers=norm_headers, rows=rows, normalized_metrics=metrics
    ).model_dump()


def parse_benchmark_tables(text, pages=None, captions=None):
    """Detect Markdown, LaTeX and plain-text tables plus inline `Benchmark: value` pairs in `text`."""
    pages = pages or pdf_parser.split_pages(text)
    captions = captions if captions is not None else pdf_parser.find_table_captions(text)
    found = _markdown_tables(text) + _latex_tables(text)
    taken = [(t["start"], t["end"]) for t in found]
    found += _plain_tables(text, captions, taken)
    found.sort(key=lambda t: t["start"])
    used: set[Any]
    used, out, ids, prev_end = set(), [], set(), 0
    for ordinal, tb in enumerate(found, 1):
        cap = _assign_caption(tb, captions, used, prev_end)
        prev_end = tb["end"]
        number = (cap or {}).get("number") or str(ordinal)
        table_id = f"table_{number}"
        while table_id in ids:
            table_id += "b"
        ids.add(table_id)
        caption = (cap or {}).get("caption") or ""
        built = _build_table(table_id, caption, tb["headers"], tb["rows"])
        built["label"] = (cap or {}).get("label") or f"Table {number}"
        built["format"] = tb["format"]
        built["page"] = pdf_parser.page_at(pages, tb["start"])
        built["_span"] = (tb["start"], tb["end"])
        out.append(built)

    masked = list(text)
    for tb in out:
        s, e = tb["_span"]
        masked[s:e] = " " * (e - s)
    pairs = _inline_pairs("".join(masked))
    if pairs:
        rows = [{"Benchmark": name, "Value": val} for name, (_, val) in pairs]
        inline = BenchmarkTable(
            table_id="inline_metrics",
            caption="Benchmark: value pairs reported in the text",
            headers=["Benchmark", "Value"],
            rows=rows,
            normalized_metrics={"reported": {name: val for name, (_, val) in pairs}},
        ).model_dump()
        inline.update(
            {
                "label": "Inline metrics",
                "format": "inline",
                "page": pdf_parser.page_at(pages, pairs[0][1][0]),
                "_span": (pairs[0][1][0], pairs[0][1][0]),
            }
        )
        out.append(inline)
    return out


def _summary(tables):
    by_metric: dict[Any, Any] = {}
    for tb in tables:
        for model, metrics in tb["normalized_metrics"].items():
            for metric, value in metrics.items():
                by_metric.setdefault(metric, {}).setdefault(model, value)
    canonical = set(METRIC_SYNONYMS.values())
    best = {}
    for metric, values in by_metric.items():
        if metric in canonical and values:
            pick = (min if metric in LOWER_IS_BETTER else max)(values.items(), key=lambda kv: kv[1])
            best[metric] = {
                "model": pick[0],
                "value": pick[1],
                "direction": "lower_is_better" if metric in LOWER_IS_BETTER else "higher_is_better",
            }
    return {"table_count": len(tables), "metrics": sorted(by_metric), "by_metric": by_metric, "best_by_metric": best}


def extract_benchmark_tables(paper_id):
    """Extract and normalize benchmark tables (Markdown, LaTeX, plain text) of a paper."""
    corpus = pdf_parser.get_paper_corpus(_common._paper_id(paper_id))
    tables = parse_benchmark_tables(corpus["text"], corpus["pages"], corpus["captions"])
    for tb in tables:
        tb.pop("_span", None)
    warnings = list(corpus["warnings"])
    if not tables:
        warnings.append("No benchmark tables or Benchmark: value pairs detected.")
    return {
        "paper_id": corpus["paper_id"],
        "title": corpus["title"],
        "source": corpus["source"],
        "tables": tables,
        "normalized_summary": _summary(tables),
        "warnings": warnings,
    }
