"""Autonomous scientific literature core for noul-search-agent.
Supports OpenAlex, PubMed, Semantic Scholar, Crossref, arXiv, EuropePMC and Unpaywall
across all scientific disciplines (Medicine, CS/AI, Economics, Psychology, Engineering, Natural & Social Sciences).
Zero external dependencies (stdlib only; optional `pypdf` for PDF text extraction)."""
import concurrent.futures
import csv
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

__version__ = "1.1.0"

EV_W = {
    "meta-analysis": 1.0,
    "systematic-review": 0.9,
    "rct": 0.8,
    "review": 0.55,
    "cohort": 0.5,
    "empirical": 0.45,
    "preclinical": 0.2,
    "other": 0.3,
}

SOURCES = ("openalex", "pubmed", "s2", "crossref", "arxiv", "europepmc")
DEFAULT_SOURCES = ("openalex", "pubmed", "s2", "crossref")

UA = "noul-search-agent/" + __version__ + " (+https://github.com/ernanedomingues/noul-search-agent)"

SEARCH_TIMEOUT = 18
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MIN_HTML_FULLTEXT_CHARS = 2500

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Strict DOI match (prefix 10.NNNN/ required) so arXiv IDs like 2410.12345 are never treated as DOIs.
# The suffix accepts any non-space char because legacy DOIs contain `<`, `>`, `#`, etc.
DOI_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)
ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


class HTTPError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _cache_dir() -> Path:
    d = Path(os.environ.get("NOUL_HOME") or Path.home() / ".cache" / "noul-search-agent")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path():
    p = os.environ.get("NOUL_DB_PATH") or os.environ.get("DB_PATH") or str(_cache_dir() / "cache.db")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    return p


def _config_file() -> Path:
    return _cache_dir() / "config.json"


def _load_config_file() -> dict:
    cfg_file = _config_file()
    if not cfg_file.exists():
        return {}
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _valid_email(value):
    v = (value or "").strip()
    if not v or not EMAIL_RE.match(v) or v.lower().endswith(".local"):
        return None
    return v


def _resolve_email(cfg=None):
    """Return (email, source). Never fabricates a placeholder address."""
    cfg = _load_config_file() if cfg is None else cfg
    email = _valid_email(cfg.get("email"))
    if email:
        return email, "config"
    email = _valid_email(os.environ.get("NOUL_EMAIL") or os.environ.get("UNPAYWALL_EMAIL"))
    if email:
        return email, "env"
    return None, "none"


def get_config() -> dict:
    cfg = _load_config_file()
    email, source = _resolve_email(cfg)
    workspace = cfg.get("workspace") or os.environ.get("NOUL_WORKSPACE") or os.getcwd()
    return {
        "email": email,
        "email_source": source,
        "unpaywall_enabled": bool(email),
        "workspace": str(Path(workspace).expanduser().resolve()),
        "download_dir": cfg.get("download_dir") or "./papers",
        "research_dir": cfg.get("research_dir") or "./research",
        "config_file": str(_config_file()),
    }


def set_config(email=None, workspace=None, download_dir=None, research_dir=None, clear=False) -> dict:
    cfg_file = _config_file()
    if clear:
        if cfg_file.exists():
            cfg_file.unlink()
        return get_config()
    cfg = _load_config_file()
    if email is not None:
        cleaned = re.sub(r"^email:\s*", "", str(email).strip(), flags=re.I).strip()
        if cleaned and not EMAIL_RE.match(cleaned):
            raise HTTPError(400, f"Invalid email address: {cleaned}")
        if cleaned:
            cfg["email"] = cleaned
        else:
            cfg.pop("email", None)
    if workspace is not None:
        ws = str(workspace).strip()
        if ws:
            ws_path = Path(ws).expanduser().resolve()
            if not ws_path.is_dir():
                raise HTTPError(400, f"Workspace directory does not exist: {ws_path}")
            cfg["workspace"] = str(ws_path)
        else:
            cfg.pop("workspace", None)
    for key, value in (("download_dir", download_dir), ("research_dir", research_dir)):
        if value is not None:
            if str(value).strip():
                cfg[key] = str(value).strip()
            else:
                cfg.pop(key, None)
    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(cfg_file, 0o600)
    except OSError:
        pass
    return get_config()


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path(), timeout=15)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def cache_get(key, max_age=86400 * 14):
    try:
        with _conn() as c:
            row = c.execute("SELECT v, ts FROM kv WHERE k = ?", (key,)).fetchone()
            if not row:
                return None
            val, ts = row
            if max_age and (time.time() - ts) > max_age:
                return None
            return json.loads(val)
    except Exception:
        return None


def cache_set(key, val):
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
                      (key, json.dumps(val, ensure_ascii=False), int(time.time())))
    except Exception:
        pass


def _email():
    return _resolve_email()[0]


def _user_agent():
    email = _email()
    return UA[:-1] + f"; mailto:{email})" if email else UA


def _with_mailto(params):
    email = _email()
    if email:
        params["mailto"] = email
    return params


def _mailto_qs():
    email = _email()
    return "?" + urllib.parse.urlencode({"mailto": email}) if email else ""


def _ip_is_public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    )


