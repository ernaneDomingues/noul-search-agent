"""Phase 3 technical deep-dive tools (#08 benchmark tables, #10 claim grounding, #11 math, #12 ablations,
#13 prompts & decoding).

Deterministic, stdlib-only extraction over the best available paper text (cached open-access full text, else
title + abstract). The host agent's LLM interprets the results; these tools only structure literal evidence."""

import re

from noul import errors as _errors
from noul.providers import pdf_parser
from noul.schemas.technical import BenchmarkTable, GroundedClaim
from noul.search import engine as _engine


def _paper_id(paper_id):
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise _errors.HTTPError(400, "paper_id must be a non-empty string")
    return paper_id.strip()


# --------------------------------------------------------------------------- cell / number normalization

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
    r"^[~≈<>]?\s*(?P<num>[-+−–]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[-+−–]?\.\d+)\s*"
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
    return float(num) if "." in num else int(num)


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
}
LOWER_IS_BETTER = {"Perplexity", "Latency_ms"}


def normalize_metric_name(name):
    """Map metric/benchmark synonyms to canonical keys (`gsm-8k` -> `GSM8K`, `human-eval` -> `HumanEval`, ...)."""
    raw = clean_cell(name)
    base = re.sub(r"\([^)]*\)|\[[^\]]*\]|[↑↓]", " ", raw)
    base = re.sub(r"\s+", " ", base).strip()
    key = re.sub(r"[^a-z0-9]", "", base.lower())
    return METRIC_SYNONYMS.get(key, base or raw)


def _dedupe(names):
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


# --------------------------------------------------------------------------- table detection


def _split_md_row(line):
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [c.replace("\\|", "|").strip() for c in cells]


_MD_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _markdown_tables(text):
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
    tokens = re.sub(r"\s*(±|\+/-)\s*", r"\1", line.strip()).split()
    n = 0
    while n < len(tokens) and _is_number(parse_number(tokens[-1 - n])):
        n += 1
    return " ".join(tokens[: len(tokens) - n]), tokens[len(tokens) - n :]


def _plain_tables(text, captions, taken):
    tables = []
    for cap in captions:
        if cap["kind"] != "text" or any(s <= cap["offset"] < e for s, e in taken):
            continue
        lines, pos = [], cap["end"] + 1
        for line in text[pos : pos + 4000].split("\n"):
            lines.append((pos, line))
            pos += len(line) + 1
        header, rows, start, end, blanks = None, [], None, None, 0
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
            if len(nums) >= 2 and label:
                rows.append((label, nums))
                start = off if start is None else start
                end = off + len(line)
            elif header is None and not rows and len(line.split()) >= 2:
                header = line.strip()
                start = off
            elif rows:
                break
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
    by_metric = {}
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


# --------------------------------------------------------------------------- #08 benchmark tables


def extract_benchmark_tables(paper_id):
    """Extract and normalize benchmark tables (Markdown, LaTeX, plain text) of a paper."""
    corpus = pdf_parser.get_paper_corpus(_paper_id(paper_id))
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


# --------------------------------------------------------------------------- #10 claim grounding

MAX_CLAIMS = 30
AUTO_CLAIMS = 10
_CLAIM_NUM = re.compile(
    r"(?<![A-Za-z][-_])(?<![\w.])[-+−]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%|\s?(?:x|×|pp|ms|GB|B|M|K)\b)?"
)
_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\-]{2,}")
_STOP = frozenset(
    "the and for with that this our from are was were has have had than into its their these those which while "
    "using used use based also both can more most such between over under per all any each not but when where "
    "model models method approach paper work results result show shows showed achieve achieves achieved "
    "que com para uma umas uns dos das nos nas pelo pela como mais por modelo modelos resultado resultados "
    "artigo trabalho sobre entre ser são foi tem".split()
)
_EXTRAPOLATION_CUES = re.compile(
    r"\b(?:will|always|never|guarantees?|proves?|any|every|all\s+(?:tasks|domains|hardware|settings)|"
    r"universally|therefore|thus|implies|sempre|nunca|garante|prova|qualquer|todos?\s+os)\b",
    re.I,
)
_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=[\w%)\]]))+[.!?]?")


def _claim_numbers(text):
    return [re.sub(r"\s+", "", m.group(0)) for m in _CLAIM_NUM.finditer(text or "")]


def _num_value(token):
    m = re.match(r"[-+−]?\d+(?:,\d{3})*(?:\.\d+)?", token)
    return float(m.group(0).replace(",", "").replace("−", "-")) if m else None


def _keywords(text):
    out = set()
    for w in _WORD.findall(text or ""):
        canon = normalize_metric_name(w)
        if canon in METRIC_SYNONYMS.values():
            out.add(canon.lower())
        elif w.lower() not in _STOP:
            out.add(w.lower())
    return out


def _passages(corpus):
    """Index the document as (page, label, quote, scoring text) passages: one per table row, one per sentence."""
    text, pages, sections = corpus["text"], corpus["pages"], corpus["sections"]
    tables = parse_benchmark_tables(text, pages, corpus["captions"])
    out, spans = [], []
    for tb in tables:
        if tb["format"] == "inline":
            continue
        s, e = tb["_span"]
        spans.append((s, e))
        number = tb["label"].split()[-1]
        context = " ".join([tb["caption"], " ".join(tb["headers"])])
        pos = s
        for line in text[s:e].split("\n"):
            quote = re.sub(r"\s+", " ", line).strip()
            if quote and not _MD_SEP.match(quote) and _LATEX_RULES.sub("", quote).strip():
                out.append(
                    {
                        "page": pdf_parser.page_at(pages, pos),
                        "label": f"Tabela {number}",
                        "quote": quote,
                        "scoring": f"{context} {clean_cell(quote)}",
                    }
                )
            pos += len(line) + 1
    for m in _SENTENCE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        quote = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(quote) < 8 or not re.search(r"[A-Za-z]", quote):
            continue
        section = pdf_parser.section_at(sections, m.start())
        if section:
            label = f"Seção {section}"
        else:
            label = "Seção Resumo" if corpus["source"] == "abstract" else "Seção Texto"
        out.append({"page": pdf_parser.page_at(pages, m.start()), "label": label, "quote": quote, "scoring": quote})
    for p in out:
        p["values"] = {_num_value(x) for x in _claim_numbers(p["scoring"])} - {None}
        p["keywords"] = _keywords(p["scoring"])
    return out


