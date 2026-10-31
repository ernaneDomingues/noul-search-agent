"""OpenAlex search and work parsing."""

import urllib.error
import urllib.parse
import urllib.request

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.search import engine as _engine


def _reconstruct_openalex_abstract(inv):
    if not inv or not isinstance(inv, dict):
        return ""
    words = []
    for word, positions in inv.items():
        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int):
                    words.append((pos, word))
    if not words:
        return ""
    words.sort(key=lambda x: x[0])
    return _ids._clean_text(" ".join(w for _, w in words))


def _parse_openalex_work(w):
    if not w or not isinstance(w, dict):
        return None
    title = _ids._clean_text(w.get("title") or w.get("display_name") or "")
    if not title:
        return None
    doi = _ids._norm_doi(w.get("doi"))
    oa_id = (w.get("id") or "").rstrip("/").split("/")[-1]
    ids = w.get("ids") or {}
    pmid = (ids.get("pmid") or "").rstrip("/").split("/")[-1] or None
    abstract = _reconstruct_openalex_abstract(w.get("abstract_inverted_index"))
    year = w.get("publication_year")
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    journal = src.get("display_name") or ""
    authors = []
    for a in (w.get("authorships") or [])[:10]:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    citations = int(w.get("cited_by_count") or 0)
    oa_info = w.get("open_access") or {}
    best_oa = w.get("best_oa_location") or {}
    pdf_url = best_oa.get("pdf_url") or loc.get("pdf_url") or oa_info.get("oa_url")
    is_oa = bool(oa_info.get("is_oa") or pdf_url)
    pub_types = [w.get("type"), w.get("type_crossref")]
    ev = _classify.classify_evidence(title, abstract, pub_types)
    impact, tier = _impact._estimate_impact(citations, year, journal)
    pid = _ids._paper_id(doi=doi, openalex_id=oa_id, pmid=pmid, title=title)
    topic = w.get("primary_topic") or {}
    fields = _ids._unique([(topic.get(k) or {}).get("display_name") for k in ("field", "domain")])
    if not fields:
        fields = _ids._unique(
            [
                cn.get("display_name")
                for cn in (w.get("concepts") or [])
                if cn.get("level") == 0 and float(cn.get("score") or 0) >= 0.3
            ]
        )
    primary_category = (topic.get("subfield") or {}).get("display_name") or (fields[0] if fields else None)
    return _engine._annotate_paper(
        {
            "id": pid,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": journal,
            "doi": doi,
            "pmid": pmid,
            "openalex_id": oa_id,
            "citations": citations,
            "abstract": abstract,
            "evidence": ev,
            "impact": impact,
            "tier": tier,
            "open_access": is_oa,
            "pdf_url": pdf_url,
            "referenced_works": [x.rstrip("/").split("/")[-1] for x in (w.get("referenced_works") or [])[:40]],
            "related_works": [x.rstrip("/").split("/")[-1] for x in (w.get("related_works") or [])[:40]],
            "primary_category": primary_category,
            "fields_of_study": fields,
            "sources": ["openalex"],
        }
    )


def _search_openalex(query, limit=25, year_min=None, open_access=False):
    params = _nhttp._with_mailto({"search": query, "per-page": min(limit, 50)})
    filters = []
    if year_min:
        filters.append(f"from_publication_date:{int(year_min)}-01-01")
    if open_access:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = _nhttp._http_json(url)
    out = []
    for w in data.get("results") or []:
        p = _parse_openalex_work(w)
        if p:
            out.append(p)
    return out