def _validate_public_url(url):
    """Reject non-http(s) URLs and hosts resolving to private/loopback/link-local/reserved IPs (SSRF guard)."""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise HTTPError(400, f"Blocked URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise HTTPError(400, "Blocked URL without host")
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            raise HTTPError(400, f"Cannot resolve host {host}: {e}")
        addrs = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
    if not addrs:
        raise HTTPError(400, f"Cannot resolve host {host}")
    for ip in addrs:
        if not _ip_is_public(ip):
            raise HTTPError(400, f"Blocked non-public address {ip} for host {host}")
    return url


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_OPENER = urllib.request.build_opener(_PublicRedirectHandler)


def _http_get(url, headers=None, timeout=15, binary=False, max_bytes=MAX_DOWNLOAD_BYTES, public_only=False):
    """GET with a byte cap. `public_only=True` applies the SSRF guard to the URL and every redirect
    (use it for URLs that come from third-party metadata, e.g. PDF/full-text links)."""
    hdrs = {"User-Agent": _user_agent(), "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    if public_only:
        _validate_public_url(url)
        opener = _PUBLIC_OPENER.open
    else:
        opener = urllib.request.urlopen
    req = urllib.request.Request(url, headers=hdrs)
    with opener(req, timeout=timeout) as resp:
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise HTTPError(413, f"Response too large ({length} bytes > {max_bytes})")
        buf = bytearray()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise HTTPError(413, f"Response exceeded {max_bytes} bytes")
        data = bytes(buf)
        return data if binary else data.decode("utf-8", errors="replace")


def _http_json(url, headers=None, timeout=15):
    txt = _http_get(url, headers=headers, timeout=timeout, binary=False)
    return json.loads(txt)


def _clean_text(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s)))
    return re.sub(r"\s+", " ", s).strip()


def _norm_doi(doi):
    if not doi:
        return None
    m = DOI_RE.match(str(doi).strip())
    return m.group(1).lower() if m else None


def _paper_id(doi=None, openalex_id=None, pmid=None, s2_id=None, arxiv_id=None, title=""):
    if doi:
        d = _norm_doi(doi)
        if d:
            return "doi:" + re.sub(r"[^a-z0-9._-]", "_", d)
    if openalex_id:
        oa = str(openalex_id).rstrip("/").split("/")[-1]
        return f"oa:{oa}"
    if pmid:
        return f"pmid:{pmid}"
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    if s2_id:
        return f"s2:{s2_id}"
    h = hashlib.sha1(_clean_text(title).lower().encode("utf-8")).hexdigest()[:12]
    return f"id:{h}"


