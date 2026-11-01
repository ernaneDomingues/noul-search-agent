"""Phase 6 (#22, #23, #24, #25, #26): Academic Authoring Copilot, Draft Auditor, Related Work Synthesizer,
BibTeX Sanitizer, and Peer-Review Rebuttal Copilot.

All functions are deterministic, stdlib-only, and strictly enforce workspace confinement and symlink rejection."""

import os
import re
from pathlib import Path

from noul import config as _config
from noul import errors as _errors
from noul import ids as _ids
from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.net import cache as _cache
from noul.providers import arxiv_client
from noul.schemas.authoring import (
    BibtexEntryUpdate,
    BibtexSanitizeReport,
    BlueprintSection,
    CitationInfillerReport,
    CitationRecommendationItem,
    DraftAuditFinding,
    DraftAuditReport,
    ManuscriptBlueprint,
    RebuttalPoint,
    RebuttalReport,
    RelatedWorkSynthesis,
    RelatedWorkThemeCluster,
)
from noul.schemas.evidence import AnalysisSubject, ChecklistItem, Signal


def _tools():
    """The tool layer (`noul.api`), imported lazily because `noul.api` imports this module."""
    from noul import api

    return api


_FILE_EXT_RE = re.compile(r"\.(?:md|markdown|tex|txt|bib|pdf)$", re.I)

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "over",
        "under",
        "between",
        "among",
        "have",
        "has",
        "had",
        "been",
        "were",
        "was",
        "are",
        "is",
        "be",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "will",
        "while",
        "whereas",
        "however",
        "therefore",
        "thus",
        "hence",
        "recent",
        "studies",
        "study",
        "show",
        "shown",
        "shows",
        "demonstrate",
        "demonstrates",
        "demonstrated",
        "prior",
        "work",
        "works",
        "existing",
        "previous",
        "literature",
        "paper",
        "papers",
        "approach",
        "approaches",
        "method",
        "methods",
        "results",
        "result",
        "indicate",
        "indicates",
        "known",
        "significantly",
        "achieves",
        "outperforms",
        "reduces",
        "improves",
        "state",
        "art",
        "citation",
        "needed",
        "cite",
        "todo",
        "our",
        "we",
        "their",
        "they",
        "these",
        "those",
        "such",
        "also",
        "more",
        "most",
        "other",
        "than",
        "very",
        "well",
    }
)

_EXPLICIT_CITE_MARKER_RE = re.compile(
    r"(\[\s*(?:CITATION\s+NEEDED|citation\s+needed|CITE|TODO:\s*cite|\?)\s*\]|"
    r"\\cite[pt]?\{(?:\s*\?\s*|\s*)\})"
)
_EXISTING_CITATION_RE = re.compile(
    r"(\\cite[a-zA-Z]*\{[^}?]+\}|\[@[a-zA-Z0-9_:-]+|\[\d+(?:\s*,\s*\d+)*\]|"
    r"\([A-Z][a-zA-Z-]+\s+et\s+al\.,?\s*\d{4}\))"
)
_DECLARATIVE_TRIGGER_RE = re.compile(
    r"\b(?:"
    r"studies\s+(?:have\s+)?show(?:n|s)?|"
    r"has\s+been\s+shown(?:\s+to)?|"
    r"have\s+been\s+shown(?:\s+to)?|"
    r"prior\s+work(?:s)?\s+(?:has|have|demonstrates?|shows?|suggests?)|"
    r"recent\s+advances\s+in|"
    r"outperforms?\s+(?:traditional|existing|prior|standard|conventional|baseline)|"
    r"significantly\s+(?:reduces?|improves?|increases?|lowers?|decreases?)|"
    r"achieves?\s+state[-\s]of[-\s]the[-\s]art|"
    r"demonstrates?\s+that|"
    r"is\s+(?:widely\s+)?known\s+to|"
    r"scaling\s+laws\s+(?:dictate|show|indicate|suggest)|"
    r"clinical\s+trials\s+(?:indicate|show|demonstrate)|"
    r"empirical\s+evidence\s+(?:shows?|indicates?|suggests?)|"
    r"reduces?\s+[a-z0-9_\s-]{2,25}\s+by\s+\d+(?:\.\d+)?\s*%"
    r")\b",
    re.I,
)


def _workspace_root() -> Path:
    return Path(_config.get_config()["workspace"]).expanduser().resolve()


def _validate_workspace_path(raw_path: str, must_exist: bool = True) -> Path:
    ws = _workspace_root()
    raw_str = str(raw_path).strip()
    p = Path(raw_str).expanduser()
    candidate_abs = Path(os.path.abspath(p if p.is_absolute() else ws / p))

    # Reject symlinks anywhere along the path inside or escaping workspace
    roots = {Path(os.path.abspath(ws)), ws}
    cur = candidate_abs
    while True:
        if cur.is_symlink():
            raise _errors.HTTPError(400, f"Refusing to access symbolic link '{cur}'.")
        if cur in roots or cur.parent == cur:
            break
        cur = cur.parent

    resolved = p.resolve() if p.is_absolute() else (ws / p).resolve()
    if os.environ.get("NOUL_ALLOW_ANY_PATH") != "1":
        try:
            resolved.relative_to(ws)
        except ValueError:
            raise _errors.HTTPError(
                400,
                f"Path '{resolved}' is outside the allowed workspace '{ws}'.",
            ) from None
    if resolved.is_symlink():
        raise _errors.HTTPError(400, f"Refusing to access symbolic link '{resolved}'.")
    if must_exist and not resolved.is_file():
        raise _errors.HTTPError(404, f"File not found in workspace: '{raw_str}'")
    return resolved


def _looks_like_filepath(raw_input: str) -> bool:
    s = (raw_input or "").strip()
    if not s or "\n" in s or "\r" in s or len(s) > 260:
        return False
    if s.startswith(("../", "./", "/", "~/", "..\\")):
        return True
    if _FILE_EXT_RE.search(s) and len(s.split()) <= 4:
        return True
    ws = _workspace_root()
    cand = ws / s
    try:
        if cand.exists() or cand.is_symlink():
            return True
    except OSError:
        pass
    return False


def _load_text_or_workspace_file(raw_input: str) -> tuple[str, str, Path | None]:
    """Return `(content_text, source_label, resolved_path_or_None)`.
    Validates strict workspace confinement and rejects path traversal / symlinks."""
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise _errors.HTTPError(400, "Input text or file path must be a non-empty string.")
    s = raw_input.strip()
    if _looks_like_filepath(s):
        path = _validate_workspace_path(s, must_exist=True)
        return path.read_text(encoding="utf-8", errors="replace"), path.name, path
    return raw_input, "inline_draft", None


def _make_bibtex_key(paper: dict, used_keys: set | None = None) -> str:
    authors = paper.get("authors") or ["anon"]
    first_author = str(authors[0] or "anon").strip()
    if "," in first_author:
        surname = first_author.split(",", 1)[0].strip().lower()
    else:
        surname = first_author.split()[-1].strip().lower() if first_author.split() else "anon"
    surname = re.sub(r"[^a-z0-9]", "", surname) or "anon"
    year = str(paper.get("year") or "nd")
    title_words = [
        w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", paper.get("title") or "") if w.lower() not in _STOPWORDS
    ]
    word = re.sub(r"[^a-z0-9]", "", title_words[0]) if title_words else "paper"
    base_key = f"{surname}{year}{word}"
    if used_keys is None:
        return base_key
    key = base_key
    suffix = 2
    while key in used_keys:
        key = f"{base_key}_{suffix}"
        suffix += 1
    used_keys.add(key)
    return key


def _format_bibtex_entry_from_paper(paper: dict, key: str) -> str:
    venue = str(paper.get("journal") or paper.get("venue") or "arXiv").strip()
    is_conf = arxiv_client.is_conference_venue(venue)
    etype = "inproceedings" if is_conf else "article"
    authors_bib = " and ".join(paper.get("authors") or ["Anonymous"])
    title = paper.get("title") or ""
    year = paper.get("year") or ""
    lines = [
        f"@{etype}{{{key},",
        f"  title = {{{title}}},",
        f"  author = {{{authors_bib}}},",
        f"  year = {{{year}}},",
    ]
    if is_conf:
        lines.append(f"  booktitle = {{{venue}}},")
    else:
        lines.append(f"  journal = {{{venue}}},")
    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")
    elif paper.get("arxiv_id"):
        lines.append(f"  eprint = {{{paper['arxiv_id']}}},")
    lines.append("}")
    return "\n".join(lines)


def _detect_input_format(text: str) -> str:
    if re.search(r"\\(?:cite[a-zA-Z]*|begin|section|subsection|documentclass|usepackage)\b", text or ""):
        return "latex"
    if re.search(r"(?:^#{1,6}\s+|\*\*[^*]+\*\*|\[\s*CITATION\s+NEEDED\s*\])", text or "", re.M | re.I):
        return "markdown"
    return "plain"