def _clip_quote(quote, numbers, limit=220):
    if len(quote) <= limit:
        return quote
    anchor = 0
    for n in numbers:
        i = quote.find(re.sub(r"[^\d.,]", "", n))
        if i >= 0:
            anchor = i
            break
    start = max(0, min(anchor - limit // 3, len(quote) - limit))
    clip = quote[start : start + limit].strip()
    return ("..." if start > 0 else "") + clip + ("..." if start + limit < len(quote) else "")


def _auto_claims(corpus, passages):
    """Numeric / comparative sentences: from the abstract first, then from the non-table body text."""
    comparative = re.compile(
        r"\b(?:outperform\w*|improv\w*|better than|surpass\w*|reduc\w*|faster|"
        r"state[-\s]of[-\s]the[-\s]art)\b",
        re.I,
    )
    abstract = [re.sub(r"\s+", " ", m.group(0)).strip() for m in _SENTENCE.finditer(corpus["abstract"] or "")]
    body = [p["quote"] for p in passages if not p["label"].startswith("Tabela")]
    claims = []
    for group in (abstract, body):
        for s in group:
            if (
                len(s.split()) >= 5
                and not re.match(r"(?:Table|Tabela|Fig(?:ure|\.)|Figura)\s*\d", s, re.I)
                and (_claim_numbers(s) or comparative.search(s))
                and s not in claims
            ):
                claims.append(s)
                if len(claims) >= AUTO_CLAIMS:
                    return claims
        if len(claims) >= 3:
            break
    return claims


def _validate_claims(claims):
    if claims is None:
        return []
    if isinstance(claims, str):
        claims = claims.split(";")
    if not isinstance(claims, (list, tuple)) or any(not isinstance(x, str) for x in claims):
        raise _errors.HTTPError(400, "claims must be a list of strings")
    claims = list(dict.fromkeys(x.strip() for x in claims if x.strip()))
    if len(claims) > MAX_CLAIMS:
        raise _errors.HTTPError(400, f"claims accepts at most {MAX_CLAIMS} items (got {len(claims)})")
    return claims


def ground_claim(claim, passages):
    """Classify one claim against indexed passages -> GroundedClaim dict."""
    numbers = _claim_numbers(claim)
    values = [v for v in (_num_value(n) for n in numbers) if v is not None]
    kws = _keywords(claim)
    best, best_key = None, None
    for p in passages:
        matched = sum(1 for v in values if v in p["values"])
        lex = len(kws & p["keywords"]) / len(kws) if kws else 0.0
        key = (matched / len(values) if values else 0.0) * 2 + lex
        if best_key is None or key > best_key:
            best, best_key = p, key
    if best is None:
        return GroundedClaim(
            claim=claim,
            status="extrapolated",
            numbers_checked=numbers,
            citation_tag="[EXTRAPOLAÇÃO NÃO LITERAL — sem correspondência exata no texto; "
            "documento sem texto analisável]",
        ).model_dump()
    matched = sum(1 for v in values if v in best["values"])
    shared = kws & best["keywords"]
    lex = len(shared) / len(kws) if kws else 1.0
    if values:
        if matched == len(values):
            status = "verified" if (shared or not kws) else "partially_supported"
        else:
            status = "partially_supported" if matched else "extrapolated"
    else:
        status = "verified" if lex >= 0.75 else "partially_supported" if lex >= 0.4 else "extrapolated"
    cues = {c.lower() for c in _EXTRAPOLATION_CUES.findall(claim)} - {
        c.lower() for c in _EXTRAPOLATION_CUES.findall(best["quote"])
    }
    if cues and status == "verified":
        status = "partially_supported"
    quote = _clip_quote(best["quote"], numbers)
    if status == "extrapolated":
        tag = (
            f"[EXTRAPOLAÇÃO NÃO LITERAL — sem correspondência exata no texto; trecho mais próximo na "
            f'pág. {best["page"]}: "{quote}"]'
        )
    else:
        tag = f'[pág. {best["page"]}, {best["label"]}: "{quote}"]'
    return GroundedClaim(
        claim=claim,
        status=status,
        citation_tag=tag,
        page=best["page"],
        table_or_section=best["label"],
        verbatim_quote=quote,
        numbers_checked=numbers,
    ).model_dump()


def verify_paper_claims(paper_id, claims=None):
    """Audit claims (given, or auto-extracted numeric/comparative sentences) against the paper's literal text."""
    claims = _validate_claims(claims)
    corpus = pdf_parser.get_paper_corpus(_paper_id(paper_id))
    auto = not claims
    passages = _passages(corpus)
    if auto:
        claims = _auto_claims(corpus, passages)
    results = [ground_claim(cl, passages) for cl in claims]
    summary = {k: sum(r["status"] == k for r in results) for k in ("verified", "partially_supported", "extrapolated")}
    warnings = list(corpus["warnings"])
    if auto and not claims:
        warnings.append("No numeric or comparative claims found to audit.")
    if summary["extrapolated"]:
        warnings.append(f"{summary['extrapolated']} claim(s) are NOT literally supported by the paper text.")
    return {
        "paper_id": corpus["paper_id"],
        "title": corpus["title"],
        "source": corpus["source"],
        "auto_extracted_claims": auto,
        "claims": results,
        "summary": summary,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #11 math & pseudocode

MATH_MODES = ("latex", "python", "both")
MAX_MATH_ITEMS = 40
_GREEK = (
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi rho "
    "sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega"
).split()
_UNICODE = {
    "α": "\\alpha ",
    "β": "\\beta ",
    "γ": "\\gamma ",
    "δ": "\\delta ",
    "ε": "\\epsilon ",
    "η": "\\eta ",
    "θ": "\\theta ",
    "λ": "\\lambda ",
    "μ": "\\mu ",
    "π": "\\pi ",
    "ρ": "\\rho ",
    "σ": "\\sigma ",
    "τ": "\\tau ",
    "φ": "\\phi ",
    "ω": "\\omega ",
    "Σ": "\\Sigma ",
    "Δ": "\\Delta ",
    "·": "*",
    "×": "*",
    "−": "-",
    "√": "\\sqrt ",
    "ᵀ": "^T",
    "²": "^2",
    "³": "^3",
    "⊤": "\\top",
    "←": "=",
}
_MATH_FUNCS = {
    "exp": "math.exp",
    "log": "math.log",
    "ln": "math.log",
    "sin": "math.sin",
    "cos": "math.cos",
    "tan": "math.tan",
    "tanh": "math.tanh",
    "sqrt": "math.sqrt",
    "max": "max",
    "min": "min",
    "abs": "abs",
    "sigmoid": None,
    "softmax": None,
}
_CALLABLE_LETTERS = set("fghFGH")


class _MathError(ValueError):
    pass


def _py_name(name):
    import keyword

    name = re.sub(r"\W+", "_", name).strip("_") or "x"
    if name[0].isdigit():
        name = "v_" + name
    if keyword.iskeyword(name) or name in ("math", "Any", "Callable", "Sequence"):
        name += "_"
    return "lam" if name == "lambda_" else name


def _prep_latex(expr):
    s = expr
    for k, v in _UNICODE.items():
        s = s.replace(k, v)
    s = re.split(r"\\\\", s)[0]  # first line of multi-line (align) environments
    s = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag|displaystyle|limits)\b", " ", s)
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])", "", s)
    s = re.sub(r"\\[,;:! ]|\\q?quad\b|~|&", " ", s)
    s = re.sub(r"\\(?:cdot|times|ast)\b", "*", s)
    s = re.sub(r"\\div\b", "/", s)
    s = re.sub(r"\^\s*\{?\s*(?:\\top|T|\\intercal)\s*\}?", "^⊺", s)
    s = re.sub(r"\\(?:mathcal|mathbf|boldsymbol|bm|mathbb|mathit|mathsf)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(
        r"\\(?:mathrm|text|textrm|operatorname|textit)\s*\{([^{}]*)\}", lambda m: "⟨" + m.group(1).strip() + "⟩", s
    )
    s = re.sub(r"\\(hat|bar|tilde|dot|vec)\s*\{([^{}]*)\}", lambda m: f"⟨{m.group(2)}_{m.group(1)}⟩", s)
    s = re.sub(r"\\(hat|bar|tilde|dot|vec)\s+([A-Za-z])", lambda m: f"⟨{m.group(2)}_{m.group(1)}⟩", s)
    return s.strip().rstrip(".,;")


_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|(\\[A-Za-z]+)|(⟨[^⟩]+⟩)|([A-Za-z][A-Za-z0-9]*)|(\^⊺)|(.))")


def _tokenize(s, word_mode):
    out = []
    for m in _TOKEN.finditer(s):
        num, cmd, ph, word, tr, ch = m.groups()
        if num:
            out.append(("num", num))
        elif cmd:
            out.append(("cmd", cmd[1:]))
        elif ph:
            out.append(("id", ph[1:-1].replace(" ", "_")))
        elif word:
            if word_mode or len(word) == 1:
                out.append(("id", word))
            else:
                out.extend(("id", c) for c in word)
        elif tr:
            out.append(("op", "T"))
        elif ch and not ch.isspace():
            if ch not in "+-*/^_=(){}[],'|":
                raise _MathError(f"unsupported symbol {ch!r}")
            out.append(("op", ch))
    return out