NEG_PATTERNS = re.compile(
    r"\b(?:"
    r"(?:non|not|un)[-\s]?randomi[sz]ed(?:\s+(?:controlled\s+)?(?:clinical\s+)?(?:trials?|stud(?:y|ies)))?|"
    r"(?:future|further|more|larger|additional)\s+(?:well[-\s]designed\s+|large[-\s]scale\s+|high[-\s]quality\s+)?"
    r"(?:meta[-\s]?analys\w+|systematic\s+reviews?|rcts?|randomi[sz]ed\s+(?:controlled\s+)?(?:clinical\s+)?trials?)|"
    r"need\s+for\s+(?:further\s+|more\s+)?(?:meta[-\s]?analys\w+|systematic\s+reviews?|rcts?|"
    r"randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"no\s+(?:meta[-\s]?analys\w+|rcts?|randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"lack\s+of\s+(?:meta[-\s]?analys\w+|rcts?|randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"protocol\s+for\s+a\s+(?:systematic\s+review|meta[-\s]?analysis|randomi[sz]ed(?:\s+controlled)?\s+trial)"
    r")\b",
    re.I,
)

_META_RE = re.compile(r"\bmeta[-\s]?analys(is|es)\b")
_SR_RE = re.compile(r"\bsystematic\s+review\b|\bprisma\b|\bcochrane\s+review\b|\bscoping\s+review\b")
_RCT_RE = re.compile(
    r"\brandomi[sz]ed\s+controlled\s+trial\b|\brandomi[sz]ed\s+trial\b|\brct\b|"
    r"\bdouble[-\s]?blind\b|\bplacebo[-\s]?controlled\b|\bclinical\s+trial\b"
)
_PRECLINICAL_RE = re.compile(
    r"\bin\s+vitro\b|\bin\s+vivo\b|\bmice\b|\bmurine\b|\brat\s+model\b|\banimal\s+model\b|"
    r"\bcell\s+line\b|\bwistar\b|\bc57bl\b|\bzebrafish\b"
)
_OBSERVATIONAL_RE = re.compile(
    r"\bcohort\b|\blongitudinal\b|\bprospective\s+study\b|\bretrospective\s+study\b|"
    r"\bcase[-\s]?control\b|\bcross[-\s]?sectional\b|\bepidemiolog|\bpanel\s+data\b|"
    r"\bdifference[-\s]?in[-\s]?differences\b|\bquasi[-\s]?experiment|\b(?:non|not|un)[-\s]?randomi[sz]ed\b"
)
_REVIEW_RE = re.compile(r"\breview\b|\bsurvey\s+of\b|\boverview\s+of\b|\bstate[-\s]of[-\s]the[-\s]art\b")
_EMPIRICAL_RE = re.compile(
    r"\bbenchmark\b|\bablation\b|\bempirical\b|\bexperiment(al|s)?\b|\bdataset\b|"
    r"\bevaluation\s+of\b|\bcomparative\s+study\b"
)
_STRONG_DESIGNS = (("meta-analysis", _META_RE), ("systematic-review", _SR_RE), ("rct", _RCT_RE))


def classify_evidence(title="", abstract="", pub_types=None):
    """Classify study design across biomedical and general scientific disciplines.
    Order: structured publication types > title > title+abstract; negated mentions are ignored."""
    pts_norm = [re.sub(r"[^a-z0-9]", "", str(x).lower()) for x in (pub_types or []) if x]
    if any(pt == "metaanalysis" for pt in pts_norm):
        return "meta-analysis"
    if any(pt == "systematicreview" for pt in pts_norm):
        return "systematic-review"
    if any(pt.startswith(("randomizedcontrolledtrial", "randomisedcontrolledtrial")) for pt in pts_norm):
        return "rct"

    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()
    pts_text = " ".join(str(x).lower() for x in (pub_types or []) if x)
    title_clean = NEG_PATTERNS.sub(" ", title_l)

    for label, rx in _STRONG_DESIGNS:
        if rx.search(title_clean):
            return label
    # A clearly observational/preclinical title wins over secondary mentions in the abstract.
    if _PRECLINICAL_RE.search(title_l):
        return "preclinical"
    if _OBSERVATIONAL_RE.search(title_l):
        return "cohort"

    raw = f"{title_l} {abstract_l} {pts_text}"
    clean = NEG_PATTERNS.sub(" ", raw)
    for label, rx in _STRONG_DESIGNS:
        if rx.search(clean):
            return label
    if _PRECLINICAL_RE.search(raw):
        return "preclinical"
    if _OBSERVATIONAL_RE.search(raw):
        return "cohort"
    if _REVIEW_RE.search(clean):
        return "review"
    if _EMPIRICAL_RE.search(raw):
        return "empirical"
    return "other"


def _norm_venue(venue):
    v = re.sub(r"\([^)]*\)", " ", (venue or "").lower()).replace("&", " and ")
    v = re.sub(r"[^a-z0-9]+", " ", v).strip()
    v = re.sub(r"^(?:19|20)\d\d\s+", "", v)
    return re.sub(r"^the\s+", "", v)


# Full normalized venue names, compared by equality (never by substring: "science" must not
# match "Journal of Food Science", nor "cell" match "Stem Cells International").
EXACT_ELITE_VENUES = frozenset(_norm_venue(v) for v in (
    "Nature", "Science", "Cell", "The Lancet", "Lancet",
    "New England Journal of Medicine", "The New England Journal of Medicine", "NEJM",
    "JAMA", "Journal of the American Medical Association", "BMJ", "British Medical Journal",
    "Annals of Internal Medicine", "Cochrane Database of Systematic Reviews", "Circulation",
    "European Heart Journal", "PNAS", "Proceedings of the National Academy of Sciences",
    "Proceedings of the National Academy of Sciences of the United States of America",
    "NeurIPS", "Neural Information Processing Systems", "Advances in Neural Information Processing Systems",
    "ICML", "International Conference on Machine Learning",
    "ICLR", "International Conference on Learning Representations",
    "ACL", "Annual Meeting of the Association for Computational Linguistics",
    "CVPR", "Computer Vision and Pattern Recognition",
    "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
    "ACM Computing Surveys", "American Economic Review", "Quarterly Journal of Economics",
    "Econometrica", "Journal of Finance",
))


def _estimate_impact(citations=0, year=None, venue=""):
    """Return (impact, tier). `tier` is a transparent citation/venue band (top/high/standard/emerging),
    not a JCR/SJR quartile."""
    cites = int(citations or 0)
    age = max(1, (time.gmtime().tm_year - int(year))) if year else 5
    cites_per_year = cites / age
    is_elite = _norm_venue(venue) in EXACT_ELITE_VENUES
    if is_elite or cites_per_year >= 25 or cites >= 200:
        return "high", "top"
    if cites_per_year >= 5 or cites >= 35:
        return "medium", "high"
    if cites >= 5:
        return "modest", "standard"
    return "emerging", "emerging"


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
    return _clean_text(" ".join(w for _, w in words))


def _parse_openalex_work(w):
    if not w or not isinstance(w, dict):
        return None
    title = _clean_text(w.get("title") or w.get("display_name") or "")
    if not title:
        return None
    doi = _norm_doi(w.get("doi"))
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
    ev = classify_evidence(title, abstract, pub_types)
    impact, tier = _estimate_impact(citations, year, journal)
    pid = _paper_id(doi=doi, openalex_id=oa_id, pmid=pmid, title=title)
    return {
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
        "sources": ["openalex"],
    }


def _search_openalex(query, limit=25, year_min=None, open_access=False):
    params = _with_mailto({"search": query, "per-page": min(limit, 50)})
    filters = []
    if year_min:
        filters.append(f"from_publication_date:{int(year_min)}-01-01")
    if open_access:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    out = []
    for w in data.get("results") or []:
        p = _parse_openalex_work(w)
        if p:
            out.append(p)
    return out


def _search_s2(query, limit=20, year_min=None, open_access=False):
    fields = "paperId,externalIds,title,abstract,year,venue,authors,citationCount,isOpenAccess,openAccessPdf,publicationTypes"
    params = {"query": query, "limit": min(limit, 40), "fields": fields}
    if year_min:
        params["year"] = f"{int(year_min)}-"
    if open_access:
        params["openAccessPdf"] = ""
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    out = []
    for item in data.get("data") or []:
        p = _parse_s2_paper(item)
        if p:
            out.append(p)
    return out


def _parse_s2_paper(item):
    if not item or not isinstance(item, dict):
        return None
    title = _clean_text(item.get("title") or "")
    if not title:
        return None
    ext = item.get("externalIds") or {}
    doi = _norm_doi(ext.get("DOI"))
    pmid = str(ext.get("PubMed") or "") or None
    arxiv_id = ext.get("ArXiv")
    s2_id = item.get("paperId")
    abstract = _clean_text(item.get("abstract") or "")
    year = item.get("year")
    journal = item.get("venue") or ("arXiv" if arxiv_id else "")
    authors = [a.get("name") for a in (item.get("authors") or [])[:10] if a.get("name")]
    citations = int(item.get("citationCount") or 0)
    oa_pdf = (item.get("openAccessPdf") or {}).get("url")
    if not oa_pdf and arxiv_id:
        oa_pdf = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    is_oa = bool(item.get("isOpenAccess") or oa_pdf)
    ev = classify_evidence(title, abstract, item.get("publicationTypes") or [])
    impact, tier = _estimate_impact(citations, year, journal)
    pid = _paper_id(doi=doi, pmid=pmid, s2_id=s2_id, arxiv_id=arxiv_id, title=title)
    return {
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
        "sources": ["s2"],
    }


def _eutils_params(params):
    params["tool"] = "noul-search-agent"
    email = _email()
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
    sdata = _http_json(esearch)
    id_list = (sdata.get("esearchresult") or {}).get("idlist") or []
    if not id_list:
        return []
    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        _eutils_params({"db": "pubmed", "id": ",".join(id_list), "retmode": "xml"})
    )
    xml_txt = _http_get(efetch)
    return _parse_pubmed_xml(xml_txt)


