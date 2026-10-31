"""Phase 4 (#15): arXiv temporal version fetcher and snapshot diff analyzer.

All HTTP requests go through `noul.net.http._http_get` (respecting SSRF guard and byte caps)."""

import difflib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from noul import errors as _errors
from noul import ids as _ids
from noul.net import cache as _cache
from noul.net import http as _nhttp
from noul.providers import crossref as _crossref
from noul.providers import openalex as _openalex
from noul.providers import pdf_parser
from noul.providers import s2 as _s2
from noul.schemas.technical import ArxivVersionDiffReport, VersionChangeItem
from noul.tools import technical_tools

_ARXIV_BASE_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})$", re.I)
_VER_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?P<ver>v\d+)$", re.I)
_VER_TOKEN_RE = re.compile(r"^v?(?P<num>\d+)$", re.I)

_STRONG_CLAIM_RE = re.compile(
    r"\b(?:state[-\s]of[-\s]the[-\s]art|\bsota\b|proves?|always|outperforms?\s+all|first|novel|"
    r"significant(?:ly)?|guarantees?|universally|unprecedented|superior\s+to\s+all|beats?\s+all)\b",
    re.I,
)
_MODERATE_CLAIM_RE = re.compile(
    r"\b(?:competitive|suggests?|often|comparable|empirical|promising|preliminary|may|might|"
    r"partially|modest(?:ly)?|on\s+par\s+with)\b",
    re.I,
)
_METHOD_CHANGE_RE = re.compile(
    r"\b(?:architecture|layers?|parameters?|sample\s+size|cohort|datasets?|protocol|training|"
    r"optimizer|learning\s+rate|batch\s+size|ablation|evaluation|fine[-\s]?tun\w+|quantiz\w+|"
    r"context\s+(?:window|length)|tokens?)\b",
    re.I,
)

_KNOWN_ENTITIES = (
    "MMLU",
    "GSM8K",
    "HumanEval",
    "MBPP",
    "MATH",
    "HellaSwag",
    "ARC-Challenge",
    "TruthfulQA",
    "Big-Bench",
    "BBH",
    "DROP",
    "SQuAD",
    "GLUE",
    "SuperGLUE",
    "ImageNet",
    "SWE-bench",
    "GPQA",
    "LiveCodeBench",
    "MT-Bench",
    "AlpacaEval",
    "MedQA",
    "PubMedQA",
    "GPT-4o",
    "GPT-4",
    "GPT-3.5",
    "Claude-3.5",
    "Claude-3",
    "Claude",
    "Llama-3.1",
    "Llama-3",
    "Llama-2",
    "Mistral",
    "Mixtral",
    "Qwen-2.5",
    "Qwen-2",
    "Qwen",
    "Gemini",
    "DeepSeek-R1",
    "DeepSeek-V3",
    "DeepSeek",
    "Gemma",
    "Phi-3",
    "RoBERTa",
    "DeBERTa",
    "BM25",
    "XGBoost",
)
_HYPHEN_ENTITY_RE = re.compile(
    r"\b(?:GPT|Llama|Claude|Qwen|DeepSeek|Mistral|Mixtral|Gemma|Phi|BERT|T5)-[A-Za-z0-9.-]+\b"
)

_SENT_METRIC_PATTERNS = (
    re.compile(
        r"\b(?P<name>MMLU|GSM-?8K|Human-?Eval|MBPP|MATH|HellaSwag|ARC[-_ ]?(?:Challenge|c)|TruthfulQA|"
        r"BLEU|ROUGE-?L|F1(?:-score)?|AUROC|AUC|accuracy|perplexity|PPL|latency|throughput|pass@1)\b"
        r"[^0-9\n.;]{0,30}?(?P<val>\d+(?:\.\d+)?)\s*%?",
        re.I,
    ),
    re.compile(
        r"(?P<val>\d+(?:\.\d+)?)\s*%?\s*(?:accuracy|score|points|pass@1|F1|BLEU)?\s*"
        r"(?:on|in|for)\s+(?:the\s+)?"
        r"(?P<name>MMLU|GSM-?8K|Human-?Eval|MBPP|MATH|HellaSwag|ARC[-_ ]?(?:Challenge|c)|TruthfulQA|"
        r"BLEU|ROUGE-?L|F1(?:-score)?|AUROC|AUC|accuracy|perplexity|PPL|latency|throughput)\b",
        re.I,
    ),
)