class _MathParser:
    """Recursive-descent LaTeX/plain-text expression -> Python expression (implicit multiplication aware)."""

    def __init__(self, tokens, word_mode):
        self.t, self.i, self.word_mode = tokens, 0, word_mode
        self.vars, self.seqs, self.calls, self.bound = [], [], [], set()
        self.transpose = False
        self.matrices = set()

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if tok[0] is None or (kind and tok[0] != kind) or (val is not None and tok[1] != val):
            raise _MathError(f"unexpected token {tok}")
        self.i += 1
        return tok

    def accept(self, val):
        if self.peek() == ("op", val):
            self.i += 1
            return True
        return False

    def _note(self, bucket, name):
        if name not in bucket and name not in self.bound:
            bucket.append(name)

    def parse(self):
        out = self.expr()
        if self.peek()[0] is not None:
            raise _MathError(f"trailing token {self.peek()}")
        return out

    def expr(self):
        out = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            out = f"{out} {op} {self.term()}"
        return out

    def _starts_factor(self):
        kind, val = self.peek()
        return (
            kind in ("num", "id")
            or (kind == "cmd" and val not in ("le", "leq", "ge", "geq", "to"))
            or (kind == "op" and val in "({[")
        )

    def term(self):
        out = self.unary()
        while True:
            if self.peek() in (("op", "*"), ("op", "/")):
                op = self.take()[1]
                out = f"{out} {op} {self.unary()}"
            elif self._starts_factor():
                out = f"{out} * {self.unary()}"
            else:
                return out

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            op = self.take()[1]
            return f"{op}{self.unary()}"
        return self.power()

    def power(self):
        base = self.atom()
        while True:
            if self.peek() == ("op", "T"):
                self.take()
                self.transpose = True
                if re.fullmatch(r"\w+", base):
                    self.matrices.add(base)
                base = f"{base}.T"
            elif self.accept("^"):
                base = f"{base} ** {self.group_or_atom()}"
            elif self.accept("'"):
                base = base + "_prime"
            else:
                return base

    def group_or_atom(self):
        if self.peek() == ("op", "{"):
            return f"({self.braced()})"
        if self.peek()[0] == "num" and len(self.peek()[1]) > 1 and not self.word_mode:
            num = self.take()[1]
            return num[0] if "." not in num else num
        return self.atom()

    def braced(self):
        self.take("op", "{")
        out = self.expr() if self.peek() != ("op", "}") else "1"
        self.take("op", "}")
        return out

    def subscript(self):
        """`_{total}` / `_k` / `_{i,j}` -> python-safe suffix."""
        names = {"+": "plus", "-": "minus", ",": "_", "*": "x", "=": "eq"}
        if self.accept("{"):
            out, depth = "", 1
            while True:
                kind, val = self.take()
                if (kind, val) == ("op", "{"):
                    depth += 1
                    continue
                if (kind, val) == ("op", "}"):
                    depth -= 1
                    if not depth:
                        return out.strip("_") or "0"
                    continue
                if kind == "op":
                    out += f"_{names[val]}_" if val in names else "_"
                elif kind == "cmd":
                    out += f"_{val}_"
                else:
                    out += val
        kind, val = self.take()
        return val

    def call_args(self):
        self.take("op", "(")
        args = [] if self.peek() == ("op", ")") else [self.expr()]
        while self.accept(","):
            args.append(self.expr())
        self.take("op", ")")
        return ", ".join(args)

    def identifier(self, name):
        sub = None
        if self.accept("_"):
            sub = self.subscript()
        if sub is not None and sub in self.bound:
            py = _py_name(name)
            self._note(self.seqs, py)
            return f"{py}[{sub}]"
        py = _py_name(f"{name}_{sub}" if sub is not None else name)
        is_call = self.peek() == ("op", "(") and (len(name) > 1 or name in _CALLABLE_LETTERS) and sub is None
        if is_call and name.lower() in _MATH_FUNCS and _MATH_FUNCS[name.lower()]:
            return f"{_MATH_FUNCS[name.lower()]}({self.call_args()})"
        if is_call:
            self._note(self.calls, py)
            return f"{py}({self.call_args()})"
        if py in self.bound:
            return py
        self._note(self.vars, py)
        return py

    def big_operator(self, fn):
        var, lo, hi = None, None, None
        if self.accept("_"):
            if self.accept("{"):
                var = _py_name(self.take("id")[1])
                if self.accept("="):
                    lo = self.expr()
                self.take("op", "}")
            else:
                var = _py_name(self.take("id")[1])
        if self.accept("^"):
            hi = self.group_or_atom()
        var = var or "i"
        self.bound.add(var)
        start = len(self.seqs)
        summand = self.unary_chain()
        self.bound.discard(var)
        if hi is not None:
            rng = f"range({lo if lo is not None else 0}, {hi} + 1)"
        elif len(self.seqs) > start:
            rng = f"range(len({self.seqs[start]}))"
        else:
            raise _MathError("unbounded big operator")
        return f"{fn}({summand} for {var} in {rng})"

    def unary_chain(self):
        out = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")) or self._starts_factor():
            op = self.take()[1] if self.peek() in (("op", "*"), ("op", "/")) else "*"
            out = f"{out} {op} {self.unary()}"
        return f"({out})"

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return val
        if kind == "id":
            if val == "e" and self.peek() == ("op", "^"):
                self.take()
                return f"math.exp{self.group_or_atom() if self.peek() == ('op', '{') else '(' + self.atom() + ')'}"
            return self.identifier(val)
        if kind == "op" and val in "([":
            close = ")" if val == "(" else "]"
            inner = self.expr()
            self.take("op", close)
            return f"({inner})"
        if kind == "op" and val == "{":
            self.i -= 1
            return f"({self.braced()})"
        if kind == "op" and val == "|":
            inner = self.expr()
            self.take("op", "|")
            return f"abs({inner})"
        if kind == "cmd":
            if val == "frac" or val in ("dfrac", "tfrac"):
                num = self.braced()
                return f"(({num}) / ({self.braced()}))"
            if val == "sqrt":
                if self.accept("["):
                    n = self.expr()
                    self.take("op", "]")
                    return f"(({self.braced()}) ** (1 / ({n})))"
                return f"math.sqrt({self.braced() if self.peek() == ('op', '{') else self.atom()})"
            if val in ("sum", "prod"):
                return self.big_operator("sum" if val == "sum" else "math.prod")
            if val == "pi":
                return "math.pi"
            if val in ("infty",):
                return "math.inf"
            if val in _MATH_FUNCS and _MATH_FUNCS[val]:
                if self.peek() == ("op", "("):
                    return f"{_MATH_FUNCS[val]}({self.call_args()})"
                if self.peek() == ("op", "{"):
                    return f"{_MATH_FUNCS[val]}({self.braced()})"
                return f"{_MATH_FUNCS[val]}({self.power()})"
            if val in _GREEK:
                return self.identifier(val)
            if val == "nabla" and self.peek()[0] in ("id", "cmd"):
                return self.identifier("grad_" + self.take()[1])
            if val == "partial" and self.peek()[0] in ("id", "cmd"):
                return self.identifier("d_" + self.take()[1])
        raise _MathError(f"unsupported token {kind}:{val}")


def _split_equation(expr):
    """Top-level `lhs = rhs` split (first `=` outside braces/parentheses)."""
    depth = 0
    for i, ch in enumerate(expr):
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "=" and depth == 0 and expr[i - 1 : i] not in ("<", ">", "!", ":") and expr[i + 1 : i + 2] != "=":
            lhs = expr[:i].rstrip(":").strip()
            rhs = expr[i + 1 :].strip()
            if "=" in rhs:  # chained (align) equations: keep the first right-hand side
                rhs = _split_equation(rhs)[0] or rhs
            return lhs, rhs
    return None, expr.strip()


def _docstring(title, body):
    title, body = (x.replace('"""', '\\"\\"\\"').rstrip() for x in (title, body))
    body = "\n".join(f"    {ln.strip()}".rstrip() for ln in body.splitlines() if ln.strip())
    return f'    r"""{title}\n\n{body} \n    """\n'


def _signature(vars_, seqs, calls, matrices=()):
    params = [f"{v}: {'Any' if v in matrices else 'float'}" for v in vars_ if v not in seqs and v not in calls]
    params += [f"{s}: Sequence[float]" for s in seqs]
    params += [f"{c}: Callable[..., Any]" for c in calls]
    return ", ".join(params)