def _parse_pubmed_xml(xml_txt):
    out = []
    root = ET.fromstring(xml_txt)
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//MedlineCitation/PMID")
        title = _clean_text("".join(art.find(".//ArticleTitle").itertext())) if art.find(".//ArticleTitle") is not None else ""
        if not title:
            continue
        abs_parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            lbl = ab.attrib.get("Label")
            txt = _clean_text("".join(ab.itertext()))
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
        journal = _clean_text(art.findtext(".//Journal/Title") or "")
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
                doi = _norm_doi(aid.text)
            elif id_type == "pmc" and aid.text:
                pmc_id = aid.text.strip()
        pub_types = [pt.text for pt in art.findall(".//PublicationTypeList/PublicationType") if pt.text]
        ev = classify_evidence(title, abstract, pub_types)
        impact, tier = _estimate_impact(0, year, journal)
        pdf_url = _europepmc_pdf_url(pmc_id) if pmc_id else None
        pid = _paper_id(doi=doi, pmid=pmid, title=title)
        out.append({
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
            "sources": ["pubmed"],
        })
    return out


def _search_crossref(query, limit=15, year_min=None):
    params = _with_mailto({"query": query, "rows": min(limit, 25)})
    if year_min:
        params["filter"] = f"from-pub-date:{int(year_min)}"
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    items = (data.get("message") or {}).get("items") or []
    out = []
    for it in items:
        titles = it.get("title") or []
        title = _clean_text(titles[0] if titles else "")
        if not title:
            continue
        doi = _norm_doi(it.get("DOI"))
        abstract = _clean_text(it.get("abstract") or "")
        yr = None
        for k in ("published-print", "published-online", "issued", "created"):
            parts = ((it.get(k) or {}).get("date-parts") or [[]])[0]
            if parts and isinstance(parts[0], int):
                yr = parts[0]
                break
        containers = it.get("container-title") or []
        journal = _clean_text(containers[0] if containers else "")
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
        ev = classify_evidence(title, abstract, [it.get("type")])
        impact, tier = _estimate_impact(citations, yr, journal)
        pid = _paper_id(doi=doi, title=title)
        out.append({
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
            "sources": ["crossref"],
        })
    return out


def _search_arxiv(query, limit=15, year_min=None):
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(limit, 25),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    xml_txt = _http_get(url)
    root = ET.fromstring(xml_txt)
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        title = _clean_text(entry.findtext("a:title", "", ns))
        if not title:
            continue
        abstract = _clean_text(entry.findtext("a:summary", "", ns))
        pub = entry.findtext("a:published", "", ns)
        year = int(pub[:4]) if len(pub) >= 4 and pub[:4].isdigit() else None
        if year_min and year and year < int(year_min):
            continue
        id_url = entry.findtext("a:id", "", ns)
        arxiv_id = id_url.rstrip("/").split("/abs/")[-1] if "/abs/" in id_url else id_url.rstrip("/").split("/")[-1]
        doi = _norm_doi(entry.findtext("arxiv:doi", "", ns))
        authors = [
            _clean_text(a.findtext("a:name", "", ns))
            for a in entry.findall("a:author", ns)[:10]
            if a.findtext("a:name", "", ns)
        ]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None
        ev = classify_evidence(title, abstract, ["preprint"])
        impact, tier = _estimate_impact(0, year, "arXiv")
        pid = _paper_id(doi=doi, arxiv_id=arxiv_id, title=title)
        out.append({
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
            "sources": ["arxiv"],
        })
    return out


def _europepmc_pdf_url(pmc_id):
    return "https://europepmc.org/backend/ptpmcrender.fcgi?" + urllib.parse.urlencode(
        {"accid": str(pmc_id).strip(), "blobtype": "pdf"}
    )


