"""Citation recommender: finds the claims of a draft that need a reference and proposes cached or searched papers (`recommend_citations`)."""

import re

from noul import errors as _errors
from noul import log as _log
from noul.evidence import integrity as _integrity
from noul.evidence import untrusted as _untrusted
from noul.net import cache as _cache
from noul.schemas.authoring import (
    CitationInfillerReport,
    CitationRecommendationItem,
)
from noul.tools.authoring import bibtex as _bibtex
from noul.tools.authoring import common as _common

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
        if low not in _common._STOPWORDS and low not in words:
            words.append(low)
    return " ".join(words[:8]) or "scientific empirical study"


def _search_cached_papers_for_claim(query: str, limit: int = 2) -> list[dict]:
    q_terms = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    candidates = []
    try:
        seen_ids = set()
        for p in _cache.scan(_cache.PAPER, newest_first=False):
            if not p.get("title"):
                continue
            pid = p.get("id") or p.get("title")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            hay = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
            overlap = sum(2 if t in (p.get("title") or "").lower() else 1 for t in q_terms if t in hay)
            cites = int(p.get("citations") or 0)
            candidates.append((overlap, cites, p))
    except (TypeError, ValueError, AttributeError) as e:
        _log.logger.debug("cached-paper scan for citations failed: %s", type(e).__name__)
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c[2] for c in candidates[:limit]]


def _retrieve_papers_for_claim(query: str, max_per_claim: int = 2) -> list[dict]:
    t = _common._tools()
    papers = []
    try:
        res = t.search_papers(query, limit=max_per_claim, sort="citations")
        papers = list((res or {}).get("papers") or [])
    except _errors.UPSTREAM_ERRORS as e:
        _log.logger.debug("citation search failed: %s", type(e).__name__)
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
        evidence = paper.get("title") or "Direct empirical evidence reported."
    # Author, venue and abstract are third-party metadata: defuse delimiter look-alikes in the whole line.
    return _untrusted.neutralize(
        f"Candidate support: {first_author} et al. ({year}; {venue}; {citations} citations). "
        f"Check that it backs the claim: {evidence}"
    )


def recommend_citations(
    draft_text: str,
    max_per_claim: int = 2,
    citation_style: str = "latex",
) -> dict:
    """Issue #22: Scan a draft paragraph/document for `[CITATION NEEDED]` / `\\cite{?}` markers and
    unsubstantiated empirical claims, recommend 2-3 real papers per claim with explicit justifications,
    annotate inline citations, and output a ready-to-use BibTeX block."""
    text, _, _ = _common._load_text_or_workspace_file(draft_text)
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
        # Crossref check on the few candidates (cached weekly): search sources do not flag every retraction.
        papers = [_integrity.enrich(p, versions=False) for p in _retrieve_papers_for_claim(query, max_per_claim=max_k)]
        retracted = [p for p in papers if _integrity.is_retracted(p)]
        if any(p.get(_integrity.CHECK_FAILED) for p in papers):
            warnings.append(f"Claim '{sent[:60]}...': {_integrity.CHECK_FAILED_WARNING}")
        if retracted:
            # Never propose a retracted paper as support; say which candidates were dropped and why.
            papers = [p for p in papers if not _integrity.is_retracted(p)]
            dropped = "; ".join(f"{p.get('id')} ({_integrity.describe(p)})" for p in retracted)
            warnings.append(f"Retracted candidate(s) not proposed for claim '{sent[:60]}...': {dropped}")

        if not papers:
            if not retracted:
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
                k = _bibtex._make_bibtex_key(p, used_keys)
                if pid:
                    key_by_paper_id[pid] = k
                bibtex_entries[k] = _bibtex._format_bibtex_entry_from_paper(p, k)
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
            new_sent = f"{sent[:-1]} {cite_tag}{sent[-1]}" if sent.endswith((".", "!", "?")) else f"{sent} {cite_tag}"

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