def _clean_arxiv_token(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    s = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    return s.strip().rstrip("/")


def _norm_version(v: str | None, default: str) -> str:
    if v is None or not str(v).strip():
        return default
    m = _VER_TOKEN_RE.match(str(v).strip())
    if not m:
        raise _errors.HTTPError(400, f"Invalid arXiv version specifier: {v!r}")
    num = int(m.group("num"))
    if num < 1:
        raise _errors.HTTPError(400, f"arXiv version number must be >= 1 (got {v!r})")
    return f"v{num}"


def parse_arxiv_version_args(
    arxiv_id: str,
    from_version: str | None = "v1",
    to_version: str | None = "v2",
) -> tuple[str, str, str]:
    """Normalize `(arxiv_id, from_version, to_version)` -> `(base_id, v_old, v_new)`."""
    if not isinstance(arxiv_id, str) or not arxiv_id.strip():
        raise _errors.HTTPError(400, "arxiv_id must be a non-empty string")
    raw = arxiv_id.strip()

    # Check range/pair syntax: "2401.12345v1,2401.12345v2" or "2401.12345v1..v3"
    if "," in raw or ".." in raw:
        sep = "," if "," in raw else ".."
        parts = [p.strip() for p in raw.split(sep) if p.strip()]
        if len(parts) != 2:
            raise _errors.HTTPError(400, f"Invalid arXiv version pair: {arxiv_id!r}")
        left = _clean_arxiv_token(parts[0])
        right = _clean_arxiv_token(parts[1])
        m_left = _VER_SUFFIX_RE.match(left)
        if not m_left:
            raise _errors.HTTPError(400, f"First identifier in pair must include a version (e.g. v1): {parts[0]!r}")
        base_id = m_left.group("base")
        v_from = _norm_version(m_left.group("ver"), "v1")
        if _VER_TOKEN_RE.match(right):
            v_to = _norm_version(right, "v2")
        else:
            m_right = _VER_SUFFIX_RE.match(right)
            if not m_right:
                raise _errors.HTTPError(
                    400, f"Second identifier in pair must include a version (e.g. v2): {parts[1]!r}"
                )
            if m_right.group("base").lower() != base_id.lower():
                raise _errors.HTTPError(
                    400, f"Cannot diff different arXiv papers: {base_id!r} vs {m_right.group('base')!r}"
                )
            v_to = _norm_version(m_right.group("ver"), "v2")
    else:
        cleaned = _clean_arxiv_token(raw)
        m_ver = _VER_SUFFIX_RE.match(cleaned)
        base_id = m_ver.group("base") if m_ver else cleaned
        v_from = _norm_version(from_version, "v1")
        v_to = _norm_version(to_version, "v2")

    if not _ARXIV_BASE_RE.match(base_id):
        raise _errors.HTTPError(400, f"Invalid arXiv identifier: {arxiv_id!r}")
    if v_from == v_to:
        raise _errors.HTTPError(400, f"from_version and to_version must differ (both are {v_from!r})")
    return base_id, v_from, v_to


def _parse_atom_entry(xml_txt: str, base_id: str, version: str) -> dict:
    try:
        root = ET.fromstring(xml_txt)
    except ET.ParseError as e:
        raise _errors.HTTPError(502, f"Invalid XML from arXiv API: {e}") from e
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        raise _errors.HTTPError(404, f"arXiv version {base_id}{version} not found")
    title = _ids._clean_text(entry.findtext("a:title", "", ns))
    summary = _ids._clean_text(entry.findtext("a:summary", "", ns))
    if not title and not summary:
        raise _errors.HTTPError(404, f"arXiv version {base_id}{version} not found")
    authors = [
        _ids._clean_text(a.findtext("a:name", "", ns))
        for a in entry.findall("a:author", ns)
        if a.findtext("a:name", "", ns)
    ]
    published = (entry.findtext("a:published", "", ns) or "").strip()
    updated = (entry.findtext("a:updated", "", ns) or "").strip()
    ver_id = f"{base_id}{version}"
    return {
        "id": f"arxiv:{ver_id}",
        "arxiv_id": ver_id,
        "title": title,
        "abstract": summary,
        "summary": summary,
        "authors": authors,
        "published": published,
        "updated": updated,
        "year": int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None,
        "journal": "arXiv",
        "open_access": True,
        "pdf_url": f"https://arxiv.org/pdf/{ver_id}.pdf",
        "sources": ["arxiv"],
    }


def _enrich_snapshot_text(snap: dict, ver_id: str) -> dict:
    out = dict(snap)
    title = out.get("title") or ""
    abstract = out.get("abstract") or out.get("summary") or ""
    out["abstract"] = abstract
    out["summary"] = abstract
    ft_rec = (
        _cache.cache_get(f"ft:arxiv:{ver_id}")
        or _cache.cache_get(f"ft:{ver_id}")
        or ({"text": out["fulltext"]} if out.get("fulltext") else None)
        or ({"text": out["text"]} if out.get("text") else None)
    )
    ft_text = (ft_rec or {}).get("text") or ""
    parts = [x for x in (title, abstract, ft_text) if x]
    out["text"] = "\n\n".join(parts)
    return out


def fetch_version_snapshot(base_id: str, version: str) -> dict:
    """Retrieve a versioned arXiv snapshot (`<base_id><version>`) from cache or the arXiv Atom query API."""
    ver_id = f"{base_id}{version}"
    for key in (f"paper:arxiv:{ver_id}", f"arxiv_ver:{ver_id}", f"paper:{ver_id}"):
        cached = _cache.cache_get(key)
        if cached and isinstance(cached, dict):
            return _enrich_snapshot_text(cached, ver_id)

    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ver_id})
    xml_txt = _nhttp._http_get(url)
    snap = _parse_atom_entry(xml_txt, base_id, version)
    _cache.cache_set(f"paper:arxiv:{ver_id}", snap)
    _cache.cache_set(f"arxiv_ver:{ver_id}", snap)
    return _enrich_snapshot_text(snap, ver_id)