def _parse_europepmc_result(it):
    if not it or not isinstance(it, dict):
        return None
    title = _clean_text(it.get("title") or "")
    if not title:
        return None
    doi = _norm_doi(it.get("doi"))
    pmid = str(it.get("pmid") or "").strip() or None
    pmc_id = str(it.get("pmcid") or "").strip() or None
    abstract = _clean_text(it.get("abstractText") or "")
    yr_txt = str(it.get("pubYear") or "")
    year = int(yr_txt) if yr_txt.isdigit() else None
    journal = _clean_text(
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
    for ft in ((it.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
        if (ft.get("documentStyle") == "pdf" and ft.get("availabilityCode") in ("OA", "F")
                and ft.get("url")):
            pdf_url = ft["url"]
            break
    if not pdf_url and pmc_id and is_oa:
        pdf_url = _europepmc_pdf_url(pmc_id)
    pub_types = list((it.get("pubTypeList") or {}).get("pubType") or [])
    if it.get("source") == "PPR":
        pub_types.append("preprint")
    ev = classify_evidence(title, abstract, pub_types)
    impact, tier = _estimate_impact(citations, year, journal)
    pid = _paper_id(doi=doi, pmid=pmid, title=title)
    return {
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
        "sources": ["europepmc"],
    }


def _search_europepmc(query, limit=20, year_min=None, open_access=False):
    q = f"({query})"
    if year_min:
        q += f" AND PUB_YEAR:[{int(year_min)} TO 3000]"
    if open_access:
        q += " AND OPEN_ACCESS:y"
    params = {"query": q, "resultType": "core", "format": "json", "pageSize": min(limit, 50)}
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    out = []
    for it in ((data.get("resultList") or {}).get("result")) or []:
        p = _parse_europepmc_result(it)
        if p:
            out.append(p)
    return out


def _merge_papers(lists):
    by_key = {}
    for p in lists:
        doi = _norm_doi(p.get("doi"))
        norm_title = re.sub(r"[^a-z0-9]+", "", (p.get("title") or "").lower())[:80]
        dedup_key = f"doi:{doi}" if doi else f"t:{norm_title}"
        if dedup_key not in by_key:
            by_key[dedup_key] = dict(p)
        else:
            cur = by_key[dedup_key]
            # Merge sources
            cur["sources"] = sorted(set((cur.get("sources") or []) + (p.get("sources") or [])))
            # Keep richer abstract
            if len(p.get("abstract") or "") > len(cur.get("abstract") or ""):
                cur["abstract"] = p["abstract"]
            # Keep max citations
            if int(p.get("citations") or 0) > int(cur.get("citations") or 0):
                cur["citations"] = int(p["citations"])
                cur["impact"], cur["tier"] = _estimate_impact(cur["citations"], cur.get("year"), cur.get("journal"))
            # Keep identifiers and open access URLs
            for field in ("doi", "pmid", "pmc_id", "openalex_id", "s2_id", "arxiv_id", "pdf_url", "journal", "year"):
                if not cur.get(field) and p.get(field):
                    cur[field] = p[field]
            if p.get("open_access"):
                cur["open_access"] = True
            if EV_W.get(p.get("evidence"), 0) > EV_W.get(cur.get("evidence"), 0):
                cur["evidence"] = p["evidence"]
    return list(by_key.values())


def _score_paper(p, query=""):
    ev_score = EV_W.get(p.get("evidence"), 0.3) * 40.0
    cites = min(int(p.get("citations") or 0), 500) / 500.0 * 25.0
    has_abs = 15.0 if len(p.get("abstract") or "") > 80 else 0.0
    q_terms = [w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())]
    text = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
    overlap = sum(1 for w in set(q_terms) if w in text) / max(1, len(set(q_terms))) * 20.0 if q_terms else 10.0
    return ev_score + cites + has_abs + overlap


def search(query, mode="quick", _unused=None, filters=None, lang="en", sources=None):
    filters = filters or {}
    chosen = [s for s in (sources or DEFAULT_SOURCES) if s in SOURCES]
    if not chosen:
        chosen = ["openalex", "pubmed", "s2"]
    year_min = filters.get("year_min")
    open_access = bool(filters.get("open_access"))

    cache_key = "search:" + hashlib.sha1(
        json.dumps({"q": query, "f": filters, "s": chosen}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = cache_get(cache_key, max_age=3600 * 12)
    if cached:
        for p in cached.get("papers") or []:
            cache_set("paper:" + p["id"], p)
        return cached

    warnings = []
    raw_papers = []
    active_sources = []

    def _run_source(src):
        if src == "openalex":
            return _search_openalex(query, limit=25, year_min=year_min, open_access=open_access)
        if src == "pubmed":
            return _search_pubmed(query, limit=20, year_min=year_min)
        if src == "s2":
            return _search_s2(query, limit=20, year_min=year_min, open_access=open_access)
        if src == "crossref":
            return _search_crossref(query, limit=15, year_min=year_min)
        if src == "arxiv":
            return _search_arxiv(query, limit=15, year_min=year_min)
        if src == "europepmc":
            return _search_europepmc(query, limit=20, year_min=year_min, open_access=open_access)
        return []

    # No `with` block: its implicit shutdown(wait=True) would block on the slowest source.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen))
    futs = {ex.submit(_run_source, s): s for s in chosen}
    done, not_done = concurrent.futures.wait(futs.keys(), timeout=SEARCH_TIMEOUT)
    for fut in futs:
        src = futs[fut]
        if fut in done:
            try:
                raw_papers.extend(fut.result())
                active_sources.append(src)
            except Exception as e:
                warnings.append(f"{src}: {type(e).__name__}: {e}")
        else:
            fut.cancel()
            warnings.append(f"{src}: TimeoutError: source exceeded {SEARCH_TIMEOUT}s limit")
    ex.shutdown(wait=False, cancel_futures=True)

    merged = _merge_papers(raw_papers)

    # Apply post-filters
    if year_min:
        merged = [p for p in merged if (p.get("year") or 0) >= int(year_min)]
    if filters.get("min_citations"):
        mc = int(filters["min_citations"])
        merged = [p for p in merged if int(p.get("citations") or 0) >= mc]
    if open_access:
        merged = [p for p in merged if p.get("open_access")]
    if filters.get("study_types"):
        allowed = set(filters["study_types"])
        merged = [p for p in merged if p.get("evidence") in allowed]

    sort_by = filters.get("sort") or "relevance"
    if sort_by == "citations":
        merged.sort(key=lambda p: int(p.get("citations") or 0), reverse=True)
    elif sort_by == "date":
        merged.sort(key=lambda p: int(p.get("year") or 0), reverse=True)
    else:
        merged.sort(key=lambda p: _score_paper(p, query), reverse=True)

    for p in merged:
        cache_set("paper:" + p["id"], p)

    out = {"papers": merged, "sources": active_sources, "warnings": warnings}
    if merged:
        cache_set(cache_key, out)
    return out


def resolve_paper(identifier):
    """Retrieve a paper from cache or fetch on-demand by ID, DOI, OpenAlex ID, PMID, arXiv ID, or S2 ID."""
    if not identifier:
        raise HTTPError(400, "Empty paper identifier")
    ident = str(identifier).strip()

    # 1. Try direct cache hit
    p = cache_get("paper:" + ident)
    if p:
        return p

    # 2. Try normalized DOI or prefixed identifier (arXiv first: "2410.12345" is not a DOI)
    is_arxiv = ident.lower().startswith("arxiv:") or bool(ARXIV_RE.match(ident))
    norm = None if is_arxiv else _norm_doi(ident)
    if norm:
        cand_id = _paper_id(doi=norm)
        p = cache_get("paper:" + cand_id)
        if p:
            return p

    # 3. Fetch on demand from OpenAlex or Semantic Scholar
    fetched = None
    s2_fields = "paperId,externalIds,title,abstract,year,venue,authors,citationCount,isOpenAccess,openAccessPdf,publicationTypes"
    try:
        if is_arxiv:
            ax = ident.split(":", 1)[-1].strip()
            url = f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{urllib.parse.quote(ax, safe='/.')}?fields={s2_fields}"
            fetched = _parse_s2_paper(_http_json(url))
        elif norm:
            url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(norm, safe='')}{_mailto_qs()}"
            fetched = _parse_openalex_work(_http_json(url))
        elif ident.startswith("oa:") or re.match(r"^W\d+$", ident, flags=re.I):
            oa_id = ident.split(":", 1)[-1].upper()
            url = f"https://api.openalex.org/works/{urllib.parse.quote(oa_id, safe='')}{_mailto_qs()}"
            fetched = _parse_openalex_work(_http_json(url))
        elif ident.startswith("pmid:") or ident.isdigit():
            pmid = ident.split(":", 1)[-1]
            url = f"https://api.openalex.org/works/pmid:{urllib.parse.quote(pmid, safe='')}{_mailto_qs()}"
            fetched = _parse_openalex_work(_http_json(url))
        elif ident.startswith("s2:") or re.match(r"^[0-9a-f]{40}$", ident, flags=re.I):
            s2_id = ident.split(":", 1)[-1]
            url = f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(s2_id, safe='')}?fields={s2_fields}"
            fetched = _parse_s2_paper(_http_json(url))
    except Exception:
        fetched = None

    if fetched:
        cache_set("paper:" + fetched["id"], fetched)
        if fetched["id"] != ident:
            cache_set("paper:" + ident, fetched)
        return fetched

    raise HTTPError(404, f"Paper '{ident}' not found in cache or public APIs; run search_papers or pass a valid DOI/ID.")


def resolve_pdf_url(paper):
    """Attempt to resolve a direct Open Access PDF URL for a paper dict via multiple sources."""
    if paper.get("pdf_url"):
        return paper["pdf_url"], "metadata"
    if paper.get("pmc_id"):
        return _europepmc_pdf_url(paper["pmc_id"]), "europepmc"
    if paper.get("arxiv_id"):
        return f"https://arxiv.org/pdf/{paper['arxiv_id']}.pdf", "arxiv"

    doi = _norm_doi(paper.get("doi"))
    if doi:
        # 1. Try OpenAlex by DOI
        try:
            url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi, safe='')}{_mailto_qs()}"
            w = _http_json(url, timeout=10)
            best_oa = w.get("best_oa_location") or {}
            oa_info = w.get("open_access") or {}
            pdf = best_oa.get("pdf_url") or oa_info.get("oa_url")
            if pdf:
                return pdf, "openalex"
        except Exception:
            pass

        # 2. Try Unpaywall if email is configured
        email = _email()
        if email:
            try:
                uw_url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}?email={urllib.parse.quote(email)}"
                uw = _http_json(uw_url, timeout=10)
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
            ed = _http_json(epmc_url, timeout=10)
            res_list = ((ed.get("resultList") or {}).get("result")) or []
            if res_list:
                pmcid = res_list[0].get("pmcid")
                if pmcid:
                    return _europepmc_pdf_url(pmcid), "europepmc"
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
        return _clean_text("\n\n".join(pages))
    except Exception:
        return ""


