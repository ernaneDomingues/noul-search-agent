"""Semantic Scholar search and paper parsing."""

import urllib.error
import urllib.parse
import urllib.request

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.search import engine as _engine

S2_PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,authors,citationCount,isOpenAccess,openAccessPdf,"
    "publicationTypes,fieldsOfStudy,s2FieldsOfStudy"
)


def _search_s2(query, limit=20, year_min=None, open_access=False):
    params = {"query": query, "limit": min(limit, 40), "fields": S2_PAPER_FIELDS}
    if year_min:
        params["year"] = f"{int(year_min)}-"
    if open_access:
        params["openAccessPdf"] = ""
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    data = _nhttp._http_json(url)
    out = []
    for item in data.get("data") or []:
        p = _parse_s2_paper(item)
        if p:
            out.append(p)
    return out


def _parse_s2_paper(item):
    if not item or not isinstance(item, dict):
        return None
    title = _ids._clean_text(item.get("title") or "")
    if not title:
        return None
    ext = item.get("externalIds") or {}
    doi = _ids._norm_doi(ext.get("DOI"))
    pmid = str(ext.get("PubMed") or "") or None
    arxiv_id = ext.get("ArXiv")
    s2_id = item.get("paperId")
    abstract = _ids._clean_text(item.get("abstract") or "")
    year = item.get("year")
    journal = item.get("venue") or ("arXiv" if arxiv_id else "")
    authors = [a.get("name") for a in (item.get("authors") or [])[:10] if a.get("name")]
    citations = int(item.get("citationCount") or 0)
    oa_pdf = (item.get("openAccessPdf") or {}).get("url")
    if not oa_pdf and arxiv_id:
        oa_pdf = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    is_oa = bool(item.get("isOpenAccess") or oa_pdf)
    ev = _classify.classify_evidence(title, abstract, item.get("publicationTypes") or [])
    impact, tier = _impact._estimate_impact(citations, year, journal)
    pid = _ids._paper_id(doi=doi, pmid=pmid, s2_id=s2_id, arxiv_id=arxiv_id, title=title)
    fields = _ids._unique(
        list(item.get("fieldsOfStudy") or [])
        + [f.get("category") for f in (item.get("s2FieldsOfStudy") or []) if isinstance(f, dict)]
    )
    return _engine._annotate_paper(
        {
            "id": pid,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": journal,
            "doi": doi,
            "pmid": pmid,
            "arxiv_id": arxiv_id,
            "s2_id": s2_id,
            "citations": citations,
            "abstract": abstract,
            "evidence": ev,
            "impact": impact,
            "tier": tier,
            "open_access": is_oa,
            "pdf_url": oa_pdf,
            "primary_category": fields[0] if fields else None,
            "fields_of_study": fields,
            "sources": ["s2"],
        }
    )
