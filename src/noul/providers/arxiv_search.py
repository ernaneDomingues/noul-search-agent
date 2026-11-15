"""arXiv API search (Atom feed)."""

import urllib.parse
import xml.etree.ElementTree as ET

from noul import ids as _ids
from noul.net import http as _nhttp
from noul.providers import assemble as _assemble


def _search_arxiv(query, limit=15, year_min=None):
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(limit, 25),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    return _parse_feed(_nhttp._http_get(url), year_min=year_min)


def fetch_by_ids(arxiv_ids):
    """Papers for explicit arXiv ids (the API's `id_list`), e.g. ["1706.03762"]; [] when none is found."""
    ids = [str(x).strip() for x in arxiv_ids if str(x).strip()]
    if not ids:
        return []
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"id_list": ",".join(ids), "max_results": len(ids)}
    )
    return _parse_feed(_nhttp._http_get(url))


def _parse_feed(xml_txt, year_min=None):
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
            for a in entry.findall("a:author", ns)
            if a.findtext("a:name", "", ns)
        ]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None
        prim = entry.find("arxiv:primary_category", ns)
        primary_category = prim.attrib.get("term") if prim is not None else None
        categories = _ids._unique(
            [primary_category] + [cat.attrib.get("term") for cat in entry.findall("a:category", ns)]
        )
        out.append(
            _assemble.assemble_paper(
                "arxiv",
                title=title,
                abstract=abstract,
                authors=authors,
                year=year,
                journal="arXiv",
                pub_types=["preprint"],
                open_access=True,
                pdf_url=pdf_url,
                ids={"doi": doi, "arxiv_id": arxiv_id},
                primary_category=primary_category or (categories[0] if categories else None),
                categories=categories,
            )
        )
    return out