def _extract_metrics_map(text: str) -> dict:
    """Extract `{metric_label: float_val}` from benchmark tables and inline metric mentions in `text`."""
    found: dict = {}
    tables = technical_tools.parse_benchmark_tables(text)
    for tb in tables:
        if tb.get("table_id") == "inline_metrics" or tb.get("format") == "inline":
            rep = (tb.get("normalized_metrics") or {}).get("reported") or {}
            for m_name, val in rep.items():
                if technical_tools._is_number(val):
                    found.setdefault(m_name, float(val))
            continue
        norm = tb.get("normalized_metrics") or {}
        labels = list(norm.keys())
        ours_lbl = next(
            (
                lbl
                for lbl in labels
                if technical_tools._OURS_ROW_RE.search(lbl) and not technical_tools._ABL_VARIANT.search(lbl)
            ),
            None,
        )
        # Record primary/Ours row directly under the metric name
        if ours_lbl and isinstance(norm.get(ours_lbl), dict):
            for m_name, val in norm[ours_lbl].items():
                if technical_tools._is_number(val):
                    found[m_name] = float(val)
        for row_lbl, row_metrics in norm.items():
            if not isinstance(row_metrics, dict):
                continue
            for m_name, val in row_metrics.items():
                if not technical_tools._is_number(val):
                    continue
                if len(labels) == 1 or row_lbl == ours_lbl or m_name not in found:
                    found.setdefault(m_name, float(val))
                found[f"{row_lbl} ({m_name})"] = float(val)

    for rx in _SENT_METRIC_PATTERNS:
        for m in rx.finditer(text or ""):
            name = technical_tools.normalize_metric_name(m.group("name"))
            if name == "MATH" and m.group("name") != "MATH":
                continue
            val = technical_tools.parse_number(m.group("val"))
            if technical_tools._is_number(val):
                found.setdefault(name, float(val))

    for m_n in re.finditer(r"\b(?:[nN]\s*=\s*|sample\s+size(?:\s+of)?\s*)(\d{1,3}(?:,\d{3})+|\d+)\b", text or ""):
        found.setdefault("sample_size", float(m_n.group(1).replace(",", "")))
    return found