def _extract_search_query(sentence: str) -> str:
    cleaned = _EXPLICIT_CITE_MARKER_RE.sub(" ", sentence or "")
    cleaned = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r" \1 ", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+", " ", cleaned)
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", cleaned):
        low = w.lower().strip("-")
        if low not in _STOPWORDS and low not in words:
            words.append(low)
    return " ".join(words[:8]) or "scientific empirical study"


def _search_cached_papers_for_claim(query: str, limit: int = 2) -> list[dict]:
    q_terms = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    candidates = []
    try:
        with _cache._conn() as conn:
            rows = conn.execute("SELECT v FROM kv WHERE k LIKE 'paper:%'").fetchall()
        seen_ids = set()
        for (val_json,) in rows:
            try:
                p = __import__("json").loads(val_json)
            except Exception:
                continue
            if not isinstance(p, dict) or not p.get("title"):
                continue
            pid = p.get("id") or p.get("title")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            hay = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
            overlap = sum(2 if t in (p.get("title") or "").lower() else 1 for t in q_terms if t in hay)
            cites = int(p.get("citations") or 0)
            candidates.append((overlap, cites, p))
    except Exception:
        pass
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c[2] for c in candidates[:limit]]


def _retrieve_papers_for_claim(query: str, max_per_claim: int = 2) -> list[dict]:
    t = _tools()
    papers = []
    try:
        res = t.search_papers(query, limit=max_per_claim, sort="citations")
        papers = list((res or {}).get("papers") or [])
    except Exception:
        papers = []
    if len(papers) < max_per_claim:
        cached = _search_cached_papers_for_claim(query, limit=max_per_claim)
        seen = {p.get("id") for p in papers if p.get("id")}
        for cp in cached:
            if cp.get("id") not in seen:
                papers.append(cp)
                seen.add(cp.get("id"))
            if len(papers) >= max_per_claim:
                break
    return papers[:max_per_claim]


def _build_justification(paper: dict) -> str:
    authors = paper.get("authors") or ["Autor"]
    first_author = str(authors[0]).split(",")[0].split()[-1] if authors else "Autor"
    year = paper.get("year") or "n.d."
    venue = paper.get("journal") or paper.get("venue") or "literatura indexada"
    citations = int(paper.get("citations") or 0)
    abstract = (paper.get("abstract") or "").strip()
    if abstract:
        first_sent = re.split(r"(?<=[.!?])\s+", abstract)[0].strip()
        evidence = first_sent[:220]
    else:
        evidence = paper.get("title") or "Evidência empírica direta reportada."
    return (
        f"Este artigo de {first_author} et al. ({year}) comprova essa afirmação "
        f"({venue}, {citations} citações): {evidence}"
    )


def recommend_citations(
    draft_text: str,
    max_per_claim: int = 2,
    citation_style: str = "latex",
) -> dict:
    """Issue #22: Scan a draft paragraph/document for `[CITATION NEEDED]` / `\\cite{?}` markers and
    unsubstantiated empirical claims, recommend 2-3 real papers per claim with explicit justifications,
    annotate inline citations, and output a ready-to-use BibTeX block."""
    text, _, _ = _load_text_or_workspace_file(draft_text)
    style = (citation_style or "latex").strip().lower()
    if style not in ("latex", "markdown"):
        raise _errors.HTTPError(400, f"Invalid citation_style '{citation_style}'. Use 'latex' or 'markdown'.")
    max_k = max(1, min(int(max_per_claim or 2), 5))
    input_fmt = _detect_input_format(text)

    # Split into sentences while preserving exact spans for replacement
    sentence_Pattern = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.M)
    recommendations: list[CitationRecommendationItem] = []
    bibtex_entries: dict[str, str] = {}
    key_by_paper_id: dict[str, str] = {}
    used_keys: set = set()
    warnings: list[str] = []

    annotated = text
    replacements: list[tuple[str, str]] = []

    for m in sentence_Pattern.finditer(text):
        raw_sent = m.group(0)
        sent = raw_sent.strip()
        if len(sent) < 15 or sent.startswith(("#", "\\section", "\\subsection", "\\begin", "\\end", "%")):
            continue

        marker_m = _EXPLICIT_CITE_MARKER_RE.search(sent)
        trigger_m = None if marker_m else _DECLARATIVE_TRIGGER_RE.search(sent)
        has_existing = bool(_EXISTING_CITATION_RE.search(sent))

        if not marker_m and (not trigger_m or has_existing):
            continue

        marker_or_trigger = marker_m.group(1) if marker_m else (trigger_m.group(0) if trigger_m else "")
        query = _extract_search_query(sent)
        papers = _retrieve_papers_for_claim(query, max_per_claim=max_k)

        if not papers:
            warnings.append(f"No papers found in cache or APIs for claim: '{sent[:80]}...'")
            continue

        c_keys = []
        justifications = []
        slim_papers = []
        for p in papers:
            pid = p.get("id") or p.get("title") or ""
            if pid in key_by_paper_id:
                k = key_by_paper_id[pid]
            else:
                k = _make_bibtex_key(p, used_keys)
                if pid:
                    key_by_paper_id[pid] = k
                bibtex_entries[k] = _format_bibtex_entry_from_paper(p, k)
            c_keys.append(k)
            justifications.append(_build_justification(p))
            slim_papers.append(
                {
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "authors": p.get("authors") or [],
                    "year": p.get("year"),
                    "journal": p.get("journal"),
                    "doi": p.get("doi"),
                    "citations": int(p.get("citations") or 0),
                    "citation_key": k,
                }
            )

        cite_tag = f"\\cite{{{', '.join(c_keys)}}}" if style == "latex" else f"[@{'; @'.join(c_keys)}]"
        if marker_m:
            new_sent = _EXPLICIT_CITE_MARKER_RE.sub(cite_tag.replace("\\", "\\\\"), sent)
        else:
            if sent.endswith((".", "!", "?")):
                new_sent = f"{sent[:-1]} {cite_tag}{sent[-1]}"
            else:
                new_sent = f"{sent} {cite_tag}"

        replacements.append((sent, new_sent))
        recommendations.append(
            CitationRecommendationItem(
                claim_sentence=sent,
                marker_or_trigger=marker_or_trigger,
                search_query=query,
                recommended_papers=slim_papers,
                justifications=justifications,
                citation_keys=c_keys,
                inline_replacement=new_sent,
            )
        )

    for old_s, new_s in replacements:
        annotated = annotated.replace(old_s, new_s, 1)

    report = CitationInfillerReport(
        input_format=input_fmt,
        annotated_text=annotated,
        claims_detected=len(recommendations),
        recommendations=recommendations,
        bibtex_block="\n\n".join(bibtex_entries.values()),
        warnings=warnings,
    )
    return report.model_dump()


# ---------------------------------------------------------------------------
# Issue #24: Related Work Synthesizer (`synthesize_related_work`)
# ---------------------------------------------------------------------------

_ARCH_FAMILY_RE = re.compile(
    r"\b(?:architectur\w+|transformer\w*|attention|backbone|representation|graph\s+neural|encoder|decoder|"
    r"token\s+routing|sparse|mixture[-\s]of[-\s]experts|moe|state[-\s]space|mamba|layer|embedding|mechanism)\b",
    re.I,
)
_TRAIN_FAMILY_RE = re.compile(
    r"\b(?:train\w+|optimiz\w+|fine[-\s]?tun\w+|rlhf|alignment|preference|distill\w+|causal|identification|"
    r"difference[-\s]in[-\s]differences|instrumental\s+variable|panel|randomized|rct|clinical\s+trial|"
    r"cohort|intervention|dosage|denoising|regulariz\w+)\b",
    re.I,
)
_EFF_FAMILY_RE = re.compile(
    r"\b(?:quantiz\w+|prun\w+|compress\w+|kv\s+cache|inference|latency|throughput|memory|vram|hardware|"
    r"scaling|benchmark\w*|evaluation|empirical|meta[-\s]analysis|systematic\s+review|survey)\b",
    re.I,
)


def _classify_approach_family(paper: dict) -> tuple[str, str]:
    title = paper.get("title") or ""
    body = f"{paper.get('abstract', '')} {paper.get('evidence', '')}"
    s_arch = len(_ARCH_FAMILY_RE.findall(title)) * 2 + len(_ARCH_FAMILY_RE.findall(body))
    s_train = len(_TRAIN_FAMILY_RE.findall(title)) * 2 + len(_TRAIN_FAMILY_RE.findall(body))
    s_eff = len(_EFF_FAMILY_RE.findall(title)) * 2 + len(_EFF_FAMILY_RE.findall(body))
    if s_arch >= s_train and s_arch >= s_eff and s_arch > 0:
        return ("Architectural & Representation Foundations", "architecture_and_representation")
    if s_train >= s_eff and s_train > 0:
        return ("Training, Optimization & Causal Identification", "training_and_identification")
    return ("Efficiency, Scaling & Empirical Evaluation", "efficiency_and_evaluation")