_HTML_BOILERPLATE_RE = re.compile(
    r"<(script|style|nav|header|footer|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S
)


def _html_to_text(raw_html):
    return _clean_text(_HTML_BOILERPLATE_RE.sub(" ", raw_html or ""))


def fulltext(id):
    p = resolve_paper(id)
    pid = p["id"]
    cached_ft = cache_get("ft:" + pid)
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
            raw = _http_get(pdf_url, timeout=20, binary=True, public_only=True)
            if raw.startswith(b"%PDF"):
                text = _extract_pdf_text(raw)
                if text and len(text) > 200:
                    cache_set("ft:" + pid, {"text": text, "url": pdf_url, "source": source})
                    return {"id": pid, "title": p.get("title"), "available": True, "source": source, "url": pdf_url}
            else:
                # Possibly an HTML full-text page; landing/login/paywall pages are short once boilerplate is removed.
                html_text = _html_to_text(raw.decode("utf-8", errors="replace"))
                if len(html_text) >= MIN_HTML_FULLTEXT_CHARS:
                    cache_set("ft:" + pid, {"text": html_text[:120000], "url": pdf_url, "source": source})
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
            xml_txt = _http_get(xml_url, timeout=15)
            cleaned = _clean_text(xml_txt)
            if len(cleaned) > 500:
                cache_set("ft:" + pid, {"text": cleaned[:120000], "url": xml_url, "source": "europepmc_xml"})
                return {"id": pid, "title": p.get("title"), "available": True, "source": "europepmc_xml", "url": xml_url}
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


