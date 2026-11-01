"""Full-text retrieval (PDF/HTML/Europe PMC) and question-driven chunk selection."""

import io
import re
import urllib.error
import urllib.parse
import urllib.request

from noul import config as _config
from noul import ids as _ids
from noul.net import cache as _cache
from noul.net import http as _nhttp
from noul.providers import europepmc as _europepmc
from noul.search import engine as _engine

MIN_HTML_FULLTEXT_CHARS = 2500


def resolve_pdf_url(paper):
    """Attempt to resolve a direct Open Access PDF URL for a paper dict via multiple sources."""
    if paper.get("pdf_url"):
        return paper["pdf_url"], "metadata"
    if paper.get("pmc_id"):
        return _europepmc._europepmc_pdf_url(paper["pmc_id"]), "europepmc"
    if paper.get("arxiv_id"):
        return f"https://arxiv.org/pdf/{paper['arxiv_id']}.pdf", "arxiv"

    doi = _ids._norm_doi(paper.get("doi"))
    if doi:
        # 1. Try OpenAlex by DOI
        try:
            url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi, safe='')}{_nhttp._mailto_qs()}"
            w = _nhttp._http_json(url, timeout=10)
            best_oa = w.get("best_oa_location") or {}
            oa_info = w.get("open_access") or {}
            pdf = best_oa.get("pdf_url") or oa_info.get("oa_url")
            if pdf:
                return pdf, "openalex"
        except Exception:
            pass

        # 2. Try Unpaywall if email is configured
        email = _config._email()
        if email:
            try:
                uw_url = (
                    f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}?email={urllib.parse.quote(email)}"
                )
                uw = _nhttp._http_json(uw_url, timeout=10)
                best = uw.get("best_oa_location") or {}
                pdf = best.get("url_for_pdf") or best.get("url")
                if pdf:
                    return pdf, "unpaywall"
            except Exception:
                pass

        # 3. Try EuropePMC by DOI
        try:
            epmc_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
                {"query": f'DOI:"{doi}"', "format": "json", "ResultType": "core"}
            )
            ed = _nhttp._http_json(epmc_url, timeout=10)
            res_list = ((ed.get("resultList") or {}).get("result")) or []
            if res_list:
                pmcid = res_list[0].get("pmcid")
                if pmcid:
                    return _europepmc._europepmc_pdf_url(pmcid), "europepmc"
        except Exception:
            pass

    return None, None


def _extract_pdf_text(pdf_bytes):
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages[:35]:
            t = page.extract_text() or ""
            if t.strip():
                pages.append(t)
        return _ids._clean_text("\n\n".join(pages))
    except Exception:
        return ""


_HTML_BOILERPLATE_RE = re.compile(r"<(script|style|nav|header|footer|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S)


def _html_to_text(raw_html):
    return _ids._clean_text(_HTML_BOILERPLATE_RE.sub(" ", raw_html or ""))


def fulltext(id):
    p = _engine.resolve_paper(id)
    pid = p["id"]
    cached_ft = _cache.cache_get("ft:" + pid)
    if cached_ft and cached_ft.get("text"):
        return {
            "id": pid,
            "title": p.get("title"),
            "available": True,
            "source": cached_ft.get("source", "cache"),
            "url": cached_ft.get("url"),
        }

    pdf_url, source = resolve_pdf_url(p)
    if pdf_url:
        try:
            raw = _nhttp._http_get(pdf_url, timeout=20, binary=True, public_only=True)
            if raw.startswith(b"%PDF"):
                text = _extract_pdf_text(raw)
                if text and len(text) > 200:
                    _cache.cache_set("ft:" + pid, {"text": text[:120000], "url": pdf_url, "source": source})
                    return {"id": pid, "title": p.get("title"), "available": True, "source": source, "url": pdf_url}
            else:
                # Possibly an HTML full-text page; landing/login/paywall pages are short once boilerplate is removed.
                html_text = _html_to_text(raw.decode("utf-8", errors="replace"))
                if len(html_text) >= MIN_HTML_FULLTEXT_CHARS:
                    _cache.cache_set("ft:" + pid, {"text": html_text[:120000], "url": pdf_url, "source": source})
                    return {"id": pid, "title": p.get("title"), "available": True, "source": source, "url": pdf_url}
        except Exception:
            pass

    # Fallback: check EuropePMC fullTextXML if PMC ID is available
    if p.get("pmc_id"):
        try:
            xml_url = (
                "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                f"{urllib.parse.quote(str(p['pmc_id']).strip(), safe='')}/fullTextXML"
            )
            xml_txt = _nhttp._http_get(xml_url, timeout=15)
            cleaned = _ids._clean_text(xml_txt)
            if len(cleaned) > 500:
                _cache.cache_set("ft:" + pid, {"text": cleaned[:120000], "url": xml_url, "source": "europepmc_xml"})
                return {
                    "id": pid,
                    "title": p.get("title"),
                    "available": True,
                    "source": "europepmc_xml",
                    "url": xml_url,
                }
        except Exception:
            pass

    return {
        "id": pid,
        "title": p.get("title"),
        "available": False,
        "pdf_url": pdf_url,
        "doi_url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "note": "Open-access full text not directly extractable; use abstract or download_papers.",
    }


def pick_chunks(text, question="", n=6, chunk_size=1200):
    if not text:
        return []
    words = text.split()
    chunks = []
    step = 180
    for i in range(0, len(words), step):
        seg = " ".join(words[i : i + 220])
        if seg:
            chunks.append(seg[:chunk_size])
    if not question or not chunks:
        return chunks[:n]
    q_terms = set(re.findall(r"[a-z0-9]{3,}", question.lower()))
    if not q_terms:
        return chunks[:n]
    scored = []
    for idx, ch in enumerate(chunks):
        low = ch.lower()
        score = sum(low.count(t) * (2 if len(t) > 5 else 1) for t in q_terms)
        scored.append((score, -idx, ch))
    scored.sort(reverse=True)
    return [ch for _, _, ch in scored[:n]]