def _compute_metric_deltas(text_old: str, text_new: str) -> list:
    m_old = _extract_metrics_map(text_old)
    m_new = _extract_metrics_map(text_new)
    deltas = []
    seen_canonical = set()
    # First pass: canonical / top-level metric names (without "(...)")
    for key in sorted(m_old.keys(), key=lambda k: ("(" in k, k)):
        if key not in m_new:
            continue
        v1, v2 = m_old[key], m_new[key]
        if v1 == v2:
            continue
        if "(" in key:
            inner = key.rsplit("(", 1)[-1].rstrip(")")
            if inner in seen_canonical and m_old.get(inner) == v1 and m_new.get(inner) == v2:
                continue
        else:
            seen_canonical.add(key)
        d = round(v2 - v1, 4)
        v1_out = int(v1) if float(v1).is_integer() and "." not in str(v1) else v1
        v2_out = int(v2) if float(v2).is_integer() and "." not in str(v2) else v2
        deltas.append(
            {
                "metric": key,
                "from_value": v1_out,
                "to_value": v2_out,
                "delta": d,
            }
        )
    return deltas


def _split_clean_sentences(text: str) -> list:
    corpus = pdf_parser.build_corpus(text or "", source="text")
    raw_tables = technical_tools.parse_benchmark_tables(text or "", corpus["pages"], corpus["captions"])
    spans = [tb["_span"] for tb in raw_tables if tb.get("_span") and tb.get("format") != "inline"]
    out = []
    for _, _, _, sent in technical_tools._prose_sentences(text or "", corpus["sections"], table_spans=spans):
        if sent not in out:
            out.append(sent)
    return out


