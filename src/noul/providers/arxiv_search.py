"""arXiv API search (Atom feed)."""

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.search import engine as _engine


def _search_arxiv(query, limit=15, year_min=None):
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(limit, 25),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    xml_txt = _nhttp._http_get(url)
    root = ET.fromstring(xml_txt)
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        title = _ids._clean_text(entry.findtext("a:title", "", ns))
        if not title:
            continue
        abstract = _ids._clean_text(entry.findtext("a:summary", "", ns))
        pub = entry.findtext("a:published", "", ns)
        year = int(pub[:4]) if len(pub) >= 4 and pub[:4].isdigit() else None
        if year_min and year and year < int(year_min):
            continue
        id_url = entry.findtext("a:id", "", ns)
        arxiv_id = id_url.rstrip("/").split("/abs/")[-1] if "/abs/" in id_url else id_url.rstrip("/").split("/")[-1]
        doi = _ids._norm_doi(entry.findtext("arxiv:doi", "", ns))
        authors = [
            _ids._clean_text(a.findtext("a:name", "", ns))
            for a in entry.findall("a:author", ns)[:10]
            if a.findtext("a:name", "", ns)
        ]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None
        prim = entry.find("arxiv:primary_category", ns)
        primary_category = prim.attrib.get("term") if prim is not None else None
        categories = _ids._unique(
            [primary_category] + [cat.attrib.get("term") for cat in entry.findall("a:category", ns)]
        )
        ev = _classify.classify_evidence(title, abstract, ["preprint"])
        impact, tier = _impact._estimate_impact(0, year, "arXiv")
        pid = _ids._paper_id(doi=doi, arxiv_id=arxiv_id, title=title)
        out.append(
            _engine._annotate_paper(
                {
                    "id": pid,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "journal": "arXiv",
                    "doi": doi,
                    "arxiv_id": arxiv_id,
                    "citations": 0,
                    "abstract": abstract,
                    "evidence": ev,
                    "impact": impact,
                    "tier": tier,
                    "open_access": True,
                    "pdf_url": pdf_url,
                    "primary_category": primary_category or (categories[0] if categories else None),
                    "categories": categories,
                    "sources": ["arxiv"],
                }
            )
        )
    return out