def _extract_core_contribution(paper: dict) -> str:
    abs_txt = (paper.get("abstract") or "").strip()
    if abs_txt:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abs_txt) if s.strip()]
        if sents:
            first = re.sub(
                r"^(?:We|In\s+this\s+(?:paper|work|study),?\s+we|This\s+(?:paper|study))\s+\w+\s+",
                "",
                sents[0],
                flags=re.I,
            )
            return first.rstrip(".")
    return (paper.get("title") or "a domain-specific methodology").rstrip(".")


def _extract_limitation_clause(paper: dict) -> str:
    abs_txt = (paper.get("abstract") or "").strip()
    for s in re.split(r"(?<=[.!?])\s+", abs_txt):
        if re.search(
            r"\b(?:however|limitation|struggles|degrades|overhead|constrained|trade[-\s]?off|cost|latency)\b", s, re.I
        ):
            cleaned = re.sub(
                r"^(?:However|Nevertheless|Nonetheless|A\s+limitation\s+is\s+that),?\s*", "", s.strip(), flags=re.I
            )
            return cleaned.rstrip(".")
    return "operates under specific structural or computational assumptions"


def synthesize_related_work(
    paper_ids,
    proposal: str,
    citation_style: str = "latex",
    output_format: str = "markdown",
) -> dict:
    """Issue #24: Group 1-20 papers into thematic approach families, write comparative academic prose
    (never isolated per-author lists), position the user's `proposal` against the collective gap,
    and output ready-to-paste Markdown or LaTeX (`\\section{Related Work}`) with BibTeX."""
    t = _tools()
    if isinstance(paper_ids, str):
        ids = [x.strip() for x in paper_ids.split(",") if x.strip()]
    elif isinstance(paper_ids, (list, tuple)):
        ids = [str(x).strip() for x in paper_ids if str(x).strip()]
    else:
        ids = []
    if not ids:
        raise _errors.HTTPError(400, "Provide at least one paper ID in `paper_ids`.")
    if not isinstance(proposal, str) or not proposal.strip():
        raise _errors.HTTPError(400, "Provide a non-empty `proposal` describing your work's contribution.")

    style = (citation_style or "latex").strip().lower()
    if style not in ("latex", "numeric"):
        raise _errors.HTTPError(400, f"Invalid citation_style '{citation_style}'. Use 'latex' or 'numeric'.")
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    papers = []
    seen = set()
    for pid in ids[:20]:
        p = t._resolve(pid)
        real_id = p.get("id") or pid
        if real_id not in seen:
            seen.add(real_id)
            papers.append(p)

    used_keys: set = set()
    key_map: dict[str, str] = {}
    num_map: dict[str, int] = {}
    bibtex_entries: list[str] = []

    for idx, p in enumerate(papers, 1):
        pid = p.get("id") or str(idx)
        k = _make_bibtex_key(p, used_keys)
        key_map[pid] = k
        num_map[pid] = idx
        bibtex_entries.append(_format_bibtex_entry_from_paper(p, k))

    def _cite_ref(p: dict) -> str:
        pid = p.get("id") or ""
        authors = p.get("authors") or ["Author"]
        surname = str(authors[0]).split(",")[0].split()[-1] if authors else "Author"
        if style == "latex":
            return f"{surname} et al. \\cite{{{key_map[pid]}}}"
        return f"{surname} et al. [{num_map[pid]}]"

    family_order = [
        ("Architectural & Representation Foundations", "architecture_and_representation"),
        ("Training, Optimization & Causal Identification", "training_and_identification"),
        ("Efficiency, Scaling & Empirical Evaluation", "efficiency_and_evaluation"),
    ]
    grouped: dict[str, list[dict]] = {name: [] for name, _ in family_order}
    for p in papers:
        t_name, _ = _classify_approach_family(p)
        grouped[t_name].append(p)

    clusters: list[RelatedWorkThemeCluster] = []
    for theme_name, family_slug in family_order:
        group = grouped[theme_name]
        if not group:
            continue
        p_ids = [p.get("id") or "" for p in group]
        c_keys = [key_map[pid] for pid in p_ids]

        if len(group) == 1:
            p0 = group[0]
            narrative = (
                f"Within the scope of {theme_name.lower()}, {_cite_ref(p0)} investigate "
                f"{_extract_core_contribution(p0)}, establishing an important reference point while noting that the "
                f"approach {_extract_limitation_clause(p0)}."
            )
        elif len(group) == 2:
            p0, p1 = group[0], group[1]
            narrative = (
                f"Research on {theme_name.lower()} has evolved along complementary trajectories: while "
                f"{_cite_ref(p0)} focus on {_extract_core_contribution(p0)}, {_cite_ref(p1)} extend this line of "
                f"inquiry by addressing {_extract_core_contribution(p1)}; however, whereas {_cite_ref(p0)} "
                f"{_extract_limitation_clause(p0)}, {_cite_ref(p1)} highlight that their formulation "
                f"{_extract_limitation_clause(p1)}."
            )
        else:
            p0, p1 = group[0], group[1]
            rest = group[2:]
            rest_refs = ", ".join(_cite_ref(r) for r in rest)
            rest_contribs = "; ".join(_extract_core_contribution(r) for r in rest)
            narrative = (
                f"Prior work in {theme_name.lower()} reveals a clear methodological tension: while "
                f"{_cite_ref(p0)} pioneer {_extract_core_contribution(p0)}, {_cite_ref(p1)} contrast this paradigm "
                f"by introducing {_extract_core_contribution(p1)}. Building on these foundations, {rest_refs} "
                f"further explore {rest_contribs}, yet comparative analysis shows that {_cite_ref(p0)} "
                f"{_extract_limitation_clause(p0)} whereas subsequent variants remain sensitive to "
                f"{_extract_limitation_clause(rest[-1])}."
            )

        clusters.append(
            RelatedWorkThemeCluster(
                theme_name=theme_name,
                approach_family=family_slug,
                paper_ids=p_ids,
                citation_keys=c_keys,
                comparative_narrative=narrative,
            )
        )

    all_cites = (
        f"\\cite{{{', '.join(key_map[p.get('id') or ''] for p in papers)}}}"
        if style == "latex"
        else "[" + ", ".join(str(num_map[p.get("id") or ""]) for p in papers) + "]"
    )
    lims_summary = "; ".join(_extract_limitation_clause(p) for p in papers[:2])
    positioning = (
        f"Despite these substantial advances across {', '.join(c.theme_name.lower() for c in clusters)} {all_cites}, "
        f"existing methods remain constrained by key trade-offs ({lims_summary}). "
        f"In contrast, our work ({proposal.strip().rstrip('.')}) directly bridges this gap by unifying "
        f"the strengths of prior formulations while eliminating their primary operational bottlenecks."
    )

    if fmt == "latex":
        lines = ["\\section{Related Work}", ""]
        for cl in clusters:
            lines.append(f"\\paragraph{{{cl.theme_name}.}} {cl.comparative_narrative}")
            lines.append("")
        lines.append(f"\\paragraph{{Positioning of Our Contribution.}} {positioning}")
        rendered = "\n".join(lines)
    else:
        lines = ["## Related Work", ""]
        for cl in clusters:
            lines.append(f"### {cl.theme_name}")
            lines.append(cl.comparative_narrative)
            lines.append("")
        lines.append("### Positioning of Our Contribution")
        lines.append(positioning)
        rendered = "\n".join(lines)

    res = RelatedWorkSynthesis(
        proposal_summary=proposal.strip(),
        citation_style=style,
        output_format=fmt,
        thematic_clusters=clusters,
        positioning_paragraph=positioning,
        rendered_related_work=rendered,
        bibtex_block="\n\n".join(bibtex_entries),
    )
    return res.model_dump()


# ---------------------------------------------------------------------------
# Issue #23: Draft Manuscript Structuring & Auditor (`generate_manuscript_blueprint`, `audit_manuscript_draft`)
# ---------------------------------------------------------------------------

_SUPPORTED_VENUES = ("neurips", "icml", "iclr", "acl", "ieee", "nature", "lancet", "aer", "elsevier", "general")


