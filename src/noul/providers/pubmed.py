"""PubMed (NCBI E-utilities) search and XML parsing."""

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from noul import config as _config
from noul import ids as _ids
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import http as _nhttp
from noul.providers import europepmc as _europepmc
from noul.search import engine as _engine


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
        title = (
            _ids._clean_text("".join(art.find(".//ArticleTitle").itertext()))
            if art.find(".//ArticleTitle") is not None
            else ""
        )
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
        for au in art.findall(".//AuthorList/Author")[:10]:
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
        ev = _classify.classify_evidence(title, abstract, pub_types)
        impact, tier = _impact._estimate_impact(0, year, journal)
        pdf_url = _europepmc._europepmc_pdf_url(pmc_id) if pmc_id else None
        pid = _ids._paper_id(doi=doi, pmid=pmid, title=title)
        out.append(
            _engine._annotate_paper(
                {
                    "id": pid,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "journal": journal,
                    "doi": doi,
                    "pmid": pmid,
                    "pmc_id": pmc_id,
                    "citations": 0,
                    "abstract": abstract,
                    "evidence": ev,
                    "impact": impact,
                    "tier": tier,
                    "open_access": bool(pmc_id),
                    "pdf_url": pdf_url,
                    "mesh_terms": mesh_terms,
                    "sources": ["pubmed"],
                }
            )
        )
    return out