def latex_to_python(expr, func_name, label="", word_mode=False):
    """Translate an equation into a typed Python function. Returns (code, variables, translated_ok)."""
    lhs, rhs = _split_equation(_prep_latex(expr))
    header = "import math\nfrom typing import Any, Callable, Sequence\n\n\n"
    original = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag)\b", "", expr).strip()
    doc = _docstring(f"{label or func_name}: LaTeX formula", original)
    try:
        parser = _MathParser(_tokenize(rhs, word_mode), word_mode)
        body = parser.parse()
        lhs_name = re.sub(r"\s+", " ", lhs.replace("⟨", "").replace("⟩", "")).strip() if lhs else None
        variables = [v for v in parser.vars if v not in parser.calls]
        if parser.transpose:  # matrix expression: operands multiplied with a transposed matrix are matrices too
            parser.matrices |= {
                v for v in parser.vars if re.search(rf"\b{re.escape(v)}\b\s*\*|\*\s*\b{re.escape(v)}\b", body)
            }
        ret = "Any" if parser.transpose else "float"
        code = (
            f"{header}def {func_name}({_signature(variables, parser.seqs, parser.calls, parser.matrices)}) -> {ret}:\n{doc}"
            + (f"    # {lhs_name} = ...\n" if lhs_name else "")
            + f"    return {body}\n"
        )
        import ast

        ast.parse(code)
        return code, variables + [s for s in parser.seqs if s not in variables] + parser.calls, True
    except (_MathError, SyntaxError, IndexError, ValueError):
        code = (
            f"{header}def {func_name}(*args: Any, **kwargs: Any) -> float:\n{doc}"
            f'    raise NotImplementedError("Automatic translation unsupported; see the LaTeX in the docstring.")\n'
        )
        return code, [], False


_ALGO_INPUT = re.compile(r"^\s*(?:\d+[:.]\s*)?\\?(?:Input|Require|Inputs|KwIn)\b\s*:?\s*(.*)$", re.I | re.M)


def algorithm_to_python(body, func_name, label):
    """Typed skeleton for pseudocode: inputs become parameters, every step a comment (always ast-valid)."""
    import ast

    inputs = []
    for m in _ALGO_INPUT.finditer(body):
        for part in re.split(r",|;|\band\b", m.group(1)):
            words = re.findall(
                r"\\?[A-Za-z][A-Za-z0-9_]*|[α-ωΑ-Ω]", _prep_latex(part).replace("⟨", "").replace("⟩", "")
            )
            if words:
                name = _py_name(words[-1].lstrip("\\"))
                if name not in inputs:
                    inputs.append(name)
    steps = [re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines()]
    steps = [s for s in steps if s and not re.match(r"\\(?:begin|end)\{", s)]
    code = (
        "import math\nfrom typing import Any, Callable, Sequence\n\n\n"
        f"def {func_name}({', '.join(f'{x}: Any' for x in inputs)}) -> Any:\n" + _docstring(label, body)
    )
    code += "".join(f"    # {s}\n" for s in steps)
    code += '    raise NotImplementedError("Pseudocode transcribed step by step above; implement each step.")\n'
    ast.parse(code)
    return code, inputs


_ENV = re.compile(
    r"\\begin\{(equation|align|gather|multline|eqnarray|algorithm|algorithmic)(\*?)\}(.*?)"
    r"\\end\{\1\2\}",
    re.S,
)
_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_INLINE = re.compile(r"(?<![\\$])\$([^$\n]{3,200})\$(?!\$)")
_PLAIN_EQ = re.compile(
    r"^[ \t]*(?:\(\d+\)[ \t]*)?(?P<eq>(?P<lhs>[A-Za-zα-ωΑ-Ω][\w]*(?:\([^()\n=]{0,60}\))?)[ \t]*=[ \t]*"
    r"(?P<rhs>[^=\n]{3,240}?))[ \t]*(?:\(\d+\))?[ \t]*$",
    re.M,
)
_PLAIN_ALGO = re.compile(r"^[ \t]*(Algorithm[ \t]+\d+)[ \t]*[:.]?[ \t]*([^\n]*)\n((?:[ \t]*\S[^\n]*\n?){1,40})", re.M)


def _has_operator(rhs):
    return bool(re.search(r"[+*/^·×√−-]|\\(?:frac|sqrt|sum|prod|exp|log|cdot)|[A-Za-z]\s*\(", rhs))


def parse_math(text):
    """Equations (LaTeX environments, $$..$$, \\[..\\], inline $a = b$, plain-text) and algorithms, in order."""
    found, spans = [], []

    def free(s, e):
        return not any(a < e and s < b for a, b in spans)

    for m in _ENV.finditer(text):
        env, body = m.group(1), m.group(3)
        spans.append((m.start(), m.end()))
        if env in ("algorithm", "algorithmic"):
            cap = re.search(r"\\caption\{([^{}]*)\}", body)
            found.append((m.start(), "algorithm", cap.group(1) if cap else "", body, False))
        else:
            lab = re.search(r"\\label\{([^{}]*)\}", body)
            found.append((m.start(), "equation", lab.group(1) if lab else "", body.strip(), False))
    for m in _DISPLAY.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end()))
            found.append((m.start(), "equation", "", (m.group(1) or m.group(2)).strip(), False))
    for m in _INLINE.finditer(text):
        body = m.group(1)
        lhs, rhs = _split_equation(body)
        if free(m.start(), m.end()) and lhs and _has_operator(rhs):
            spans.append((m.start(), m.end()))
            found.append((m.start(), "equation", "", body.strip(), False))
    for m in _PLAIN_ALGO.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end()))
            found.append(
                (m.start(), "algorithm", f"{m.group(1)}: {m.group(2).strip()}".strip(": "), m.group(3).rstrip(), True)
            )
    for m in _PLAIN_EQ.finditer(text):
        if not free(m.start(), m.end()) or "|" in m.group(0) or "$" in m.group(0) or "\\" in m.group(0):
            continue
        lhs, rhs = m.group("lhs"), m.group("rhs").strip().rstrip(".,;")
        if not _has_operator(rhs) or len(rhs.split()) > 25:
            continue
        try:
            _MathParser(_tokenize(_prep_latex(rhs), True), True).parse()
        except _MathError:
            continue
        spans.append((m.start(), m.end()))
        found.append((m.start(), "equation", "", f"{lhs} = {rhs}", True))
    return sorted(found, key=lambda x: x[0])


def _latex_clean(body, word_mode):
    s = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag)\b", "", body)
    s = re.sub(r"[ \t]*\n[ \t]*", " ", s).strip()
    if word_mode:
        s = _prep_latex(s).replace("⟨", "\\mathrm{").replace("⟩", "}").replace("^⊺", "^{\\top}")
    return f"$$ {s} $$"