def _detect_claim_shifts(snap_old: dict, snap_new: dict, text_old: str, text_new: str) -> list:
    shifts = []
    title_old = (snap_old.get("title") or "").strip()
    title_new = (snap_new.get("title") or "").strip()
    if title_old and title_new and title_old != title_new:
        old_strong = {m.group(0).lower() for m in _STRONG_CLAIM_RE.finditer(title_old)}
        new_strong = {m.group(0).lower() for m in _STRONG_CLAIM_RE.finditer(title_new)}
        new_mod = {m.group(0).lower() for m in _MODERATE_CLAIM_RE.finditer(title_new)}
        if (old_strong - new_strong) and (new_mod or len(new_strong) < len(old_strong)):
            shifts.append(
                VersionChangeItem(
                    category="claim_attenuation",
                    summary=f"Title attenuated from '{title_old}' to '{title_new}'.",
                    before=title_old,
                    after=title_new,
                )
            )
        else:
            shifts.append(
                VersionChangeItem(
                    category="author_or_title_change",
                    summary=f"Title updated from '{title_old}' to '{title_new}'.",
                    before=title_old,
                    after=title_new,
                )
            )

    sents_old = _split_clean_sentences(text_old)
    sents_new = _split_clean_sentences(text_new)
    old_set = set(sents_old)
    new_set = set(sents_new)
    removed = [s for s in sents_old if s not in new_set]
    added = [s for s in sents_new if s not in old_set]
    used_new = set()

    for s_old in removed:
        best_idx, best_ratio = None, 0.0
        for idx, s_new in enumerate(added):
            if idx in used_new:
                continue
            ratio = difflib.SequenceMatcher(None, s_old.lower(), s_new.lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_idx = ratio, idx
        if best_idx is None or best_ratio < 0.25:
            continue
        s_new = added[best_idx]
        strong_old = {m.group(0).lower() for m in _STRONG_CLAIM_RE.finditer(s_old)}
        strong_new = {m.group(0).lower() for m in _STRONG_CLAIM_RE.finditer(s_new)}
        mod_old = {m.group(0).lower() for m in _MODERATE_CLAIM_RE.finditer(s_old)}
        mod_new = {m.group(0).lower() for m in _MODERATE_CLAIM_RE.finditer(s_new)}

        if (strong_old - strong_new) and ((mod_new - mod_old) or len(strong_new) < len(strong_old)):
            used_new.add(best_idx)
            dropped = ", ".join(sorted(strong_old - strong_new))
            added_mod = ", ".join(sorted(mod_new - mod_old)) or "more moderate phrasing"
            shifts.append(
                VersionChangeItem(
                    category="claim_attenuation",
                    summary=f"Strong claim ({dropped}) softened to {added_mod}.",
                    before=s_old,
                    after=s_new,
                )
            )
        elif (strong_new - strong_old) and ((mod_old - mod_new) or len(strong_new) > len(strong_old)):
            used_new.add(best_idx)
            gained = ", ".join(sorted(strong_new - strong_old))
            shifts.append(
                VersionChangeItem(
                    category="claim_strengthening",
                    summary=f"Claim strengthened with {gained}.",
                    before=s_old,
                    after=s_new,
                )
            )
        elif best_ratio >= 0.40 and (_METHOD_CHANGE_RE.search(s_old) or _METHOD_CHANGE_RE.search(s_new)):
            used_new.add(best_idx)
            shifts.append(
                VersionChangeItem(
                    category="methodology_change",
                    summary="Methodology or experimental setup modified between versions.",
                    before=s_old,
                    after=s_new,
                )
            )
    return shifts


def _detect_new_baselines_or_datasets(text_old: str, text_new: str) -> list:
    low_old = (text_old or "").lower()
    found = []

    for ent in _KNOWN_ENTITIES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(ent)}(?![A-Za-z0-9])", text_new or "", re.I):
            if not re.search(rf"(?<![A-Za-z0-9]){re.escape(ent)}(?![A-Za-z0-9])", low_old, re.I):
                if ent not in found:
                    found.append(ent)

    for m in _HYPHEN_ENTITY_RE.finditer(text_new or ""):
        tok = m.group(0)
        if tok.lower() not in low_old and all(tok.lower() != x.lower() for x in found):
            found.append(tok)

    tables_old = technical_tools.parse_benchmark_tables(text_old or "")
    tables_new = technical_tools.parse_benchmark_tables(text_new or "")
    old_models = {
        lbl.lower()
        for tb in tables_old
        if tb.get("table_id") != "inline_metrics"
        for lbl in (tb.get("normalized_metrics") or {})
    }
    for tb in tables_new:
        if tb.get("table_id") == "inline_metrics":
            continue
        for lbl in tb.get("normalized_metrics") or {}:
            clean_lbl = lbl.strip()
            if (
                clean_lbl
                and clean_lbl.lower() not in old_models
                and clean_lbl.lower() not in low_old
                and not technical_tools._OURS_ROW_RE.search(clean_lbl)
                and all(clean_lbl.lower() != x.lower() for x in found)
            ):
                found.append(clean_lbl)
    return found