def generate_manuscript_blueprint(
    topic: str,
    venue: str = "neurips",
    hypothesis: str = "",
    key_findings: list[str] | None = None,
    output_format: str = "markdown",
) -> dict:
    """Issue #23 (Mode 1): Generate a complete IMRaD structural blueprint tailored to `venue`
    (NeurIPS, ICML, ICLR, ACL, IEEE, Nature, Lancet, AER, general) in Markdown or LaTeX."""
    if not isinstance(topic, str) or not topic.strip():
        raise _errors.HTTPError(400, "Topic must be a non-empty string.")
    v = (venue or "neurips").strip().lower()
    if v not in _SUPPORTED_VENUES:
        raise _errors.HTTPError(400, f"Unsupported venue '{venue}'. Choose from: {', '.join(_SUPPORTED_VENUES)}")
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    if isinstance(key_findings, str):
        findings = [x.strip() for x in re.split(r"[;\n]", key_findings) if x.strip()]
    elif isinstance(key_findings, (list, tuple)):
        findings = [str(x).strip() for x in key_findings if str(x).strip()]
    else:
        findings = []

    hyp_str = (
        hypothesis or ""
    ).strip() or f"Our proposed method for {topic.strip()} improves efficiency and accuracy over SOTA baselines."
    findings_preview = (
        "; ".join(findings) if findings else "Quantified improvements (e.g., +X.X% over baseline with p < 0.01)"
    )

    venue_mandatory_title = {
        "neurips": "7. Broader Impact & NeurIPS Paper Checklist",
        "icml": "7. Impact Statement & Reproducibility Checklist",
        "iclr": "7. Reproducibility & Ethics Statement",
        "acl": "7. Limitations & Ethics Statement (ACL Mandatory)",
        "ieee": "7. Conclusion & Acknowledgment",
        "nature": "7. Data Availability, Code Availability & Author Contributions",
        "lancet": "7. Research in Context, CONSORT/STROBE Declarations & Role of Funding Source",
        "aer": "7. Data & Code Availability Statement (AEA Mandatory)",
        "elsevier": "7. Declaration of Competing Interest & Data Availability",
        "general": "7. Reproducibility & Broader Impact",
    }[v]

    sections = [
        BlueprintSection(
            section_title="Abstract",
            imrad_role="abstract",
            paragraph_prompts=[
                f"Sentences 1-2 (Context & Bottleneck): Define the high-stakes problem in {topic.strip()} and why current methods fail.",
                f"Sentence 3 (Core Hypothesis & Proposal): State the central hypothesis ({hyp_str}).",
                f"Sentences 4-5 (Quantitative Findings): Summarize empirical gains ({findings_preview}) and release of open artifacts.",
            ],
            recommended_elements=[
                "150-250 words",
                "Zero undefined acronyms",
                "Headline metric delta with confidence interval",
            ],
        ),
        BlueprintSection(
            section_title="1. Introduction",
            imrad_role="introduction",
            paragraph_prompts=[
                f"Paragraph 1 (Macro Motivation): Establish the scientific and practical importance of {topic.strip()}.",
                "Paragraph 2 (Limitations of Prior Work): Explain the structural trade-offs in existing literature.",
                f"Paragraph 3 (Central Hypothesis & Approach): Formulate the testable hypothesis: '{hyp_str}'.",
                "Paragraph 4 (Bulleted Contributions): List 3-4 concrete, falsifiable contributions anchored to sections and tables.",
            ],
            recommended_elements=[
                "Figure 1 (Overview / Teaser diagram)",
                "Bulleted list of 3-4 contributions",
                "Explicit forward references to Sections 3-5",
            ],
        ),
        BlueprintSection(
            section_title="2. Related Work",
            imrad_role="related_work",
            paragraph_prompts=[
                "Paragraph 1 (Foundational Architectures / Theory): Contrast foundational paradigms rather than listing papers in isolation.",
                "Paragraph 2 (Optimization, Identification & Empirical Benchmarks): Compare methodological assumptions and evaluation regimes.",
                "Paragraph 3 (Positioning & Gap): Explicitly state how your method resolves the collective limitation of prior work.",
            ],
            recommended_elements=[
                "3 thematic clusters",
                "Comparative connectors (While X..., Y..., whereas Z...)",
                "Explicit differentiation paragraph",
            ],
        ),
        BlueprintSection(
            section_title="3. Methodology / Problem Formulation",
            imrad_role="methodology",
            paragraph_prompts=[
                "Paragraph 1 (Formal Problem Setup & Notation): Define input/output spaces, random variables, and notation table.",
                "Paragraph 2 (Core Mechanism / Identification Strategy): Present numbered equations and algorithmic steps.",
                "Paragraph 3 (Complexity / Theoretical Guarantees): Analyze computational complexity, memory footprint, or identification assumptions.",
            ],
            recommended_elements=[
                "Numbered Equations (Eq. 1-3)",
                "Algorithm 1 pseudocode box",
                "Explicit assumptions and boundary conditions",
            ],
        ),
        BlueprintSection(
            section_title="4. Experimental Setup & Empirical Results",
            imrad_role="experiments",
            paragraph_prompts=[
                "Paragraph 1 (Datasets, Baselines & Metrics): Justify benchmark selection, sample sizes (N), splits, and strong SOTA baselines.",
                "Paragraph 2 (Implementation & Reproducibility Details): Report exact hyperparameters, random seeds, optimizer, and compute hardware.",
                f"Paragraph 3 (Main Results): Present primary quantitative evidence supporting: {findings_preview}.",
                "Paragraph 4 (Ablation & Robustness Studies): Isolate the contribution of each component (w/o X) with error bars.",
            ],
            recommended_elements=[
                "Table 1 (Main benchmark comparison with ± std)",
                "Table 2 (Ablation study)",
                "Hyperparameter & hardware specification",
            ],
        ),
        BlueprintSection(
            section_title="5. Discussion & Limitations",
            imrad_role="discussion",
            paragraph_prompts=[
                "Paragraph 1 (Interpretation of Findings): Explain why the proposed mechanism succeeds and where marginal gains saturate.",
                "Paragraph 2 (Failure Modes & Threats to Validity): Transparently analyze boundary conditions, OOD degradation, and compute/clinical constraints.",
            ],
            recommended_elements=[
                "Dedicated Limitations subsection",
                "Failure case analysis",
                "Calibration of claims to empirical evidence",
            ],
        ),
        BlueprintSection(
            section_title="6. Conclusion",
            imrad_role="conclusion",
            paragraph_prompts=[
                f"Paragraph 1 (Synthesis & Future Directions): Summarize how the study advances {topic.strip()} and outline concrete open problems.",
            ],
            recommended_elements=["Concise summary without introducing unverified claims"],
        ),
        BlueprintSection(
            section_title=venue_mandatory_title,
            imrad_role="venue_mandatory",
            paragraph_prompts=[
                f"Mandatory {v.upper()} declarations: address ethical implications, societal impact, data/code licensing, and reproducibility checklist items.",
            ],
            recommended_elements=[
                f"Venue-specific ({v.upper()}) compliance block",
                "Anonymized artifact URLs for double-blind review",
            ],
        ),
    ]

    if fmt == "latex":
        lines = [
            f"% Manuscript Blueprint ({v.upper()}) — Topic: {topic.strip()}",
            f"\\title{{{topic.strip()}}}",
            "\\author{Anonymous Author(s)}",
            "\\maketitle",
            "",
        ]
        for sec in sections:
            if sec.imrad_role == "abstract":
                lines.append("\\begin{abstract}")
                for p in sec.paragraph_prompts:
                    lines.append(f"% {p}")
                lines.append("\\end{abstract}\n")
            else:
                clean_title = re.sub(r"^\d+\.\s*", "", sec.section_title)
                lines.append(f"\\section{{{clean_title}}}")
                for p in sec.paragraph_prompts:
                    lines.append(f"% Paragraph guidance: {p}")
                for el in sec.recommended_elements:
                    lines.append(f"% Required element: {el}")
                lines.append("")
        rendered = "\n".join(lines)
    else:
        lines = [
            f"# {topic.strip()}",
            f"> **Target Venue:** `{v.upper()}` | **Central Hypothesis:** {hyp_str}",
            "",
        ]
        for sec in sections:
            lines.append(f"## {sec.section_title}")
            lines.append("**Paragraph-by-Paragraph Writing Plan:**")
            for idx, p in enumerate(sec.paragraph_prompts, 1):
                lines.append(f"{idx}. {p}")
            lines.append("\n**Recommended Elements:**")
            for el in sec.recommended_elements:
                lines.append(f"- [ ] {el}")
            lines.append("")
        rendered = "\n".join(lines)

    blueprint = ManuscriptBlueprint(
        topic=topic.strip(),
        venue=v,
        hypothesis=hyp_str,
        key_findings=findings,
        output_format=fmt,
        sections=sections,
        rendered_template=rendered,
    )
    return blueprint.model_dump()


_WEAK_CLAIM_PATTERNS = (
    (
        re.compile(r"\b(?:clearly\s+proves?|proves?\s+beyond\s+doubt|undoubtedly|obviously|trivially)\b", re.I),
        "high",
        "Overstated or dogmatic phrasing ('clearly proves' / 'obviously' / 'undoubtedly') without formal proof or citation.",
        "Replace absolute language with calibrated empirical hedging (e.g., 'Our experiments on Table 1 indicate that... \\cite{...}').",
    ),
    (
        re.compile(
            r"\b(?:massive|huge|dramatic|unprecedented|vastly\s+superior)\s+(?:improvement|gain|speedup|performance)s?\b",
            re.I,
        ),
        "high",
        "Vague qualitative superlative ('massive/dramatic improvement') lacking exact metric delta and baseline anchor.",
        "Specify the exact quantitative delta, metric, and baseline (e.g., 'improves accuracy by +4.2% (±0.3%) over Baseline X on Dataset Y').",
    ),
    (
        re.compile(r"\b(?:state[-\s]of[-\s]the[-\s]art|\bsota\b|outperforms?\s+all)\b", re.I),
        "medium",
        "State-of-the-art / universal superiority claim without adjacent Table/Figure reference or citation.",
        "Anchor the claim to specific evaluated benchmarks and citations (e.g., 'achieves competitive performance among evaluated baselines on Benchmark Z (Table 1)').",
    ),
)