def _ensure_openalex_work(p):
    """Fetch full OpenAlex record for a paper dict to obtain referenced_works / related_works / openalex_id."""
    if p.get("openalex_id") and (p.get("referenced_works") is not None):
        return p
    oa_id = p.get("openalex_id")
    doi = _norm_doi(p.get("doi"))
    pmid = p.get("pmid")
    url = None
    if oa_id:
        url = f"https://api.openalex.org/works/{urllib.parse.quote(oa_id, safe='')}{_mailto_qs()}"
    elif doi:
        url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi, safe='')}{_mailto_qs()}"
    elif pmid:
        url = f"https://api.openalex.org/works/pmid:{urllib.parse.quote(str(pmid), safe='')}{_mailto_qs()}"
    if url:
        try:
            w = _http_json(url, timeout=12)
            parsed = _parse_openalex_work(w)
            if parsed:
                p.update({k: v for k, v in parsed.items() if v and not p.get(k)})
                p["referenced_works"] = parsed.get("referenced_works") or []
                p["related_works"] = parsed.get("related_works") or []
                cache_set("paper:" + p["id"], p)
        except Exception:
            pass
    return p


def _fetch_openalex_batch(oa_ids, limit=15):
    ids = [x.rstrip("/").split("/")[-1] for x in (oa_ids or []) if x][: min(limit, 25)]
    if not ids:
        return []
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        _with_mailto({"filter": "openalex_id:" + "|".join(ids), "per-page": len(ids)})
    )
    try:
        data = _http_json(url, timeout=15)
        out = []
        for w in data.get("results") or []:
            p = _parse_openalex_work(w)
            if p:
                cache_set("paper:" + p["id"], p)
                out.append(p)
        return out
    except Exception:
        return []


def citation_graph(id):
    p = _ensure_openalex_work(resolve_paper(id))
    refs = _fetch_openalex_batch(p.get("referenced_works") or [], limit=15)
    citing = []
    oa_id = p.get("openalex_id")
    if oa_id:
        try:
            url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
                _with_mailto({"filter": f"cites:{oa_id}", "sort": "cited_by_count:desc", "per-page": 15})
            )
            data = _http_json(url, timeout=15)
            for w in data.get("results") or []:
                cp = _parse_openalex_work(w)
                if cp:
                    cache_set("paper:" + cp["id"], cp)
                    citing.append(cp)
        except Exception:
            pass
    return {
        "id": p["id"],
        "title": p.get("title"),
        "references": refs,
        "citing": citing,
    }


def related_papers(id, relation="all", study_types=None, year_min=None, limit=15):
    """Find semantically similar papers, cited references, and citing papers.
    Caches all returned papers so they can be inspected, queried, or downloaded immediately."""
    p = _ensure_openalex_work(resolve_paper(id))
    relation = (relation or "all").lower()
    similar = []
    references = []
    citing = []

    if relation in ("similar", "all"):
        # 1. Try Semantic Scholar Recommendations API
        ident_for_s2 = None
        if p.get("s2_id"):
            ident_for_s2 = p["s2_id"]
        elif p.get("doi"):
            ident_for_s2 = f"DOI:{p['doi']}"
        elif p.get("pmid"):
            ident_for_s2 = f"PMID:{p['pmid']}"
        elif p.get("arxiv_id"):
            ident_for_s2 = f"ARXIV:{p['arxiv_id']}"
        if ident_for_s2:
            try:
                fields = "paperId,externalIds,title,abstract,year,venue,authors,citationCount,isOpenAccess,openAccessPdf,publicationTypes"
                url = (
                    f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"
                    f"{urllib.parse.quote(ident_for_s2, safe=':')}?fields={fields}&limit={min(limit, 20)}"
                )
                data = _http_json(url, timeout=12)
                for item in data.get("recommendedPapers") or []:
                    sp = _parse_s2_paper(item)
                    if sp:
                        cache_set("paper:" + sp["id"], sp)
                        similar.append(sp)
            except Exception:
                pass

        # 2. Supplement with OpenAlex related_works
        if len(similar) < limit and p.get("related_works"):
            oa_rel = _fetch_openalex_batch(p.get("related_works"), limit=limit)
            similar = _merge_papers(similar + oa_rel)

    if relation in ("references", "citing", "all"):
        cg = citation_graph(p["id"])
        if relation in ("references", "all"):
            references = cg.get("references") or []
        if relation in ("citing", "all"):
            citing = cg.get("citing") or []

    def _filter_list(items):
        res = list(items)
        if year_min:
            res = [x for x in res if (x.get("year") or 0) >= int(year_min)]
        if study_types:
            allowed = set(study_types)
            res = [x for x in res if x.get("evidence") in allowed]
        res.sort(key=lambda x: _score_paper(x, p.get("title") or ""), reverse=True)
        for x in res:
            cache_set("paper:" + x["id"], x)
        return res[: max(1, min(int(limit), 40))]

    return {
        "anchor_paper": {"id": p["id"], "title": p.get("title"), "year": p.get("year"), "doi": p.get("doi")},
        "relation": relation,
        "similar": _filter_list(similar) if relation in ("similar", "all") else [],
        "references": _filter_list(references) if relation in ("references", "all") else [],
        "citing": _filter_list(citing) if relation in ("citing", "all") else [],
    }


