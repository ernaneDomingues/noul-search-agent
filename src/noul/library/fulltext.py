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


# Version of the cached full-text layout. 2: PDF page breaks as \f, line breaks kept, no tag stripping.
# 3: PDF words hyphenated across lines rejoined; Europe PMC JATS XML keeps section headings and table captions.
PDF_TEXT_FORMAT = 3
MAX_JATS_BYTES = 8 * 1024 * 1024


def is_stale(entry):
    """A cached full text extracted with an older layout (it may have lost pages or structure). Entries without
    a source URL (seeded locally) are kept."""
    if entry.get("format") == PDF_TEXT_FORMAT:
        return False
    return bool(entry.get("url"))


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
        return normalize_pdf_text([page.extract_text() or "" for page in reader.pages[:35]])
    except Exception:
        return ""


def normalize_pdf_text(pages):
    """Page texts (a list, or one string with form feeds) -> the cached layout: each page cleaned, pages joined by
    "\\n\\f\\n" (a page break that is also a line break, so a heading at the top of a page starts a line)."""
    if isinstance(pages, str):
        pages = pages.split("\f")
    return "\n\f\n".join(_clean_pdf_page(p) for p in pages).strip()


def _clean_pdf_page(text):
    """Whitespace-normalised page text that keeps line breaks (for section headings) and never strips `<...>`:
    PDF text is not HTML, and a formula such as `n < d` would otherwise swallow everything up to the next `>`."""
    text = text.replace("\f", " ").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _dehyphenate(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_HYPHEN_BREAK = re.compile(r"(\b[A-Za-z]+)-\n(?=([a-z][\w-]*))")


def _dehyphenate(text):
    """Rejoin words split by a line-end hyphen. A lowercase fragment loses the hyphen ("trans-\\nlation" ->
    "translation"); a capitalised fragment or a hyphenated continuation keeps it ("English-\\nto-German",
    "state-\\nof-the-art")."""

    def join(m):
        # Keep the hyphen for capitalised fragments and for compounds that continue with hyphens ("state-of-the-art").
        keep = not m.group(1).islower() or "-" in m.group(2)
        return m.group(1) + ("-" if keep else "")

    return _HYPHEN_BREAK.sub(join, text)


def _jats_text(xml_bytes):
    """Plain text from Europe PMC / PMC JATS XML: title, abstract, sections as `## <label> <title>` headings
    (recognised by pdf_parser.find_sections), paragraphs and `Table N: caption` lines. Documents with a DTD or
    entity declarations, or larger than MAX_JATS_BYTES, are refused (no entity expansion, no external fetches)."""
    import xml.etree.ElementTree as ET

    raw = xml_bytes if isinstance(xml_bytes, bytes) else str(xml_bytes).encode("utf-8")
    if (
        len(raw) > MAX_JATS_BYTES
        or re.search(rb"<!(?:DOCTYPE|ENTITY)", raw[:65536] + raw[-4096:], re.I)
        or b"<!ENTITY" in raw
    ):
        return ""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ""

    def local(tag):
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    def flat(el):
        return re.sub(r"\s+", " ", "".join(el.itertext())).strip()

    lines = []
    title = next((el for el in root.iter() if local(el.tag) == "article-title"), None)
    if title is not None:
        lines.append(flat(title))
    for abstract in (el for el in root.iter() if local(el.tag) == "abstract"):
        lines += ["", "Abstract", flat(abstract)]
        break

    def walk(el, depth):
        for child in el:
            tag = local(child.tag)
            if tag == "sec":
                label = next((flat(c) for c in child if local(c.tag) == "label"), "")
                head = next((flat(c) for c in child if local(c.tag) == "title"), "")
                if head:
                    lines.extend(["", "#" * min(depth + 1, 4) + " " + " ".join(x for x in (label, head) if x)])
                walk(child, depth + 1)
            elif tag == "p":
                lines.append(flat(child))
            elif tag == "table-wrap":
                label = next((flat(c) for c in child if local(c.tag) == "label"), "Table")
                caption = next((flat(c) for c in child if local(c.tag) == "caption"), "")
                lines.append(f"{label}: {caption}".strip())
            elif tag not in ("label", "title"):
                walk(child, depth)

    for body in (el for el in root.iter() if local(el.tag) == "body"):
        walk(body, 1)
        break
    return "\n".join(lines).strip()


_HTML_BOILERPLATE_RE = re.compile(r"<(script|style|nav|header|footer|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S)


def _html_to_text(raw_html):
    return _ids._clean_text(_HTML_BOILERPLATE_RE.sub(" ", raw_html or ""))


def fulltext(id):
    p = _engine.resolve_paper(id)
    pid = p["id"]
    cached_ft = _cache.cache_get("ft:" + pid)
    stale = None
    if cached_ft and is_stale(cached_ft):
        stale, cached_ft = cached_ft, None  # extracted by an older, lossy cleaner: fetch and extract again
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
                    _cache.cache_set(
                        "ft:" + pid,
                        {
                            "text": text[:120000],
                            "url": pdf_url,
                            "source": source,
                            "pdf": True,
                            "format": PDF_TEXT_FORMAT,
                        },
                    )
                    return {"id": pid, "title": p.get("title"), "available": True, "source": source, "url": pdf_url}
            else:
                # Possibly an HTML full-text page; landing/login/paywall pages are short once boilerplate is removed.
                html_text = _html_to_text(raw.decode("utf-8", errors="replace"))
                if len(html_text) >= MIN_HTML_FULLTEXT_CHARS:
                    _cache.cache_set(
                        "ft:" + pid,
                        {"text": html_text[:120000], "url": pdf_url, "source": source, "format": PDF_TEXT_FORMAT},
                    )
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
            xml_raw = _nhttp._http_get(xml_url, timeout=15, binary=True)
            cleaned = _jats_text(xml_raw)
            if len(cleaned) > 500:
                _cache.cache_set(
                    "ft:" + pid,
                    {"text": cleaned[:120000], "url": xml_url, "source": "europepmc_xml", "format": PDF_TEXT_FORMAT},
                )
                return {
                    "id": pid,
                    "title": p.get("title"),
                    "available": True,
                    "source": "europepmc_xml",
                    "url": xml_url,
                }
        except Exception:
            pass

    if stale and stale.get("text"):  # the new download failed: the older extraction is better than nothing
        return {
            "id": pid,
            "title": p.get("title"),
            "available": True,
            "source": stale.get("source", "cache"),
            "url": stale.get("url"),
        }
    return {
        "id": pid,
        "title": p.get("title"),
        "available": False,
        "pdf_url": pdf_url,
        "doi_url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "note": "Open-access full text not directly extractable; use abstract or download_papers.",
    }
