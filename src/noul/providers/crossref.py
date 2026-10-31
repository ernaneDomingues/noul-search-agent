"""Crossref search."""

import urllib.error
import urllib.parse
import urllib.request

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.search import engine as _engine


def _search_crossref(query, limit=15, year_min=None):
    params = _nhttp._with_mailto({"query": query, "rows": min(limit, 25)})
    if year_min:
        params["filter"] = f"from-pub-date:{int(year_min)}"
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = _nhttp._http_json(url)
    items = (data.get("message") or {}).get("items") or []
    out = []
    for it in items:
        titles = it.get("title") or []
        title = _ids._clean_text(titles[0] if titles else "")
        if not title:
            continue
        doi = _ids._norm_doi(it.get("DOI"))
        abstract = _ids._clean_text(it.get("abstract") or "")
        yr = None
        for k in ("published-print", "published-online", "issued", "created"):
            parts = ((it.get(k) or {}).get("date-parts") or [[]])[0]
            if parts and isinstance(parts[0], int):
                yr = parts[0]
                break
        containers = it.get("container-title") or []
        journal = _ids._clean_text(containers[0] if containers else "")
        authors = []
        for a in (it.get("author") or [])[:10]:
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)
        citations = int(it.get("is-referenced-by-count") or 0)
        pdf_url = None
        for lnk in it.get("link") or []:
            if "pdf" in (lnk.get("content-type") or "").lower() and lnk.get("URL"):
                pdf_url = lnk["URL"]
                break
        ev = _classify.classify_evidence(title, abstract, [it.get("type")])
        impact, tier = _impact._estimate_impact(citations, yr, journal)
        pid = _ids._paper_id(doi=doi, title=title)
        subjects = _ids._unique(it.get("subject") or [])
        out.append(
            _engine._annotate_paper(
                {
                    "id": pid,
                    "title": title,
                    "authors": authors,
                    "year": yr,
                    "journal": journal,
                    "doi": doi,
                    "citations": citations,
                    "abstract": abstract,
                    "evidence": ev,
                    "impact": impact,
                    "tier": tier,
                    "open_access": bool(pdf_url),
                    "pdf_url": pdf_url,
                    "primary_category": subjects[0] if subjects else None,
                    "fields_of_study": subjects,
                    "sources": ["crossref"],
                }
            )
        )
    return out