def extract_paper_math(paper_id, mode="latex"):
    """Equations and pseudocode as clean LaTeX and/or typed, ast-validated Python functions."""
    import ast

    mode = (mode or "latex").strip().lower() if isinstance(mode, str) else mode
    if mode not in MATH_MODES:
        raise _errors.HTTPError(400, f"mode must be one of {', '.join(MATH_MODES)} (got {mode!r})")
    corpus = pdf_parser.get_paper_corpus(_paper_id(paper_id))
    from noul.schemas.technical import MathItem

    items, eq_n, algo_n, seen, untranslated = [], 0, 0, set(), 0
    for _, kind, label, body, plain in parse_math(corpus["text"]):
        key = re.sub(r"\s+", "", body)
        if key in seen:
            continue
        seen.add(key)
        if kind == "equation":
            eq_n += 1
            name, label = f"equation_{eq_n}", label or f"Equation {eq_n}"
            code, variables, ok = latex_to_python(body, name, label, word_mode=plain)
            untranslated += not ok
            latex = _latex_clean(body, plain)
        else:
            algo_n += 1
            name, label = f"algorithm_{algo_n}", label or f"Algorithm {algo_n}"
            code, variables = algorithm_to_python(body, name, label)
            latex = body.strip() if not plain else "\\begin{algorithmic}\n" + body.strip() + "\n\\end{algorithmic}"
        ast.parse(code)
        items.append(
            MathItem(
                index=len(items) + 1,
                kind=kind,
                label=label,
                latex=latex if mode in ("latex", "both") else "",
                python_code=code if mode in ("python", "both") else "",
                variables=variables,
            ).model_dump()
        )
        if len(items) >= MAX_MATH_ITEMS:
            break
    warnings = list(corpus["warnings"])
    if not items:
        warnings.append("No equations or algorithms detected.")
    if untranslated and mode != "latex":
        warnings.append(
            f"{untranslated} equation(s) could not be translated automatically; their functions raise "
            "NotImplementedError and keep the LaTeX in the docstring."
        )
    return {
        "paper_id": corpus["paper_id"],
        "title": corpus["title"],
        "source": corpus["source"],
        "mode": mode,
        "equation_count": eq_n,
        "algorithm_count": algo_n,
        "items": items,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #12 ablation tables

MARGINAL_PCT = 1.0
DOMINANT_SHARE = 0.6
_ABL_VARIANT = re.compile(
    r"^\s*(?:w/o|w/|without|no|minus|remove[sd]?|removing|drop(?:ped)?|replace[sd]?|only|with)\b|^\s*[-+−–]\s*\S", re.I
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
                parse_number(m.group("num").replace("−", "-")),
                normalize_metric_name(m.group("metric")) if m.group("metric") else "score",
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
    if re.search(r"ablat|component|contribution", table.get("caption") or "", re.I):
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
            if not (_is_number(b) and _is_number(v)):
                continue
            delta = round(v - b, 4)
            pct = round(delta / abs(b) * 100.0, 2) if b else 0.0
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
            f"[{table_label}] Componente marginal: '{comp}' altera o resultado em menos de 1% "
            f"({'; '.join(info)}) — contribuição possivelmente dentro do ruído experimental."
        )
    by_metric = {}
    for e in entries:
        by_metric.setdefault(e["metric"], []).append(e)
    base = reference["baseline"]
    for metric, group in by_metric.items():
        if base and _is_number(base["values"].get(metric)):
            total = abs(reference["values"][metric] - base["values"][metric])
            basis = f"ganho total {reference['component']} vs {base['component']}"
        elif len(group) >= 2:
            total = sum(abs(e["delta"]) for e in group)
            basis = "soma das variações de todos os componentes"
        else:
            continue
        if not total:
            continue
        top = max(group, key=lambda e: abs(e["delta"]))
        share = abs(top["delta"]) / total
        if share >= DOMINANT_SHARE:
            alerts.append(
                f"[{table_label}] Componente dominante: '{top['component']}' responde por {share:.0%} "
                f"do ganho em {metric} ({basis} = {total:.4g}); os demais componentes têm papel secundário."
            )
    return alerts


def analyze_ablation_tables(paper_id):
    """Rank ablated components by impact (Δ / Δ%) and flag marginal or dominant contributions."""
    corpus = pdf_parser.get_paper_corpus(_paper_id(paper_id))
    text = corpus["text"]
    tables = [
        t
        for t in parse_benchmark_tables(text, corpus["pages"], corpus["captions"])
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


# --------------------------------------------------------------------------- #13 prompts & decoding

MAX_PROMPTS = 30
MAX_PROMPT_CHARS = 4000
_PROMPT_LABEL = re.compile(
    r"^[ \t>*_#]*(?P<label>(?:system|user|assistant)\s+(?:prompt|message)|prompt\s+template|(?:few-shot\s+|cot\s+)?"
    r"prompt(?:\s+\d+)?|instructions?|system|user)[ \t*_]*:[ \t]*(?P<rest>[^\n]*)$",
    re.I | re.M,
)
_FENCE = re.compile(r"^[ \t]*```[ \t]*(?P<lang>[\w+-]*)[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$", re.S | re.M)
_QUOTED = re.compile(r"[\"“]([^\"“”]{20,1500})[\"”]")
_VAR_DOUBLE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")
_VAR_SINGLE = re.compile(r"(?<![{\\$\w])\{([A-Za-z_][\w.-]*)\}(?!\})")
_VAR_TAG = re.compile(r"<(?!/)([a-z_][a-z0-9_-]{1,30})>", re.I)
_HTML_TAGS = frozenset("a b i u p br hr em li ol ul div span table tr td th sup sub code pre img s".split())
_PROMPT_CUES = re.compile(
    r"let'?s think step by step|you are an? (?:expert|helpful|assistant|\w+ (?:assistant|expert))|"
    r"answer the (?:following )?question|respond (?:only )?(?:in|with) json|^\s*(?:question|answer|context)\s*:",
    re.I | re.M,
)
_PROMPT_HINT = re.compile(r"\bprompts?\b|\binstructions?\b|\btemplate\b", re.I)

_HP_SEP = (
    r"(?:\s*(?:=|:|≈|\bof\b|\bis\b|\bwas\b|\bset\s+to\b|\bequal\s+to\b|\bto\b|\bat\b|\bfixed\s+at\b|\(\s*)\s*)*\s*"
)
_HP_NUM = r"(?P<v>\d+(?:\.\d+)?|\.\d+)"
_HYPERPARAMS = (
    ("temperature", r"\b(?:sampling\s+)?(?:temperature|temp)\b(?:\s*\(\s*T\s*\))?"),
    ("temperature", r"(?<![\w.])T(?=\s*=)"),
    ("top_p", r"\btop[-_ ]?p\b|\bnucleus\s+sampling(?:\s+with)?(?:\s+(?:p|threshold|probability))?|\bnucleus\s+p\b"),
    ("top_k", r"\btop[-_ ]?k(?:\s+sampling)?\b"),
    (
        "max_tokens",
        r"\bmax(?:imum)?[-_ ]?(?:number\s+of\s+)?(?:new[-_ ]|generated[-_ ]|generation[-_ ]|output[-_ ])?"
        r"(?:tokens|length|gen(?:eration)?[-_ ]len(?:gth)?)\b",
    ),
    ("repetition_penalty", r"\brepetition[-_ ]penalty\b"),
    ("beam_size", r"\b(?:beam[-_ ](?:size|width)|num[-_]beams|number\s+of\s+beams)\b"),
    ("seed", r"\b(?:random\s+)?seed\b"),
)
_HP_PATTERNS = [
    (key, re.compile(rf"(?:{name}){_HP_SEP}{_HP_NUM}(?![\w.]*\d)", 0 if name.startswith("(?<!") else re.I))
    for key, name in _HYPERPARAMS
]  # the bare `T = 0.2` form stays case-sensitive
_HP_REVERSED = (
    (
        "beam_size",
        re.compile(
            r"\b(?P<v>\d+)\s+beams\b|\bbeam\s+search\s+with\s+(?:a\s+)?(?:beam\s+)?"
            r"(?:size|width)?\s*(?:of\s+)?(?P<v2>\d+)\b",
            re.I,
        ),
    ),
    ("max_tokens", re.compile(r"\bup\s+to\s+(?P<v>\d+)\s+(?:new\s+)?tokens\b", re.I)),
)
_HP_RANGES = {
    "temperature": (0, 5),
    "top_p": (0, 1),
    "top_k": (1, 100000),
    "max_tokens": (1, 10**7),
    "repetition_penalty": (0.5, 5),
    "beam_size": (1, 1000),
    "seed": (0, 2**63),
}
_HP_INT = {"top_k", "max_tokens", "beam_size", "seed"}
_DECODING = (
    ("greedy", r"\bgreedy(?:\s+decoding)?\b"),
    ("beam_search", r"\bbeam\s+search\b"),
    ("nucleus_sampling", r"\bnucleus\s+sampling\b|\btop[-_ ]?p\s+sampling\b"),
    ("top_k_sampling", r"\btop[-_ ]?k\s+sampling\b"),
    ("self_consistency", r"\bself[-_ ]consistency\b"),
)


def prompt_variables(text):
    """`{var}`, `{{var}}` and `<var>` placeholders, in order of appearance (verbatim names)."""
    found = []
    for m in sorted(
        [*_VAR_DOUBLE.finditer(text), *_VAR_SINGLE.finditer(text), *_VAR_TAG.finditer(text)], key=lambda m: m.start()
    ):
        name = m.group(1)
        if m.re is _VAR_TAG and (
            name.lower() in _HTML_TAGS
            or f"</{name}>" not in text
            and name.lower() not in ("context", "question", "input", "answer", "document", "query")
        ):
            continue
        if name not in found:
            found.append(name)
    return found


def _is_prompt_like(text):
    return bool(
        _VAR_DOUBLE.search(text)
        or _VAR_SINGLE.search(text)
        or _PROMPT_CUES.search(text)
        or any(m.group(1).lower() not in _HTML_TAGS for m in _VAR_TAG.finditer(text))
    )


def _labeled_block(text, m):
    """Body of `Label: ...` — rest of the line plus following lines up to a blank line/next label/fence."""
    rest, pos = m.group("rest"), m.end()
    fence = _FENCE.match(text, pos + 1) if not rest.strip() else None
    if fence:
        return fence.group("body").rstrip("\n"), fence.end()
    lines, end = ([rest] if rest.strip() else []), m.end()
    for line_m in re.finditer(r"\n([^\n]*)", text[pos:]):
        line = line_m.group(1)
        if not line.strip():
            if lines:
                break
            continue
        if _PROMPT_LABEL.match(line) or line.strip().startswith("```"):
            break
        lines.append(line)
        end = pos + line_m.end()
    return "\n".join(lines).strip("\n"), end


def _find_prompts(text):
    found, spans = [], []

    def free(s, e):
        return not any(a < e and s < b for a, b in spans)

    def add(start, end, kind, label, body):
        body = body.strip("\n")
        if len(body.strip()) < 8 or not free(start, end):
            return
        spans.append((start, end))
        found.append({"start": start, "type": kind, "label": label, "text": body[:MAX_PROMPT_CHARS]})

    labels = list(_PROMPT_LABEL.finditer(text))
    for m in labels:
        label = re.sub(r"\s+", " ", m.group("label")).strip()
        body, end = _labeled_block(text, m)
        low = label.lower()
        kind = (
            "system"
            if low.startswith("system")
            else "user"
            if low.startswith("user")
            else "assistant"
            if low.startswith("assistant")
            else "template"
            if "template" in low
            else "instruction"
            if low.startswith("instruction")
            else "prompt"
        )
        # a lone `Instruction:`/`User:` line in prose must still look like a prompt
        if kind in ("instruction", "user", "prompt") and not _is_prompt_like(body) and len(body) < 40:
            continue
        add(m.start(), max(end, m.end()), kind, label.title() if label.islower() else label, body)
    for m in _FENCE.finditer(text):
        body, lang = m.group("body"), m.group("lang").lower()
        if lang == "json" or (body.lstrip().startswith(("{", "[")) and not _VAR_SINGLE.search(body)):
            continue
        context = text[max(0, m.start() - 300) : m.start()]
        if lang in ("python", "py", "bash", "sh") and not _is_prompt_like(body):
            continue
        if _is_prompt_like(body) or _PROMPT_HINT.search(context):
            add(m.start(), m.end(), "code_block", "Code block", body)
    for m in _QUOTED.finditer(text):
        if _is_prompt_like(m.group(1)):
            add(m.start(), m.end(), "quoted", "Quoted prompt", m.group(1))
    for m in re.finditer(r"(?:^|\n\s*\n)(?P<para>(?:[^\n]+\n?)+)", text):
        para = m.group("para")
        if _is_prompt_like(para) and (
            _VAR_DOUBLE.search(para) or _VAR_SINGLE.search(para) or _PROMPT_CUES.search(para)
        ):
            s = m.start("para")
            add(s, s + len(para), "inline", "Prompt text", para.rstrip())
    found.sort(key=lambda p: p["start"])
    return found[:MAX_PROMPTS]


def _num(raw, key):
    value = float(raw)
    if key in _HP_INT:
        if not value.is_integer():
            return None
        value = int(value)
    lo, hi = _HP_RANGES[key]
    return value if lo <= value <= hi else None


def extract_hyperparameters(text, pages=None):
    """Decoding hyperparameters → ({name: value | [values...]}, evidence list)."""
    hits = []
    for key, rx in _HP_PATTERNS:
        for m in rx.finditer(text):
            value = _num(m.group("v"), key)
            if key == "temperature" and m.group(0).lstrip().startswith("T") and (value is None or value > 2):
                continue
            if value is not None:
                hits.append((m.start(), key, value, m.group(0)))
    for key, rx in _HP_REVERSED:
        for m in rx.finditer(text):
            raw = m.group("v") or (m.groupdict().get("v2"))
            value = _num(raw, key) if raw else None
            if value is not None and not any(k == key and abs(s - m.start()) < 40 for s, k, _, _ in hits):
                hits.append((m.start(), key, value, m.group(0)))
    hits.sort()
    params, evidence = {}, []
    for start, key, value, quote in hits:
        current = params.get(key)
        if current is None:
            params[key] = value
        elif isinstance(current, list):
            if value not in current:
                current.append(value)
        elif current != value:
            params[key] = [current, value]
        evidence.append(
            {
                "parameter": key,
                "value": value,
                "quote": re.sub(r"\s+", " ", quote).strip(),
                "page": pdf_parser.page_at(pages, start) if pages else None,
            }
        )
    strategies = [name for name, rx in _DECODING if re.search(rx, text, re.I)]
    if strategies:
        params["decoding_strategy"] = strategies[0] if len(strategies) == 1 else strategies
    return params, evidence


def extract_json_schemas(text):
    """JSON objects shown in the paper (```json fences and balanced `{ "key": ... }` blocks), verbatim."""
    import json

    out, spans = [], []
    for m in _FENCE.finditer(text):
        body = m.group("body").strip()
        if m.group("lang").lower() == "json" or (body.startswith(("{", "[")) and re.search(r"\"[\w ]+\"\s*:", body)):
            out.append(body)
            spans.append((m.start(), m.end()))
    for m in re.finditer(r"\{", text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        got = pdf_parser.balanced(text, m.start())
        if not got:
            continue
        inner, end = got
        block = "{" + inner + "}"
        if not re.match(r"\{\s*\"[^\"\n]{1,60}\"\s*:", block) or len(block) > MAX_PROMPT_CHARS:
            continue
        try:
            json.loads(block)
        except ValueError:
            if not re.search(r"\"[^\"\n]+\"\s*:\s*(?:\"|\[|\{|\d|true|false|null|<|\.\.\.)", block):
                continue
        out.append(block)
        spans.append((m.start(), end))
    seen, unique = set(), []
    for block in out:
        key = re.sub(r"\s+", "", block)
        if key not in seen:
            seen.add(key)
            unique.append(block)
    return unique


def extract_prompts_and_decoding(paper_id):
    """Prompt templates (placeholders kept verbatim), decoding hyperparameters and JSON output schemas."""
    corpus = pdf_parser.get_paper_corpus(_paper_id(paper_id))
    text, pages = corpus["text"], corpus["pages"]
    from noul.schemas.technical import PromptExtractionResult

    prompts, variables = [], []
    for i, p in enumerate(_find_prompts(text), 1):
        names = prompt_variables(p["text"])
        variables += [n for n in names if n not in variables]
        prompts.append(
            {
                "index": i,
                "type": p["type"],
                "label": p["label"],
                "text": p["text"],
                "variables": names,
                "page": pdf_parser.page_at(pages, p["start"]),
                "section": pdf_parser.section_at(corpus["sections"], p["start"]),
            }
        )
    params, evidence = extract_hyperparameters(text, pages)
    schemas = extract_json_schemas(text)
    result = PromptExtractionResult(
        paper_id=corpus["paper_id"], prompts=prompts, hyperparameters=params, json_schemas=schemas
    ).model_dump()
    warnings = list(corpus["warnings"])
    if not prompts:
        warnings.append("No prompt templates detected.")
    if not params:
        warnings.append("No decoding hyperparameters (temperature, top_p, top_k, max_tokens, ...) reported.")
    result.update(
        {
            "title": corpus["title"],
            "source": corpus["source"],
            "variables": variables,
            "hyperparameter_evidence": evidence,
            "warnings": warnings,
        }
    )
    return result


# --------------------------------------------------------------------------- #14 failure modes & critical assumptions

_PRIORITY_SECTIONS = re.compile(
    r"limitation|error\s+analysis|failure|discussion|threat|validity|broader\s+impact|appendix|weakness|caveat", re.I
)

_ABBR_DOT = re.compile(r"\b(?:i\.i\.d|e\.g|i\.e|et\s+al|vs|cf|fig|tab|eq|sec|dr|mr|prof|no|vol|pp|approx)\.", re.I)

_FAILURE_CATEGORIES = (
    (
        "long_context",
        re.compile(
            r"\b(?:long[-\s]context|sequence\s+length|context\s+window|context\s+length|lost\s+in\s+the\s+middle|"
            r"quadratic\s+complexity|memory\s+overflow|out[-\s]of[-\s]memory|\bOOM\b|long\s+sequences|long\s+inputs)\b|"
            r"\bO\(\s*[nNlL]\s*(?:\^\s*2|²)\s*\)|>\s*(?:8|16|32|64|128)\s*k\b",
            re.I,
        ),
    ),
    (
        "noise_sensitivity",
        re.compile(
            r"\b(?:noisy|noise|perturbations?|adversarial|corrupted|missing\s+data|prompt\s+sensitivity|"
            r"sensitive\s+to\s+prompts?|brittle|outliers?|malformed\s+prompts?|typos?)\b",
            re.I,
        ),
    ),
    (
        "out_of_distribution",
        re.compile(
            r"\b(?:out[-\s]of[-\s]distribution|\bOOD\b|domain\s+shift|distribution\s+shift|covariate\s+shift|"
            r"generaliz(?:e|es|ing|ation)\s+to|unseen\s+domains?|cross[-\s]lingual|external\s+validity|"
            r"non[-\s]english|low[-\s]resource\s+languages?)\b",
            re.I,
        ),
    ),
    (
        "latency_or_compute",
        re.compile(
            r"\b(?:latency|slow(?:er)?|computational\s+cost|compute\s+cost|overhead|inference\s+time|"
            r"inference\s+cost|memory\s+footprint|bottleneck|multi[-\s]step|multiple\s+inference\s+calls|"
            r"low\s+throughput|high\s+vram|energy\s+consumption)\b",
            re.I,
        ),
    ),
    (
        "sample_or_population_bias",
        re.compile(
            r"\b(?:small\s+sample|single[-\s]center|retrospective|selection\s+bias|attrition|"
            r"short\s+follow[-\s]up|in\s+vitro|animal\s+models?|mice|murine|pre[-\s]?clinical|"
            r"convenience\s+sample|small\s+cohort)\b",
            re.I,
        ),
    ),
)

_GENERAL_LIMITATION = re.compile(
    r"\b(?:limitations?|drawbacks?|weakness(?:es)?|fails?\s+(?:when|to|on|under|if)|"
    r"struggles?\s+(?:with|to|on|when)|does\s+not\s+support|cannot\s+handle|unable\s+to|"
    r"degrades?\s+(?:when|on|under|rapidly|significantly)|shortcomings?|caveats?)\b",
    re.I,
)

_SEVERITY_HIGH = re.compile(
    r"\b(?:fails?|collapses?|severe(?:ly)?|prohibitive(?:ly)?|critical(?:ly)?|cannot|can't|"
    r"only\s+in\s+vitro|catastrophic(?:ally)?|drastic(?:ally)?|unusable)\b",
    re.I,
)
_SEVERITY_LOW = re.compile(r"\b(?:minor|slight(?:ly)?|marginal(?:ly)?|negligible|modest(?:ly)?)\b", re.I)

_TRIGGER_CLAUSE = re.compile(
    r"(?:\b(?:when|if|under|beyond|above|over|for\s+inputs?|for\s+sequences?|in\s+the\s+presence\s+of|"
    r"on\s+unseen|with\s+noisy|at\s+sequence\s+lengths?)\s+[^.;,\n]{3,90}|"
    r">\s*\d+\s*[kKmM]?(?:\s*tokens?)?|\bO\(\s*[nNlL]\s*(?:\^\s*2|²)\s*\))",
    re.I,
)

_ASSUMPTION_RE = re.compile(
    r"\b(?:assum(?:e|es|ed|ing|ption|ptions)|requires?\s+that|under\s+the\s+(?:condition|assumption)|"
    r"provided\s+that|i\.?i\.?d\.?|independent\s+and\s+identically\s+distributed|stationar(?:y|ity)|"
    r"parallel\s+trends?|exogen(?:eity|ous)|no\s+unmeasured\s+confound(?:ing|ers?)|"
    r"oracle(?:\s+[a-z]+)?|clean\s+labels?)\b",
    re.I,
)

_BASELINE_SUPERIORITY_RE = re.compile(
    r"\b(?:outperformed\s+by|baseline\s+outperforms?|outperforms?\s+(?:our|the\s+proposed)|"
    r"underperforms?|worse\s+than|classical\s+(?:methods?|models?|baselines?)|simple\s+baselines?|"
    r"linear\s+models?|BM25|logistic\s+regression|in\s+low[-\s]data|on\s+small\s+datasets?|"
    r"when\s+N\s+is\s+small)\b",
    re.I,
)

_OURS_ROW_RE = re.compile(
    r"\b(?:ours|our\s+(?:model|method|approach|system|framework)|proposed(?:\s+method|\s+model)?|full\s+model)\b", re.I
)


def _prose_sentences(text, sections, table_spans=()):
    """Yield `(is_priority, section_title, sentence_text)` preserving document order inside each priority tier."""
    protected = _ABBR_DOT.sub(lambda m: m.group(0).replace(".", "prd"), text or "")
    items = []
    for m in _SENTENCE.finditer(protected):
        if any(s <= m.start() < e for s, e in table_spans):
            continue
        raw = m.group(0).replace("prd", ".")
        quote = re.sub(r"\s+", " ", raw).strip()
        if len(quote) < 12 or not re.search(r"[A-Za-z]{3,}", quote):
            continue
        if re.match(r"^(?:#{1,4}\s+|(?:Table|Tabela|Fig(?:ure|\.)|Section)\s+\d+\s*[:.]?$)", quote, re.I):
            continue
        sec = pdf_parser.section_at(sections, m.start()) or ""
        is_prio = bool(_PRIORITY_SECTIONS.search(sec))
        items.append((0 if is_prio else 1, m.start(), sec, quote))
    items.sort(key=lambda x: (x[0], x[1]))
    return items


def _severity_for(sentence):
    if _SEVERITY_HIGH.search(sentence):
        return "high"
    if _SEVERITY_LOW.search(sentence):
        return "low"
    return "medium"


def _trigger_for(sentence, match_text):
    m = _TRIGGER_CLAUSE.search(sentence)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip(" ,;.")
    return re.sub(r"\s+", " ", match_text).strip()


def _extract_operational_limits(text):
    limits = {}
    ctx_patterns = (
        re.compile(
            r"(?:context\s+(?:window|length)|sequence\s+length|max(?:imum)?\s+(?:context|sequence|input)"
            r"(?:\s+(?:length|window|tokens?))?|beyond|exceeds?|>|>=)\s*(?:of\s*|[:=]\s*)?"
            r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>[kKmM])?\s*(?:tokens?\b|context\b)?",
            re.I,
        ),
        re.compile(r"\b(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>[kK])\s*tokens?\b", re.I),
        re.compile(r">\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[kK])\b", re.I),
    )
    for rx in ctx_patterns:
        for m in rx.finditer(text or ""):
            raw_n = float(m.group("num").replace(",", ""))
            unit = (m.group("unit") or "").lower()
            val = int(raw_n * (1000 if unit == "k" else 1_000_000 if unit == "m" else 1))
            if 128 <= val <= 10_000_000:
                limits["max_context_tokens"] = val
                break
        if "max_context_tokens" in limits:
            break

    m_samp = re.search(
        r"\b(?:min(?:imum)?\s+sample(?:\s+size)?(?:\s+of)?|sample\s+size(?:\s+of)?|\b[nN]\s*=\s*|cohort\s+of\s+)"
        r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:patients?|participants?|subjects?|samples?|examples?|instances?|individuals?)?\b",
        text or "",
        re.I,
    )
    if m_samp:
        n_val = int(m_samp.group(1).replace(",", ""))
        if 1 <= n_val <= 10_000_000:
            limits["min_sample_size"] = n_val

    m_lat = re.search(
        r"(?:latency(?:\s+of|\s*[:=])?\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<u1>ms|milliseconds?|s|seconds?)\b|"
        r"\b(?P<v2>\d+(?:\.\d+)?)\s*(?P<u2>ms|milliseconds?)\s+(?:latency|per\s+(?:token|query|step|request)))",
        text or "",
        re.I,
    )
    if m_lat:
        v = float(m_lat.group("v1") or m_lat.group("v2"))
        u = (m_lat.group("u1") or m_lat.group("u2") or "ms").lower()
        ms = v * 1000.0 if u.startswith("s") else v
        limits["max_latency_ms"] = int(ms) if ms.is_integer() else round(ms, 2)

    langs = []
    for lang in ("English", "Chinese", "Spanish", "Portuguese", "French", "German", "Japanese", "Arabic"):
        if re.search(rf"\b{lang}(?:[-\s]only)?\b", text or "", re.I):
            if lang not in langs:
                langs.append(lang)
    if langs:
        limits["supported_languages"] = langs
    return limits


def _table_baseline_superiority(paper_id, corpus, inspect_fulltext=True):
    """Inspect benchmark tables for metrics where a non-ablation baseline outperforms 'Ours'/proposed row."""
    scenarios = []
    tables = parse_benchmark_tables(corpus["text"], corpus["pages"], corpus["captions"])
    if inspect_fulltext:
        try:
            ext = extract_benchmark_tables(paper_id)
            if ext and ext.get("tables"):
                tables = ext["tables"]
        except Exception:
            pass
    title_words = {
        w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", corpus.get("title") or "") if w.lower() not in _STOP
    }
    for tb in tables:
        if tb.get("table_id") == "inline_metrics" or tb.get("format") == "inline":
            continue
        metrics_map = tb.get("normalized_metrics") or {}
        if len(metrics_map) < 2:
            continue
        labels = list(metrics_map.keys())
        ours_label = next((lbl for lbl in labels if _OURS_ROW_RE.search(lbl) and not _ABL_VARIANT.search(lbl)), None)
        if ours_label is None and title_words:
            ours_label = next(
                (
                    lbl
                    for lbl in labels
                    if any(w in lbl.lower() for w in title_words)
                    and not _ABL_VARIANT.search(lbl)
                    and not _ABL_BASE.search(lbl)
                ),
                None,
            )
        if not ours_label:
            continue
        ours_vals = metrics_map.get(ours_label) or {}
        tb_label = tb.get("label") or tb.get("table_id") or "Table"
        for other_label, other_vals in metrics_map.items():
            if other_label == ours_label or _ABL_VARIANT.search(other_label):
                continue
            for metric, ours_v in ours_vals.items():
                other_v = other_vals.get(metric)
                if not (_is_number(ours_v) and _is_number(other_v)):
                    continue
                lower_better = metric in LOWER_IS_BETTER
                if (not lower_better and other_v > ours_v) or (lower_better and other_v < ours_v):
                    msg = (
                        f"{tb_label} ({metric}): baseline '{other_label}' ({other_v}) outperforms "
                        f"'{ours_label}' ({ours_v})."
                    )
                    if msg not in scenarios:
                        scenarios.append(msg)
    return scenarios


def detect_failure_modes(paper_id: str, inspect_fulltext: bool = True) -> dict:
    """Detect categorized failure modes, critical assumptions, baseline superiority scenarios and operational limits."""
    from noul.schemas.technical import FailureModeEntry, FailureModesReport

    pid = _paper_id(paper_id)
    if inspect_fulltext:
        corpus = pdf_parser.get_paper_corpus(pid)
        p = corpus.get("paper") or _engine.resolve_paper(pid)
        full_t = corpus.get("text") or ""
        abs_t = p.get("abstract") or ""
        if abs_t and abs_t not in full_t:
            combined = f"{p.get('title') or ''}\n\n{abs_t}\n\n{full_t}".strip()
            corpus = pdf_parser.build_corpus(combined, paper=p, source=corpus.get("source") or "fulltext")
    else:
        p = _engine.resolve_paper(pid)
        text = "\n\n".join(x for x in (p.get("title") or "", p.get("abstract") or "") if x)
        corpus = pdf_parser.build_corpus(text, paper=p, source="abstract")

    domain = p.get("domain")
    if not domain:
        try:
            from noul.providers import domain_classifier

            domain = domain_classifier.resolve_domain(paper=p).value
        except Exception:
            domain = "general_stem"

    text = corpus["text"]
    raw_tables = parse_benchmark_tables(text, corpus["pages"], corpus["captions"])
    table_spans = [tb["_span"] for tb in raw_tables if tb.get("_span") and tb.get("format") != "inline"]
    sentences = _prose_sentences(text, corpus["sections"], table_spans=table_spans)

    failure_modes = []
    seen_cat_quote = set()
    assumptions = []
    baseline_scenarios = []

    for _, _, _sec, sent in sentences:
        matched_specific = False
        for category, rx in _FAILURE_CATEGORIES:
            m = rx.search(sent)
            if m:
                matched_specific = True
                key = (category, sent)
                if key not in seen_cat_quote:
                    seen_cat_quote.add(key)
                    failure_modes.append(
                        FailureModeEntry(
                            category=category,
                            severity=_severity_for(sent),
                            description=sent,
                            trigger_condition=_trigger_for(sent, m.group(0)),
                            evidence_quote=sent,
                        )
                    )
        if not matched_specific:
            m_gen = _GENERAL_LIMITATION.search(sent)
            if m_gen:
                key = ("general_limitation", sent)
                if key not in seen_cat_quote:
                    seen_cat_quote.add(key)
                    failure_modes.append(
                        FailureModeEntry(
                            category="general_limitation",
                            severity=_severity_for(sent),
                            description=sent,
                            trigger_condition=_trigger_for(sent, m_gen.group(0)),
                            evidence_quote=sent,
                        )
                    )

        if _ASSUMPTION_RE.search(sent) and sent not in assumptions:
            assumptions.append(sent)

        if _BASELINE_SUPERIORITY_RE.search(sent) and sent not in baseline_scenarios:
            baseline_scenarios.append(sent)

    for tbl_scenario in _table_baseline_superiority(pid, corpus, inspect_fulltext=inspect_fulltext):
        if tbl_scenario not in baseline_scenarios:
            baseline_scenarios.append(tbl_scenario)

    op_limits = _extract_operational_limits(text)

    report = FailureModesReport(
        paper_id=corpus["paper_id"] or p.get("id") or pid,
        title=corpus["title"] or p.get("title") or "",
        domain=domain or "general_stem",
        failure_modes=failure_modes,
        critical_assumptions=assumptions,
        baseline_superiority_scenarios=baseline_scenarios,
        operational_limits=op_limits,
    )
    return report.model_dump(mode="json")


# --------------------------------------------------------------------------- #15 arXiv version diff


def diff_arxiv_versions(arxiv_id: str, from_version: str = "v1", to_version: str = "v2") -> dict:
    """Compare two versions of an arXiv paper (metadata, metrics, claim attenuations, new baselines/datasets)."""
    from noul.providers import arxiv_client

    base_id, v_old, v_new = arxiv_client.parse_arxiv_version_args(
        arxiv_id, from_version=from_version, to_version=to_version
    )
    snap_old = arxiv_client.fetch_version_snapshot(base_id, v_old)
    snap_new = arxiv_client.fetch_version_snapshot(base_id, v_new)
    report = arxiv_client.diff_snapshots(base_id, v_old, snap_old, v_new, snap_new)
    return report.model_dump(mode="json")