def _detect_weak_claims(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text or ""):
        sent = m.group(0).strip()
        if len(sent) < 12 or sent.startswith(("%", "#")):
            continue
        has_anchor = bool(
            _EXISTING_CITATION_RE.search(sent)
            or re.search(r"\b(?:Table|Figure|Fig\.|Tab\.|Eq\.|Section)\s*\d+|\b\\ref\{", sent, re.I)
        )
        for rx, sev, desc, rewrite in _WEAK_CLAIM_PATTERNS:
            if rx.search(sent):
                if sev == "medium" and has_anchor:
                    continue
                findings.append(
                    DraftAuditFinding(
                        category="weak_claim",
                        severity=sev,
                        excerpt=sent[:240],
                        issue_description=desc,
                        suggested_rewrite=rewrite,
                    )
                )
                break
    return findings


def _detect_notation_inconsistencies(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    # 1. Mixed math symbol styling: e.g. L_total vs \mathcal{L}_{total} or L vs \mathcal{L}
    has_plain_loss = bool(re.search(r"\$L(?:_[a-zA-Z0-9{}]+)?\$|\bL_(?:total|loss|train)\b", text or ""))
    has_cal_loss = bool(re.search(r"\\mathcal\{L\}", text or ""))
    if has_plain_loss and has_cal_loss:
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="high",
                excerpt="Mixed usage of $L$ / $L_{total}$ and $\\mathcal{L}$ across equations and prose.",
                issue_description="Inconsistent mathematical notation for the loss/objective function ($L$ vs. $\\mathcal{L}$).",
                suggested_rewrite="Standardize on $\\mathcal{L}_{\\text{total}}$ throughout all equations and inline prose.",
            )
        )

    # 2. Mixed indexing subscript vs superscript: x_i and x^{(i)}
    if re.search(r"\bx_\{?i\}?\b", text or "") and re.search(r"\bx\^\{\(i\)\}", text or ""):
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="medium",
                excerpt="Mixed sample indexing $x_i$ and $x^{(i)}$.",
                issue_description="Sample index notation alternates between subscript $x_i$ and parenthesized superscript $x^{(i)}$.",
                suggested_rewrite="Use $x_i$ consistently for token/feature indices and $x^{(n)}$ exclusively for dataset sample indices.",
            )
        )

    # 3. Terminology inconsistency (e.g., fine-tuning vs finetuning vs Fine Tuning)
    ft_variants = set(
        re.findall(r"\b(?:fine-tun(?:ing|ed)|finetun(?:ing|ed)|fine\s+tun(?:ing|ed))\b", (text or "").lower())
    )
    normalized_ft = {re.sub(r"\s+", " ", v) for v in ft_variants}
    if len(normalized_ft) > 1:
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="low",
                excerpt=", ".join(sorted(normalized_ft)),
                issue_description="Inconsistent hyphenation of 'fine-tuning' vs 'finetuning' / 'fine tuning'.",
                suggested_rewrite="Standardize on hyphenated 'fine-tuning' / 'fine-tuned' across the entire manuscript.",
            )
        )

    # 4. Equation with unintroduced variables
    eq_matches = list(re.finditer(r"(?:\\begin\{equation\}|\$\$)(.*?)(?:\\end\{equation\}|\$\$)", text or "", re.S))
    for eq_m in eq_matches:
        eq_body = eq_m.group(1).strip()
        after_window = (text or "")[eq_m.end() : eq_m.end() + 260]
        if not re.search(r"\b(?:where|denotes|represents|is\s+the|with)\b", after_window, re.I):
            findings.append(
                DraftAuditFinding(
                    category="notation_inconsistency",
                    severity="medium",
                    excerpt=re.sub(r"\s+", " ", eq_body)[:160],
                    issue_description="Displayed equation is not followed by a 'where ... denotes ...' symbol definition clause.",
                    suggested_rewrite="Immediately follow the equation with: 'where $\\theta \\in \\mathbb{R}^d$ denotes the model parameters and ...'",
                )
            )
            break

    return findings


