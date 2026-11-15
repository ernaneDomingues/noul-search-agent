"""The one place a source's fields become a paper record (`noul.schemas.paper.Paper`)."""

from __future__ import annotations

from typing import Any

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.providers import annotate as _annotate
from noul.schemas.paper import Paper

MAX_AUTHORS = 100


def assemble_paper(
    source: str,
    *,
    title: str,
    abstract: str = "",
    authors: list[str] | None = None,
    year: int | None = None,
    journal: str = "",
    citations: int = 0,
    pub_types: list[Any] | None = None,
    open_access: bool = False,
    pdf_url: str | None = None,
    ids: dict[str, Any] | None = None,
    **extra: Any,
) -> Paper:
    """Build the record of one paper found in `source`.

    `ids` holds the identifiers the source knows (`doi`, `pmid`, `arxiv_id`, `s2_id`, `openalex_id`): they name
    the paper (`noul.ids._paper_id`) and are stored as given. `extra` is whatever else the source offers
    (categories, MeSH terms, a retraction...). The evidence level, the impact band and the domain and artifact
    flags are derived here, the same way for every source."""
    ids = dict(ids or {})
    impact, tier = _impact._estimate_impact(citations, year, journal)
    paper: dict[str, Any] = {
        "id": _ids._paper_id(**ids, title=title),
        "title": title,
        "authors": list(authors or [])[:MAX_AUTHORS],
        "year": year,
        "journal": journal,
        **ids,
        "citations": citations,
        "abstract": abstract,
        "evidence": _classify.classify_evidence(title, abstract, pub_types or []),
        "impact": impact,
        "tier": tier,
        "open_access": open_access,
        "pdf_url": pdf_url,
        **extra,
        "sources": [source],
    }
    return _annotate.annotate_paper(paper)  # type: ignore[no-any-return, unused-ignore]