def _build_markdown_diff(
    base_id: str,
    v_old: str,
    v_new: str,
    snap_old: dict,
    snap_new: dict,
    title_changed: bool,
    authors_added: list,
    authors_removed: list,
    metric_deltas: list,
    claim_shifts: list,
    new_baselines: list,
) -> str:
    lines = [
        f"## ArXiv Version Diff: {base_id} ({v_old} -> {v_new})",
        "",
        "### Metadata & Authorship Changes",
    ]
    if title_changed:
        lines.append(f"- **Title changed**: `{snap_old.get('title', '')}` -> `{snap_new.get('title', '')}`")
    else:
        lines.append(f"- **Title**: {snap_new.get('title') or snap_old.get('title') or base_id} (unchanged)")
    if authors_added:
        lines.append(f"- **Authors added**: {', '.join(authors_added)}")
    if authors_removed:
        lines.append(f"- **Authors removed**: {', '.join(authors_removed)}")
    if not (title_changed or authors_added or authors_removed):
        lines.append("- No title or authorship changes.")

    lines += ["", "### Metric & Table Number Changes"]
    if metric_deltas:
        for md in metric_deltas:
            lines.append(
                f"- **{md['metric']}**: `{md['from_value']}` -> `{md['to_value']}` (delta: `{md['delta']:+g}`)"
            )
    else:
        lines.append("- No metric deltas detected.")

    lines += ["", "### Claim Shifts & Attenuations"]
    if claim_shifts:
        for cs in claim_shifts:
            lines.append(f"- **[{cs.category}]** {cs.summary}")
            if cs.before:
                lines.append(f'  - `{v_old}`: "{cs.before}"')
            if cs.after:
                lines.append(f'  - `{v_new}`: "{cs.after}"')
    else:
        lines.append("- No significant claim shifts detected.")

    lines += ["", "### New Baselines & Datasets"]
    if new_baselines:
        for nb in new_baselines:
            lines.append(f"- `{nb}`")
    else:
        lines.append("- None added.")

    lines += ["", "### Text Diff Highlights"]
    sents_old = _split_clean_sentences(snap_old.get("text") or snap_old.get("abstract") or "")
    sents_new = _split_clean_sentences(snap_new.get("text") or snap_new.get("abstract") or "")
    udiff = list(difflib.unified_diff(sents_old, sents_new, fromfile=v_old, tofile=v_new, lineterm="", n=1))
    if udiff:
        lines.append("```diff")
        lines.extend(udiff[:30])
        lines.append("```")
    else:
        lines.append("- Texts are identical.")
    return "\n".join(lines)


def diff_snapshots(
    base_id: str,
    v_old: str,
    snap_old: dict,
    v_new: str,
    snap_new: dict,
) -> ArxivVersionDiffReport:
    """Compare two versioned arXiv snapshots and build an `ArxivVersionDiffReport`."""
    title_old = (snap_old.get("title") or "").strip()
    title_new = (snap_new.get("title") or "").strip()
    title_changed = bool(title_old != title_new)

    authors_old = list(snap_old.get("authors") or [])
    authors_new = list(snap_new.get("authors") or [])
    authors_added = [a for a in authors_new if a not in authors_old]
    authors_removed = [a for a in authors_old if a not in authors_new]

    text_old = snap_old.get("text") or "\n\n".join(
        x for x in (title_old, snap_old.get("abstract") or snap_old.get("summary") or "") if x
    )
    text_new = snap_new.get("text") or "\n\n".join(
        x for x in (title_new, snap_new.get("abstract") or snap_new.get("summary") or "") if x
    )

    metric_deltas = _compute_metric_deltas(text_old, text_new)
    claim_shifts = _detect_claim_shifts(snap_old, snap_new, text_old, text_new)
    new_baselines = _detect_new_baselines_or_datasets(text_old, text_new)

    md_diff = _build_markdown_diff(
        base_id=base_id,
        v_old=v_old,
        v_new=v_new,
        snap_old=snap_old,
        snap_new=snap_new,
        title_changed=title_changed,
        authors_added=authors_added,
        authors_removed=authors_removed,
        metric_deltas=metric_deltas,
        claim_shifts=claim_shifts,
        new_baselines=new_baselines,
    )

    return ArxivVersionDiffReport(
        arxiv_base_id=base_id,
        from_version=v_old,
        to_version=v_new,
        title_changed=title_changed,
        authors_added=authors_added,
        authors_removed=authors_removed,
        metric_deltas=metric_deltas,
        claim_shifts=claim_shifts,
        new_baselines_or_datasets=new_baselines,
        markdown_diff=md_diff,
    )