def _detect_reproducibility_omissions(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    checks = [
        (
            re.compile(r"\b(?:random\s+seeds?|seeds?\s*=|\bseed\b|multi[-\s]seed)\b", re.I),
            "high",
            "Missing Random Seeds specification",
            "No mention of random seeds or multi-seed evaluation runs was found in the draft.",
            "Add to Experimental Setup: 'All experiments are repeated across 5 random seeds (seeds {42, 100, 2024, 7, 99}) and we report mean ± standard deviation.'",
        ),
        (
            re.compile(
                r"\b(?:learning\s+rate|optimizer|adamw?|sgd|batch\s+size|hyperparameter|dosage|mg/day|protocol)\b", re.I
            ),
            "high",
            "Missing Hyperparameters / Protocol specification",
            "Draft omits optimizer, learning rate, batch size, or intervention protocol parameters.",
            "Add to Experimental Setup: 'We optimize using AdamW (lr = 2e-4, weight decay = 0.01, batch size = 64, cosine warmup over 500 steps).'",
        ),
        (
            re.compile(
                r"\b(?:train(?:ing)?\s*/\s*(?:val|dev|test)|sample\s+size|\b[nN]\s*=\s*\d+|split|cohort\s+of\s+\d+)\b",
                re.I,
            ),
            "high",
            "Missing Sample Size (N) or Train/Val/Test splits",
            "Draft does not specify dataset split sizes or cohort sample size (N).",
            "Add to Experimental Setup: 'The dataset comprises N = 10,000 examples partitioned into 80%/10%/10% train/validation/test splits without data overlap.'",
        ),
        (
            re.compile(
                r"(?:\\pm|±|\b95%\s*CI\b|\bconfidence\s+interval|\bstd\b|\bstandard\s+deviation|\bp\s*[<=>]\s*0\.\d+|\berror\s+bars?\b)",
                re.I,
            ),
            "medium",
            "Missing Error Bars / Confidence Intervals / Statistical Significance",
            "Quantitative claims lack standard deviations (±), 95% confidence intervals, or p-values.",
            "Report dispersion alongside point estimates (e.g., '88.4% ± 0.4% (95% CI: [87.6, 89.2], paired t-test p < 0.01)').",
        ),
        (
            re.compile(r"\b(?:NVIDIA|A100|H100|V100|RTX|TPU|GPU|VRAM|compute\s+hours|wall[-\s]clock)\b", re.I),
            "medium",
            "Missing Compute Infrastructure & Runtime specification",
            "Draft does not report GPU/TPU hardware, VRAM footprint, or training wall-clock time.",
            "Add to Experimental Setup: 'Experiments were executed on 4x NVIDIA A100 (80GB VRAM) GPUs requiring 6.5 GPU-hours per training run.'",
        ),
    ]
    for rx, sev, excerpt_lbl, desc, rewrite in checks:
        if not rx.search(text or ""):
            findings.append(
                DraftAuditFinding(
                    category="reproducibility_omission",
                    severity=sev,
                    excerpt=excerpt_lbl,
                    issue_description=desc,
                    suggested_rewrite=rewrite,
                )
            )
    return findings


def audit_manuscript_draft(
    draft_input: str,
    venue: str = "neurips",
    output_format: str = "markdown",
) -> dict:
    """Issue #23 (Mode 2): Audit a manuscript draft (.md / .tex file in workspace or inline text) for
    weak/unsubstantiated claims, mathematical/terminological inconsistencies, and reproducibility omissions."""
    text, source_label, _ = _load_text_or_workspace_file(draft_input)
    v = (venue or "neurips").strip().lower()
    if v not in _SUPPORTED_VENUES:
        raise _errors.HTTPError(400, f"Unsupported venue '{venue}'. Choose from: {', '.join(_SUPPORTED_VENUES)}")
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    weak = _detect_weak_claims(text)
    notation = _detect_notation_inconsistencies(text)
    repro = _detect_reproducibility_omissions(text)

    signals: list[Signal] = []
    for f in [*weak, *notation, *repro]:
        signals.append(
            Signal(
                id=f"d{len(signals) + 1}",
                category=f.category,
                kind="gap" if f.category == "reproducibility_omission" else "concern",
                description=f.issue_description,
                priority_hint=_contract.priority(f.severity),
                detail=f"Suggested rewrite: {f.suggested_rewrite}" if f.suggested_rewrite else "",
                # Omissions carry a label, not a passage of the draft: no excerpt for them.
                excerpts=[_untrusted.excerpt(f.excerpt, origin="draft")]
                if f.excerpt and f.category != "reproducibility_omission"
                else [],
            )
        )

    def state(findings):
        return "no" if findings else "unclear"

    checklist = [
        ChecklistItem(
            id="claims_anchored",
            question="Are all empirical and SOTA claims anchored to specific tables, figures or citations?",
            detected=state(weak),
        ),
        ChecklistItem(
            id="notation_consistent",
            question="Are notation and terminology consistent across prose and equations?",
            detected=state(notation),
        ),
        ChecklistItem(
            id="reproducibility_complete",
            question="Are reproducibility details (seeds, hyperparameters, sample sizes, error bars, hardware) complete?",
            detected=state(repro),
        ),
        ChecklistItem(
            id="venue_sections",
            question=f"Are the {v.upper()} mandatory sections (Limitations, Ethics/Broader Impact, checklist) included?",
            detected="unclear",
            remediation="Run audit_submission_readiness for the venue-specific checks.",
        ),
    ]
    report = DraftAuditReport(
        tool="audit_manuscript_draft",
        subject=AnalysisSubject(title=source_label, source="draft"),
        signals=signals,
        checklist=checklist,
        rubric=_contract.rubric(
            f"Manuscript draft audit ({v.upper()})",
            (
                ("claims", "Which claims are over-stated or lack a table, figure or citation?"),
                ("notation", "Which notation or terminology changes would confuse a reviewer?"),
                ("reproducibility", "What would a reader need to reproduce the main result that is missing?"),
                ("priorities", "Which three fixes matter most before submission?"),
            ),
            "These are pattern-based findings on the user's draft, not an assessment. Confirm each against the "
            "excerpt; 'unclear' means the heuristics found no problem, not that the item is satisfied.",
        ),
        extraction_confidence=_contract.extraction_confidence("draft", length=len(text)),
        limitations=["Pattern-based detection: paraphrased problems are missed and some flags are false positives."],
        venue=v,
        output_format=fmt,
    )
    report.rendered_markdown = _contract.render_markdown(report, f"Manuscript draft audit ({source_label})")
    if fmt == "latex":
        lines = [f"\\section*{{Manuscript Audit ({v.upper()}) --- {source_label}}}", "\\begin{itemize}"]
        for sig in signals:
            quote = sig.excerpts[0].text if sig.excerpts else ""
            lines.append(
                f"\\item \\textbf{{[{sig.category}, {sig.priority_hint}]}} \\texttt{{{quote}}} --- "
                f"{sig.description} \\emph{{{sig.detail}}}"
            )
        if not signals:
            lines.append("\\item No pattern-based findings.")
        lines.append("\\end{itemize}")
        report.rendered_report = "\n".join(lines)
    else:
        report.rendered_report = report.rendered_markdown
    return report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Issue #25: BibTeX Sanitizer & Preprint-to-Published Resolver (`sanitize_bibtex`)
# ---------------------------------------------------------------------------

_BIB_ENTRY_START_RE = re.compile(r"@([a-zA-Z]+)\s*\{\s*([^,\s]+)\s*,", re.M)
_PROTECTED_ACRONYMS = (
    "LLMs",
    "LLM",
    "GPUs",
    "GPU",
    "RLHF",
    "Transformer",
    "BERT",
    "GPT-4",
    "GPT",
    "CONSORT",
    "STROBE",
    "PRISMA",
    "DNA",
    "RNA",
    "COVID-19",
    "MMLU",
    "GSM8K",
)


def parse_bibtex_entries(raw_bib: str) -> list[dict]:
    """Parse `.bib` text into a list of dicts: `{'entry_type': str, 'key': str, 'fields': OrderedDict}`."""
    entries: list[dict] = []
    text = raw_bib or ""
    pos = 0
    while pos < len(text):
        m = _BIB_ENTRY_START_RE.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower().strip()
        key = m.group(2).strip()
        brace_open = text.find("{", m.start())
        depth = 0
        end_idx = len(text)
        for i in range(brace_open, len(text)):
            if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
                depth += 1
            elif text[i] == "}" and (i == 0 or text[i - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        body = text[m.end() : end_idx]
        pos = end_idx + 1
        if etype in ("comment", "preamble", "string"):
            continue

        fields: dict[str, str] = {}
        f_pos = 0
        while f_pos < len(body):
            fm = re.search(r"([a-zA-Z0-9_:-]+)\s*=\s*", body[f_pos:])
            if not fm:
                break
            fname = fm.group(1).lower().strip()
            val_start = f_pos + fm.end()
            while val_start < len(body) and body[val_start].isspace():
                val_start += 1
            if val_start >= len(body):
                break
            ch = body[val_start]
            if ch == "{":
                d = 0
                v_end = val_start
                for j in range(val_start, len(body)):
                    if body[j] == "{" and (j == 0 or body[j - 1] != "\\"):
                        d += 1
                    elif body[j] == "}" and (j == 0 or body[j - 1] != "\\"):
                        d -= 1
                        if d == 0:
                            v_end = j
                            break
                fval = body[val_start + 1 : v_end].strip()
                f_pos = v_end + 1
            elif ch == '"':
                v_end = val_start + 1
                while v_end < len(body):
                    if body[v_end] == '"' and body[v_end - 1] != "\\":
                        break
                    v_end += 1
                fval = body[val_start + 1 : v_end].strip()
                f_pos = v_end + 1
            else:
                comma = body.find(",", val_start)
                v_end = comma if comma != -1 else len(body)
                fval = body[val_start:v_end].strip().rstrip(",").strip()
                f_pos = v_end + 1
            fields[fname] = re.sub(r"\s+", " ", fval).strip()

        entries.append({"entry_type": etype, "key": key, "fields": fields})
    return entries


def _normalize_authors_field(raw_author: str) -> str:
    if not raw_author:
        return ""
    s = re.sub(r"\s*;\s*", " and ", raw_author.strip())
    s = re.sub(r"\s+&\s+", " and ", s)
    parts = [p.strip() for p in re.split(r"\band\b", s) if p.strip()]
    return " and ".join(parts)


def _escape_latex_outside_math(value: str, protect_acronyms: bool = False) -> tuple[str, int]:
    if not value:
        return "", 0
    fixes = 0
    segments = re.split(r"(\$[^$]*\$)", value)
    out_segs = []
    for seg in segments:
        if seg.startswith("$") and seg.endswith("$"):
            out_segs.append(seg)
            continue
        for ch in ("&", "%", "_"):
            pattern = re.compile(rf"(?<!\\){re.escape(ch)}")
            matches = len(pattern.findall(seg))
            if matches:
                fixes += matches
                seg = pattern.sub(lambda _, c=ch: f"\\{c}", seg)
        if protect_acronyms:
            for acr in _PROTECTED_ACRONYMS:
                seg = re.sub(rf"(?<!\{{)\b({re.escape(acr)})\b(?!\}})", lambda m: f"{{{m.group(1)}}}", seg)
        out_segs.append(seg)
    return "".join(out_segs), fixes


def _entry_completeness_score(entry: dict) -> int:
    f = entry.get("fields") or {}
    score = 0
    if not arxiv_client.is_arxiv_preprint_entry(entry):
        score += 10
    if f.get("doi") and not str(f["doi"]).lower().startswith("10.48550/arxiv"):
        score += 5
    for k in ("journal", "booktitle", "pages", "volume", "number", "publisher", "year", "author"):
        if f.get(k):
            score += 1
    return score


def _choose_canonical_bib_key(keys: list[str], fields: dict, used_keys: set) -> str:
    # Prefer a clean human-readable key like 'vaswani2017attention' over 'vaswani2017_dup' or 'arxiv2305'
    clean_candidates = [
        k
        for k in keys
        if re.match(r"^[a-z]+[0-9]{4}[a-z0-9]+$", k, re.I)
        and not k.lower().startswith("arxiv")
        and not re.search(r"_(?:dup|copy|old|\d+)$", k, re.I)
    ]
    if clean_candidates:
        chosen = clean_candidates[0]
    else:
        authors_raw = fields.get("author") or "anon"
        first_author = authors_raw.split(" and ")[0].strip()
        surname = (
            first_author.split(",")[0].strip().lower()
            if "," in first_author
            else (first_author.split()[-1].lower() if first_author.split() else "anon")
        )
        surname = re.sub(r"[^a-z0-9]", "", surname) or "anon"
        year = re.sub(r"[^0-9]", "", str(fields.get("year") or ""))[:4] or "nd"
        t_words = [
            w.lower()
            for w in re.findall(r"[A-Za-z0-9]{3,}", re.sub(r"[{}]", "", fields.get("title") or ""))
            if w.lower() not in _STOPWORDS
        ]
        word = re.sub(r"[^a-z0-9]", "", t_words[0]) if t_words else "paper"
        chosen = keys[0] if keys and not keys[0].lower().startswith("arxiv") else f"{surname}{year}{word}"

    base = chosen
    idx = 2
    while chosen in used_keys:
        chosen = f"{base}_{idx}"
        idx += 1
    used_keys.add(chosen)
    return chosen


def sanitize_bibtex(
    bibtex_input: str,
    update_preprints: bool = True,
    output_path: str | None = None,
) -> dict:
    """Issue #25: Parse and sanitize a `.bib` file or BibTeX string:
    - Upgrade arXiv/CoRR preprints (`@misc` / `arXiv`) to their peer-reviewed conference (`@inproceedings`)
      or journal (`@article`) version with definitive DOI;
    - Deduplicate identical works while preserving the cleanest citation key;
    - Escape unescaped LaTeX characters (`&`, `%`, `_`) and protect uppercase acronyms (`{LLM}`, `{GPU}`, ...)."""
    raw_bib, _, _ = _load_text_or_workspace_file(bibtex_input)
    parsed = parse_bibtex_entries(raw_bib)
    if not parsed:
        raise _errors.HTTPError(400, "No valid BibTeX entries found in input.")

    out_file: Path | None = None
    if output_path is not None and str(output_path).strip():
        out_file = _validate_workspace_path(str(output_path).strip(), must_exist=False)

    # Group duplicates by (norm_doi | arxiv_id | norm_title)
    groups: list[list[dict]] = []
    group_index_by_sig: dict[str, int] = {}

    for entry in parsed:
        f = entry["fields"]
        doi = _ids._norm_doi(f.get("doi"))
        if doi and doi.startswith("10.48550/arxiv"):
            doi = None
        ax_id = arxiv_client.extract_arxiv_id_from_entry(entry)
        norm_t = arxiv_client._norm_title_key(f.get("title") or "")

        sigs = []
        if doi:
            sigs.append(f"doi:{doi}")
        if ax_id:
            sigs.append(f"ax:{ax_id.lower()}")
        if norm_t:
            sigs.append(f"title:{norm_t}")

        matched_idx = next((group_index_by_sig[s] for s in sigs if s in group_index_by_sig), None)
        if matched_idx is None:
            matched_idx = len(groups)
            groups.append([entry])
        else:
            groups[matched_idx].append(entry)
        for s in sigs:
            group_index_by_sig[s] = matched_idx

    duplicates_removed = 0
    preprints_upgraded = 0
    dois_completed = 0
    latex_escapes_fixed = 0
    updates: list[BibtexEntryUpdate] = []
    sanitized_blocks: list[str] = []
    used_keys: set = set()
    warnings: list[str] = []

    for grp in groups:
        grp_sorted = sorted(grp, key=_entry_completeness_score, reverse=True)
        primary = grp_sorted[0]
        merged_fields = dict(primary["fields"])
        etype = primary["entry_type"]
        all_keys = [e["key"] for e in grp]

        for other in grp_sorted[1:]:
            duplicates_removed += 1
            for fk, fv in other["fields"].items():
                if fv and not merged_fields.get(fk):
                    merged_fields[fk] = fv

        notes: list[str] = []
        old_venue = merged_fields.get("booktitle") or merged_fields.get("journal") or etype
        had_doi_before = bool(
            merged_fields.get("doi") and not str(merged_fields["doi"]).lower().startswith("10.48550/arxiv")
        )
        action = "deduplicated" if len(grp) > 1 else "kept"
        if len(grp) > 1:
            notes.append(f"Merged {len(grp)} duplicate entries ({', '.join(all_keys)}).")

        # Check preprint upgrade
        candidate_entry = {"entry_type": etype, "key": primary["key"], "fields": merged_fields}
        if update_preprints and arxiv_client.is_arxiv_preprint_entry(candidate_entry):
            resolved = arxiv_client.resolve_published_venue_for_preprint(candidate_entry)
            if resolved:
                preprints_upgraded += 1
                action = "upgraded_preprint"
                etype = resolved["entry_type"]
                venue_name = resolved["venue"]
                if etype == "inproceedings":
                    merged_fields["booktitle"] = venue_name
                    if arxiv_client._PREPRINT_VENUE_RE.search(str(merged_fields.get("journal") or "")):
                        merged_fields.pop("journal", None)
                else:
                    merged_fields["journal"] = venue_name
                if resolved.get("year"):
                    merged_fields["year"] = str(resolved["year"])
                if resolved.get("doi"):
                    merged_fields["doi"] = resolved["doi"]
                    if not had_doi_before:
                        dois_completed += 1
                if resolved.get("arxiv_id") and not merged_fields.get("eprint"):
                    merged_fields["eprint"] = str(resolved["arxiv_id"])
                notes.append(f"Upgraded arXiv preprint to {etype} in '{venue_name}'.")
        elif not had_doi_before and update_preprints:
            # Try to complete missing DOI for already-published entry if available in cache/APIs
            resolved = arxiv_client.resolve_published_venue_for_preprint(candidate_entry)
            if resolved and resolved.get("doi"):
                merged_fields["doi"] = resolved["doi"]
                dois_completed += 1
                if action == "kept":
                    action = "doi_completed"
                notes.append(f"Completed missing DOI: {resolved['doi']}.")

        # Standardize authors
        if merged_fields.get("author"):
            merged_fields["author"] = _normalize_authors_field(merged_fields["author"])

        # Escape LaTeX characters & protect acronyms
        entry_fixes = 0
        for field_name in ("title", "journal", "booktitle"):
            if merged_fields.get(field_name):
                fixed_val, cnt = _escape_latex_outside_math(
                    merged_fields[field_name], protect_acronyms=(field_name == "title")
                )
                merged_fields[field_name] = fixed_val
                entry_fixes += cnt
        if entry_fixes > 0:
            latex_escapes_fixed += entry_fixes
            if action == "kept":
                action = "latex_sanitized"
            notes.append(f"Escaped {entry_fixes} unescaped LaTeX special character(s).")

        canonical_key = _choose_canonical_bib_key(all_keys, merged_fields, used_keys)
        new_venue = merged_fields.get("booktitle") or merged_fields.get("journal") or etype

        # Format clean BibTeX block
        ordered_keys = [
            k
            for k in (
                "title",
                "author",
                "year",
                "booktitle",
                "journal",
                "volume",
                "number",
                "pages",
                "publisher",
                "doi",
                "eprint",
                "url",
            )
            if merged_fields.get(k)
        ]
        for k in merged_fields:
            if k not in ordered_keys and merged_fields[k]:
                ordered_keys.append(k)

        bib_lines = [f"@{etype}{{{canonical_key},"]
        for k in ordered_keys:
            bib_lines.append(f"  {k} = {{{merged_fields[k]}}},")
        bib_lines.append("}")
        sanitized_blocks.append("\n".join(bib_lines))

        updates.append(
            BibtexEntryUpdate(
                original_key=primary["key"],
                canonical_key=canonical_key,
                title=merged_fields.get("title") or "",
                action=action,
                old_venue_or_type=old_venue,
                new_venue_or_type=new_venue,
                doi=merged_fields.get("doi"),
                notes=notes,
            )
        )

    sanitized_text = "\n\n".join(sanitized_blocks) + ("\n" if sanitized_blocks else "")
    if out_file is not None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(sanitized_text, encoding="utf-8")

    report = BibtexSanitizeReport(
        entries_total=len(parsed),
        entries_out=len(sanitized_blocks),
        duplicates_removed=duplicates_removed,
        preprints_upgraded=preprints_upgraded,
        dois_completed=dois_completed,
        latex_escapes_fixed=latex_escapes_fixed,
        updates=updates,
        sanitized_bibtex=sanitized_text.strip(),
        warnings=warnings,
    )
    return report.model_dump()


# ---------------------------------------------------------------------------
# Issue #26: Peer-Review Rebuttal Copilot (`parse_and_draft_rebuttal`)
# ---------------------------------------------------------------------------

_REVIEWER_HEADER_RE = re.compile(
    r"^[ \t#*>-]*(?P<rev>Reviewer\s*#?\s*(?:[0-9]+|[A-Z])\b|R[1-9]\b|Meta[-\s]?Reviewer\b|Area\s+Chair\b)"
    r"[^\n:]*:?[ \t]*$",
    re.I | re.M,
)

_NEW_EXP_RE = re.compile(
    r"\b(?:run\s+additional|additional\s+experiments?|new\s+experiments?|more\s+experiments?|ablation|"
    r"evaluate\s+on|test\s+on\s+additional|more\s+seeds|statistical\s+significance|p[-\s]?values?|"
    r"confidence\s+intervals?|error\s+bars?|measure\s+latency|wall[-\s]clock|sensitivity\s+analysis)\b",
    re.I,
)
_BASELINE_CRIT_RE = re.compile(
    r"\b(?:compare\s+(?:against|with|to)|missing\s+baselines?|recent\s+baselines?|prior\s+work\s+by|"
    r"sota\s+baselines?|stronger\s+baselines?|outperformed\s+by|comparison\s+with)\b",
    re.I,
)
_NOTATION_FMT_RE = re.compile(
    r"\b(?:typos?|notation|equations?\s+\d*|figures?\s+\d*|tables?\s+\d*|captions?|define\s+symbol|"
    r"formatting|font\s+size|missing\s+reference|broken\s+ref)\b",
    re.I,
)
_METHOD_STAT_RE = re.compile(
    r"\b(?:assumptions?|data\s+leakage|confounding|endogeneity|selection\s+bias|overfitting|"
    r"identifi(?:cation|ability)|theoretical\s+guarantee|convergence|generalization\s+bound)\b",
    re.I,
)
_CLARITY_RE = re.compile(
    r"\b(?:unclear|clarify|how\s+does|what\s+is\s+meant|intuition|confusing|explain\s+why|elaborate)\b",
    re.I,
)


def _classify_rebuttal_comment(comment: str) -> tuple[str, str, bool]:
    low = comment or ""
    if _NEW_EXP_RE.search(low):
        return "new_experiment_request", "high", True
    if _BASELINE_CRIT_RE.search(low):
        req_exp = bool(re.search(r"\b(?:run|evaluate|compare\s+empirically|table)\b", low, re.I))
        return "baseline_criticism", "high", req_exp
    if _METHOD_STAT_RE.search(low):
        return "methodological_or_statistical", "high", False
    if _NOTATION_FMT_RE.search(low) and not _CLARITY_RE.search(low):
        return "notation_or_formatting", "low", False
    return "clarity_question", "medium", False


def _segment_reviewer_blocks(raw_text: str) -> list[tuple[str, list[str]]]:
    matches = list(_REVIEWER_HEADER_RE.finditer(raw_text or ""))
    blocks: list[tuple[str, str]] = []
    if not matches:
        blocks.append(("Reviewer 1", (raw_text or "").strip()))
    else:
        for i, m in enumerate(matches):
            rev_raw = re.sub(r"\s+", " ", m.group("rev").strip())
            if re.match(r"^R(\d+)$", rev_raw, re.I):
                rev_id = f"Reviewer {rev_raw[1:]}"
            else:
                rev_id = rev_raw.title().replace("#", "").strip()
                rev_id = re.sub(r"\s+", " ", rev_id)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
            blocks.append((rev_id, raw_text[start:end].strip()))

    out: list[tuple[str, list[str]]] = []
    for rev_id, body in blocks:
        points: list[str] = []
        for raw_line in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.)]|\[?[WQ]\d+\]?)\s+)", body):
            cleaned = re.sub(
                r"^\s*(?:Weaknesses|Questions|Comments|Major|Minor)\s*:?\s*", "", raw_line.strip(), flags=re.I
            )
            cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)]|\[?[WQ]\d+\]?:?)\s*", "", cleaned).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if len(cleaned) >= 15:
                points.append(cleaned)
        if not points and body.strip():
            points.append(re.sub(r"\s+", " ", body.strip()))
        if points:
            out.append((rev_id, points))
    return out


