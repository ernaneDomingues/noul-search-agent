"""Europe PMC search and parsing."""

import urllib.error
import urllib.parse
import urllib.request

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.search import engine as _engine


def _europepmc_pdf_url(pmc_id):
    return "https://europepmc.org/backend/ptpmcrender.fcgi?" + urllib.parse.urlencode(
        {"accid": str(pmc_id).strip(), "blobtype": "pdf"}
    )


def _parse_europepmc_result(it):
    if not it or not isinstance(it, dict):
        return None
    title = _ids._clean_text(it.get("title") or "")
    if not title:
        return None
    doi = _ids._norm_doi(it.get("doi"))
    pmid = str(it.get("pmid") or "").strip() or None
    pmc_id = str(it.get("pmcid") or "").strip() or None
    abstract = _ids._clean_text(it.get("abstractText") or "")
    yr_txt = str(it.get("pubYear") or "")
    year = int(yr_txt) if yr_txt.isdigit() else None
    journal = _ids._clean_text(
        ((it.get("journalInfo") or {}).get("journal") or {}).get("title")
        or it.get("journalTitle")
        or ("Preprint" if it.get("source") == "PPR" else "")
    )
    authors = []
    for a in ((it.get("authorList") or {}).get("author") or [])[:10]:
        name = f"{a.get('firstName') or ''} {a.get('lastName') or ''}".strip() or a.get("fullName") or ""
        if name:
            authors.append(name)
    if not authors and it.get("authorString"):
        authors = [s.strip().rstrip(".") for s in str(it["authorString"]).split(",") if s.strip()][:10]
    citations = int(it.get("citedByCount") or 0)
    is_oa = str(it.get("isOpenAccess") or "").upper() == "Y"
    pdf_url = None
    for ft in (it.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if ft.get("documentStyle") == "pdf" and ft.get("availabilityCode") in ("OA", "F") and ft.get("url"):
            pdf_url = ft["url"]
            break
    if not pdf_url and pmc_id and is_oa:
        pdf_url = _europepmc_pdf_url(pmc_id)
    pub_types = list((it.get("pubTypeList") or {}).get("pubType") or [])
    if it.get("source") == "PPR":
        pub_types.append("preprint")
    ev = _classify.classify_evidence(title, abstract, pub_types)
    impact, tier = _impact._estimate_impact(citations, year, journal)
    pid = _ids._paper_id(doi=doi, pmid=pmid, title=title)
    mesh_terms = _ids._unique(
        [
            m.get("descriptorName")
            for m in ((it.get("meshHeadingList") or {}).get("meshHeading") or [])
            if isinstance(m, dict)
        ]
    )[:15]
    return _engine._annotate_paper(
        {
            "id": pid,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": journal,
            "doi": doi,
            "pmid": pmid,
            "pmc_id": pmc_id,
            "citations": citations,
            "abstract": abstract,
            "evidence": ev,
            "impact": impact,
            "tier": tier,
            "open_access": bool(is_oa or pdf_url),
            "pdf_url": pdf_url,
            "mesh_terms": mesh_terms,
            "sources": ["europepmc"],
        }
    )


def _search_europepmc(query, limit=20, year_min=None, open_access=False):
    q = f"({query})"
    if year_min:
        q += f" AND PUB_YEAR:[{int(year_min)} TO 3000]"
    if open_access:
        q += " AND OPEN_ACCESS:y"
    params = {"query": q, "resultType": "core", "format": "json", "pageSize": min(limit, 50)}
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
    data = _nhttp._http_json(url)
    out = []
    for it in ((data.get("resultList") or {}).get("result")) or []:
        p = _parse_europepmc_result(it)
        if p:
            out.append(p)
    return out