def _safe_filename(p):
    year = p.get("year") or "nd"
    first_author = ((p.get("authors") or ["Unknown"])[0]).split()[-1]
    first_author = re.sub(r"[^A-Za-z0-9_-]", "", first_author) or "Author"
    title_short = re.sub(r"[^A-Za-z0-9]+", "_", (p.get("title") or "paper")).strip("_")[:55]
    short_id = re.sub(r"[^A-Za-z0-9._-]", "_", p.get("id") or "id")[:25]
    return f"{year}_{first_author}_{title_short}_{short_id}.pdf"


def download_paper_pdf(id, output_dir="./papers"):
    """Download Open Access PDF for a paper to `output_dir`."""
    p = resolve_paper(id)
    dest_dir = Path(output_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = (dest_dir / _safe_filename(p)).resolve()
    if not target.is_relative_to(dest_dir):
        raise HTTPError(403, f"PDF path '{target}' is outside the download directory")

    if target.exists() and target.stat().st_size > 1024:
        return {
            "id": p["id"],
            "title": p.get("title"),
            "status": "downloaded",
            "filepath": str(target),
            "source": "local_cache",
        }

    pdf_url, source = resolve_pdf_url(p)
    if pdf_url:
        try:
            raw = _http_get(pdf_url, timeout=25, binary=True, public_only=True)
            if raw.startswith(b"%PDF"):
                target.write_bytes(raw)
                # Also populate fulltext cache if pypdf is installed
                txt = _extract_pdf_text(raw)
                if txt and len(txt) > 200:
                    cache_set("ft:" + p["id"], {"text": txt, "url": pdf_url, "source": source})
                return {
                    "id": p["id"],
                    "title": p.get("title"),
                    "status": "downloaded",
                    "filepath": str(target),
                    "source": source,
                    "pdf_url": pdf_url,
                }
        except Exception as e:
            err_msg = str(e)
        else:
            err_msg = "URL did not return a direct PDF binary"
    else:
        err_msg = "No Open Access PDF URL found"

    return {
        "id": p["id"],
        "title": p.get("title"),
        "status": "paywalled_or_unavailable",
        "reason": err_msg,
        "doi_url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "pdf_url": pdf_url,
    }


def export(papers, format="bibtex"):
    fmt = (format or "bibtex").lower()
    if fmt == "bibtex":
        entries = []
        for idx, p in enumerate(papers, 1):
            first = ((p.get("authors") or ["anon"])[0]).split()[-1].lower()
            first = re.sub(r"[^a-z0-9]", "", first) or "ref"
            key = f"{first}{p.get('year') or 'nd'}_{idx}"
            authors_bib = " and ".join(p.get("authors") or [])
            lines = [
                f"@article{{{key},",
                f"  title = {{{p.get('title') or ''}}},",
                f"  author = {{{authors_bib}}},",
                f"  year = {{{p.get('year') or ''}}},",
                f"  journal = {{{p.get('journal') or ''}}},",
            ]
            if p.get("doi"):
                lines.append(f"  doi = {{{p['doi']}}},")
            lines.append("}")
            entries.append("\n".join(lines))
        return "\n\n".join(entries), "application/x-bibtex"

    if fmt == "ris":
        entries = []
        for p in papers:
            lines = ["TY  - JOUR", f"TI  - {p.get('title') or ''}"]
            for a in p.get("authors") or []:
                lines.append(f"AU  - {a}")
            if p.get("year"):
                lines.append(f"PY  - {p['year']}")
            if p.get("journal"):
                lines.append(f"JO  - {p['journal']}")
            if p.get("doi"):
                lines.append(f"DO  - {p['doi']}")
            lines.append("ER  - ")
            entries.append("\n".join(lines))
        return "\n\n".join(entries), "application/x-research-info-systems"

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access"])
        for p in papers:
            writer.writerow([
                p.get("id") or "",
                p.get("title") or "",
                "; ".join(p.get("authors") or []),
                p.get("year") or "",
                p.get("journal") or "",
                p.get("doi") or "",
                p.get("citations") or 0,
                p.get("evidence") or "",
                bool(p.get("open_access")),
            ])
        return buf.getvalue(), "text/csv"

    raise HTTPError(400, f"Unsupported export format: {format}")