_ARXIV_IN_TEXT_RE = re.compile(r"(?:arxiv[:/\s]*|10\.48550/arxiv\.)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_CONFERENCE_VENUE_RE = re.compile(
    r"\b(?:neurips|neural\s+information\s+processing|icml|international\s+conference\s+on\s+machine\s+learning|"
    r"iclr|learning\s+representations|cvpr|computer\s+vision\s+and\s+pattern\s+recognition|iccv|eccv|"
    r"acl|emnlp|naacl|eacl|coling|aaai|ijcai|kdd|sigkdd|sigir|www|thewebconf|chi|uist|icse|fse|ase|"
    r"osdi|sosp|nsdi|eurosys|usenix|ccs|ndss|sp|isca|micro|hpca|asplos|pldi|popl|oopsla|icde|vldb|sigmod|"
    r"aistats|uai|colt|corl|icra|iros|rss|miccai|ismb|recomb|proceedings|conference|symposium|workshop)\b",
    re.I,
)
_PREPRINT_VENUE_RE = re.compile(r"\b(?:arxiv|corr|biorxiv|medrxiv|ssrn|preprint|techrxiv|chemrxiv)\b", re.I)


def _norm_title_key(title: str) -> str:
    clean = re.sub(r"\\[a-zA-Z]+\b", " ", title or "")
    clean = re.sub(r"[{}]", "", clean)
    return re.sub(r"[^a-z0-9]+", "", clean.lower())


def extract_arxiv_id_from_entry(entry: dict) -> str | None:
    """Extract canonical base arXiv ID (without `vN`) from a parsed BibTeX entry dict if present."""
    fields = entry.get("fields") or entry
    eprint = str(fields.get("eprint") or fields.get("arxiv_id") or "").strip()
    if eprint:
        cleaned = _clean_arxiv_token(eprint)
        m_ver = _VER_SUFFIX_RE.match(cleaned)
        base = m_ver.group("base") if m_ver else cleaned
        if _ARXIV_BASE_RE.match(base):
            return base
    for k in ("journal", "booktitle", "note", "howpublished", "url", "doi", "eid"):
        val = str(fields.get(k) or "")
        m = _ARXIV_IN_TEXT_RE.search(val)
        if m:
            return m.group(1)
    return None


def is_arxiv_preprint_entry(entry: dict) -> bool:
    """Return True when a parsed BibTeX entry represents an arXiv/CoRR preprint rather than a final publication."""
    etype = str(entry.get("entry_type") or "").lower().strip()
    fields = entry.get("fields") or entry
    journal = str(fields.get("journal") or "").strip()
    booktitle = str(fields.get("booktitle") or "").strip()
    howpub = str(fields.get("howpublished") or "").strip()
    archive = str(fields.get("archiveprefix") or "").strip().lower()
    doi = str(fields.get("doi") or "").strip().lower()
    eprint = extract_arxiv_id_from_entry(entry)

    # If it already has a formal non-preprint conference or journal venue, it's not a bare preprint
    formal_venue = booktitle or journal
    if formal_venue and not _PREPRINT_VENUE_RE.search(formal_venue) and etype in ("inproceedings", "article"):
        if not doi.startswith("10.48550/arxiv"):
            return False

    if etype in ("misc", "unpublished", "techreport") and (
        eprint or archive == "arxiv" or "arxiv" in journal.lower() or "arxiv" in howpub.lower()
    ):
        return True
    if _PREPRINT_VENUE_RE.search(journal) or _PREPRINT_VENUE_RE.search(booktitle) or _PREPRINT_VENUE_RE.search(howpub):
        return True
    if doi.startswith("10.48550/arxiv"):
        return True
    if eprint and not formal_venue:
        return True
    return False


def is_conference_venue(venue: str) -> bool:
    """Return True if `venue` is a conference/proceedings rather than a journal."""
    return bool(_CONFERENCE_VENUE_RE.search(venue or ""))