_REBUTTAL_TEMPLATES = {
    "new_experiment_request": (
        "We thank the reviewer for suggesting this experiment. [TODO: say whether you ran it; if so, the setup, "
        "the number of runs/seeds and the result, including any that did not favour your method.]",
        "[TODO: where the new results appear, e.g. 'Table X / Appendix Y'.]",
    ),
    "baseline_criticism": (
        "We thank the reviewer for pointing to this baseline. [TODO: whether you added the comparison, the result, "
        "and how it changes or qualifies your claims.]",
        "[TODO: the table/section updated with the baseline.]",
    ),
    "methodological_or_statistical": (
        "We thank the reviewer for this methodological point. [TODO: the assumption or analysis in question, your "
        "justification, and any sensitivity/robustness check you added.]",
        "[TODO: the methodology/limitations section updated.]",
    ),
    "notation_or_formatting": (
        "We thank the reviewer for catching this. [TODO: the notation/caption fix.]",
        "[TODO: the sections where notation or captions changed.]",
    ),
    "clarity_question": (
        "We thank the reviewer for the question. [TODO: a direct answer, pointing to the evidence in the paper.]",
        "[TODO: the paragraph or figure added/clarified, if any.]",
    ),
}


def parse_and_draft_rebuttal(
    reviews_input: str,
    paper_id: str | None = None,
    draft_input: str | None = None,
    output_format: str = "markdown",
) -> dict:
    """Issue #26: Parse raw peer-review comments by reviewer and objection, classify each point,
    flag items requiring new experiments, and build a rebuttal skeleton (Markdown and LaTeX) whose responses are
    templates with [TODO] placeholders - the author supplies the actual results."""
    t = _tools()
    reviews_text, _, _ = _load_text_or_workspace_file(reviews_input)
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    context: list = []
    if paper_id and str(paper_id).strip():
        try:
            p = t._resolve(str(paper_id).strip())
            if p.get("abstract"):
                context.append(_untrusted.excerpt(p["abstract"], paper_id=p.get("id"), section="Abstract"))
        except Exception:
            pass
    if draft_input and str(draft_input).strip():
        try:
            d_txt, d_label, _ = _load_text_or_workspace_file(str(draft_input).strip())
            context.append(_untrusted.excerpt(d_txt, origin="draft", section=d_label, limit=1200))
        except Exception:
            pass

    segmented = _segment_reviewer_blocks(reviews_text)
    reviewers_detected = [rev for rev, _ in segmented]
    points: list[RebuttalPoint] = []
    for rev_idx, (rev_id, comments) in enumerate(segmented, 1):
        for pt_idx, comment in enumerate(comments, 1):
            cat, sev, req_exp = _classify_rebuttal_comment(comment)
            response, action = _REBUTTAL_TEMPLATES.get(cat, _REBUTTAL_TEMPLATES["clarity_question"])
            points.append(
                RebuttalPoint(
                    point_id=f"R{rev_idx}.{pt_idx}",
                    reviewer_id=_untrusted.neutralize(rev_id),
                    category=cat,
                    priority_hint=_contract.priority(sev),
                    requires_new_experiment=req_exp,
                    comment=_untrusted.excerpt(comment, origin="reviews", limit=2000),
                    response_template=response,
                    manuscript_action_template=action,
                )
            )

    new_exp_count = sum(1 for pt in points if pt.requires_new_experiment)
    md_lines = [
        "# Author rebuttal skeleton",
        "",
        "> Templates only: replace every [TODO] with what you actually did and found. Noul does not know your "
        "results; never send a response that claims experiments you have not run.",
        "",
        f"- **Reviewers:** {', '.join(reviewers_detected)}",
        f"- **Points:** {len(points)} ({new_exp_count} asking for new experiments)",
    ]
    tex_lines = ["% Author rebuttal skeleton (Noul): replace every [TODO]", "\\section*{Author Response to Reviewers}"]
    current_rev = None
    for pt in points:
        if pt.reviewer_id != current_rev:
            current_rev = pt.reviewer_id
            md_lines += ["", f"## Response to {current_rev}"]
            tex_lines += ["", f"\\subsection*{{Response to {current_rev}}}"]
        badge = " — asks for a new experiment" if pt.requires_new_experiment else ""
        md_lines += [
            "",
            f"### [{pt.point_id}] {pt.category} (priority hint: {pt.priority_hint}){badge}",
            _untrusted.render(pt.comment),
            "",
            f"**Response:** {pt.response_template}",
            "",
            f"**Change in the manuscript:** {pt.manuscript_action_template}",
        ]
        tex_lines += [
            f"\\paragraph{{[{pt.point_id}] {pt.category}}} \\textit{{(reviewer comment {pt.point_id})}}",
            f"\\noindent\\textbf{{Response:}} {pt.response_template}\\\\",
            f"\\noindent\\textbf{{Change:}} {pt.manuscript_action_template}",
        ]

    report = RebuttalReport(
        tool="parse_and_draft_rebuttal",
        subject=AnalysisSubject(paper_id=str(paper_id).strip() if paper_id else None, source="reviews"),
        excerpts=context,
        rubric=_contract.rubric(
            "Rebuttal planning",
            (
                (
                    "valid",
                    "Which reviewer points are correct, which are misunderstandings, and which are out of scope?",
                ),
                ("evidence", "For each point, what evidence (existing results, new analysis) answers it?"),
                ("experiments", "Which requested experiments are feasible before the deadline?"),
                ("tone", "Is each response specific, polite and free of claims you cannot back up?"),
            ),
            "The categories and priorities are pattern-based guesses and the responses are templates. Help the "
            "author decide each point and fill the [TODO]s with their real results; never invent results.",
        ),
        extraction_confidence=_contract.extraction_confidence("reviews", length=len(reviews_text)) if points else "low",
        limitations=["Comment segmentation and classification are pattern-based; check the grouping per reviewer."],
        reviewers_detected=reviewers_detected,
        total_points=len(points),
        new_experiments_requested=new_exp_count,
        points=points,
        markdown_rebuttal="\n".join(md_lines).strip(),
        latex_rebuttal="\n".join(tex_lines).strip(),
    )
    report.rendered_markdown = report.markdown_rebuttal if fmt == "markdown" else report.latex_rebuttal
    return report.model_dump(mode="json")
