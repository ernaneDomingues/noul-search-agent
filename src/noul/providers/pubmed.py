"""PubMed (NCBI E-utilities) search and XML parsing."""

import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from noul import config as _config
from noul import ids as _ids
from noul.evidence import integrity as _integrity
from noul.net import http as _nhttp
from noul.providers import assemble as _assemble
from noul.providers import europepmc as _europepmc


def _eutils_params(params):
    params["tool"] = "noul-search-agent"
    email = _config._email()
    if email:
        params["email"] = email
    return params


def _search_pubmed(query, limit=20, year_min=None):
    term = query
    if year_min:
        term = f"({query}) AND ({int(year_min)}:3000[pdat])"
    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
        _eutils_params({"db": "pubmed", "term": term, "retmode": "json", "retmax": min(limit, 30), "sort": "relevance"})
    )
    sdata = _nhttp._http_json(esearch)
    id_list = (sdata.get("esearchresult") or {}).get("idlist") or []
    if not id_list:
        return []
    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        _eutils_params({"db": "pubmed", "id": ",".join(id_list), "retmode": "xml"})
    )
    xml_txt = _nhttp._http_get(efetch)
    return _parse_pubmed_xml(xml_txt)


def _parse_pubmed_xml(xml_txt):
    out = []
    root = ET.fromstring(xml_txt)
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//MedlineCitation/PMID")
        title_el = art.find(".//ArticleTitle")
        title = _ids._clean_text("".join(title_el.itertext())) if title_el is not None else ""
        if not title:
            continue
        abs_parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            lbl = ab.attrib.get("Label")
            txt = _ids._clean_text("".join(ab.itertext()))
            if txt:
                abs_parts.append(f"{lbl}: {txt}" if lbl else txt)
        abstract = " ".join(abs_parts)
        year_txt = (
            art.findtext(".//Journal/JournalIssue/PubDate/Year")
            or art.findtext(".//Journal/JournalIssue/PubDate/MedlineDate")
            or ""
        )
        m_yr = re.search(r"\b(19\d\d|20\d\d)\b", year_txt)
        year = int(m_yr.group(1)) if m_yr else None
        journal = _ids._clean_text(art.findtext(".//Journal/Title") or "")
        authors = []
        for au in art.findall(".//AuthorList/Author"):
            last = au.findtext("LastName") or ""
            fore = au.findtext("ForeName") or au.findtext("Initials") or ""
            full = f"{fore} {last}".strip()
            if full:
                authors.append(full)
        doi = None
        pmc_id = None
        for aid in art.findall(".//PubmedData/ArticleIdList/ArticleId"):
            id_type = aid.attrib.get("IdType")
            if id_type == "doi" and aid.text:
                doi = _ids._norm_doi(aid.text)
            elif id_type == "pmc" and aid.text:
                pmc_id = aid.text.strip()
        pub_types = [pt.text for pt in art.findall(".//PublicationTypeList/PublicationType") if pt.text]
        mesh_terms = _ids._unique([d.text for d in art.findall(".//MeshHeadingList/MeshHeading/DescriptorName")])[:15]
        retraction = _integrity.from_pub_types(pub_types, "pubmed")
        extra: dict[str, Any] = {"retraction": retraction} if retraction else {}
        out.append(
            _assemble.assemble_paper(
                "pubmed",
                title=title,
                abstract=abstract,
                authors=authors,
                year=year,
                journal=journal,
                pub_types=pub_types,
                open_access=bool(pmc_id),
                pdf_url=_europepmc._europepmc_pdf_url(pmc_id) if pmc_id else None,
                ids={"doi": doi, "pmid": pmid},
                pmc_id=pmc_id,
                mesh_terms=mesh_terms,
                **extra,
            )
        )
    return out