def _is_formal_published_record(rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    venue = str(rec.get("journal") or rec.get("venue") or rec.get("booktitle") or "").strip()
    if not venue or _PREPRINT_VENUE_RE.search(venue):
        return False
    return True


def resolve_published_venue_for_preprint(entry: dict) -> dict | None:
    """Check cache and public scholarly APIs (S2 / Crossref / OpenAlex) for a formal peer-reviewed version
    of an arXiv preprint. Returns a dict with `{venue, entry_type, year, doi, authors, title, arxiv_id}` or None."""
    fields = entry.get("fields") or entry
    raw_title = str(fields.get("title") or "").strip()
    norm_t = _norm_title_key(raw_title)
    arxiv_id = extract_arxiv_id_from_entry(entry)

    candidates = []

    # 1. Check direct cache keys and SQLite cache for matching title or arxiv_id
    if arxiv_id:
        for k in (f"arxiv_pub:{arxiv_id}", f"paper:arxiv:{arxiv_id}", f"paper:{arxiv_id}"):
            cached = _cache.cache_get(k)
            if cached and _is_formal_published_record(cached):
                candidates.append(cached)

    try:
        with _cache._conn() as conn:
            rows = conn.execute("SELECT v FROM kv WHERE k LIKE 'paper:%' OR k LIKE 'arxiv_pub:%'").fetchall()
        for (val_json,) in rows:
            try:
                rec = json.loads(val_json)
            except Exception:
                continue
            if not _is_formal_published_record(rec):
                continue
            rec_ax = _clean_arxiv_token(str(rec.get("arxiv_id") or ""))
            rec_ax_base = m.group("base") if (m := _VER_SUFFIX_RE.match(rec_ax)) else rec_ax
            rec_norm_t = _norm_title_key(rec.get("title") or "")
            if (arxiv_id and rec_ax_base and rec_ax_base.lower() == arxiv_id.lower()) or (
                norm_t
                and rec_norm_t
                and (norm_t == rec_norm_t or difflib.SequenceMatcher(None, norm_t, rec_norm_t).ratio() >= 0.90)
            ):
                candidates.append(rec)
    except Exception:
        pass

    # 2. If not found in cache, query external APIs (all guarded by try/except for offline determinism)
    if not candidates and arxiv_id:
        try:
            url = (
                f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:"
                f"{urllib.parse.quote(arxiv_id, safe='/.')}?fields={_s2.S2_PAPER_FIELDS}"
            )
            p_s2 = _s2._parse_s2_paper(_nhttp._http_json(url))
            if p_s2 and _is_formal_published_record(p_s2):
                candidates.append(p_s2)
        except Exception:
            pass

    if not candidates and raw_title:
        clean_q = re.sub(r"[{}]", "", raw_title).strip()
        for search_fn in (
            lambda q: _crossref._search_crossref(q, limit=5),
            lambda q: _openalex._search_openalex(q, limit=5),
            lambda q: _s2._search_s2(q, limit=5),
        ):
            try:
                results = search_fn(clean_q) or []
            except Exception:
                results = []
            for r in results:
                if not _is_formal_published_record(r):
                    continue
                r_norm = _norm_title_key(r.get("title") or "")
                if (
                    norm_t
                    and r_norm
                    and (norm_t == r_norm or difflib.SequenceMatcher(None, norm_t, r_norm).ratio() >= 0.88)
                ):
                    candidates.append(r)
            if candidates:
                break

    if not candidates:
        return None

    best = candidates[0]
    venue = str(best.get("journal") or best.get("venue") or best.get("booktitle") or "").strip()
    is_conf = is_conference_venue(venue)
    doi = _ids._norm_doi(best.get("doi"))
    if doi and doi.startswith("10.48550/arxiv"):
        doi = None
    return {
        "venue": venue,
        "entry_type": "inproceedings" if is_conf else "article",
        "year": best.get("year") or fields.get("year"),
        "doi": doi,
        "authors": best.get("authors") or [],
        "title": best.get("title") or raw_title,
        "arxiv_id": arxiv_id or best.get("arxiv_id"),
    }
