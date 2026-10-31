"""Open-artifact radar (#02): code repositories, model weights and datasets cited by a paper.

Extraction is regex-based and offline; the optional HTTP check goes through the core SSRF guard
(`noul.net.http._validate_public_url`, also re-applied on every redirect) with short timeouts."""

import concurrent.futures
import re
import urllib.error
import urllib.parse
import urllib.request

from noul import errors as _errors
from noul.net import http as _nhttp
from noul.schemas.paper import ArtifactStatus

VERIFY_TIMEOUT = 5
MAX_VERIFY_URLS = 20
_TRAILING = ".,;:!?)]}>'\"*"

_URL_RX = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|huggingface\.co|civitai\.com|kaggle\.com|"
    r"pytorch\.org|zenodo\.org|(?:dx\.)?doi\.org|(?:[\w-]+\.)?figshare\.com|osf\.io|dataverse\.harvard\.edu)"
    r"/[^\s<>\"'`]+",
    re.I,
)
_CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
_HF_NON_MODEL = {
    "datasets",
    "spaces",
    "docs",
    "papers",
    "blog",
    "models",
    "learn",
    "tasks",
    "collections",
    "organizations",
    "join",
    "login",
    "pricing",
    "settings",
    "chat",
    "posts",
    "api",
}
_GIT_NON_REPO = {
    "features",
    "about",
    "pricing",
    "orgs",
    "topics",
    "marketplace",
    "sponsors",
    "settings",
    "explore",
    "login",
    "join",
    "search",
    "collections",
    "trending",
    "site",
    "help",
    "users",
}

EVASIVE_RX = re.compile(
    r"available\s+(?:up)?on\s+(?:reasonable\s+)?request"
    r"|available\s+from\s+the\s+(?:corresponding\s+)?authors?"
    r"|(?:will\s+be|be)\s+(?:made\s+)?(?:publicly\s+)?(?:available|released)\s+(?:up)?on\s+(?:acceptance|publication)"
    r"|(?:will\s+be\s+)?shared\s+(?:up)?on\s+(?:reasonable\s+)?request"
    r"|(?:provided|obtained)\s+(?:by|from)\s+the\s+(?:corresponding\s+)?authors?\s+(?:up)?on\s+(?:reasonable\s+)?request"
    r"|disponíve(?:l|is)\s+mediante\s+solicitação",
    re.I,
)


def _clean(raw):
    url = raw.strip()
    while url and url[-1] in _TRAILING:
        # Keep balanced closing brackets that belong to the URL, e.g. wiki-style paths.
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        url = url[:-1]
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def classify_url(url):
    """Return (kind, canonical_url) with kind in {"code", "weights", "dataset"}, or (None, None)."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    segs = [s for s in parsed.path.split("/") if s]
    low = [s.lower() for s in segs]
    if host in _CODE_HOSTS:
        if len(segs) >= 2 and low[0] not in _GIT_NON_REPO:
            repo = re.sub(r"\.git$", "", segs[1], flags=re.I)
            if repo:
                return "code", f"https://{host}/{segs[0]}/{repo}"
        return None, None
    if host == "huggingface.co":
        if low[:1] == ["datasets"] and len(segs) >= 3:
            return "dataset", f"https://huggingface.co/datasets/{segs[1]}/{segs[2]}"
        if len(segs) >= 2 and low[0] not in _HF_NON_MODEL:
            return "weights", f"https://huggingface.co/{segs[0]}/{segs[1]}"
        return None, None
    if host == "civitai.com" and low[:1] == ["models"] and len(segs) >= 2:
        return "weights", url
    if host == "kaggle.com" and len(segs) >= 2:
        if low[0] == "models":
            return "weights", url
        if low[0] == "datasets":
            return "dataset", url
        return None, None
    if host == "pytorch.org" and low[:1] == ["hub"] and len(segs) >= 2:
        return "weights", url
    if host == "zenodo.org" and low[:1] in (["record"], ["records"]) and len(segs) >= 2:
        return "dataset", url
    if host in ("doi.org", "dx.doi.org") and "/".join(low).startswith("10.5281/zenodo"):
        return "dataset", f"https://doi.org/{'/'.join(segs)}"
    if host.endswith("figshare.com") and segs:
        return "dataset", url
    if host == "osf.io" and segs:
        return "dataset", url
    if host == "dataverse.harvard.edu" and segs:
        return "dataset", url
    return None, None


def extract_artifact_urls(text):
    """Find code/weights/dataset links in free text: [{"url", "kind"}] (deduplicated, in order)."""
    found, seen = [], set()
    for m in _URL_RX.finditer(text or ""):
        kind, url = classify_url(_clean(m.group(0)))
        if kind and url.lower() not in seen:
            seen.add(url.lower())
            found.append({"url": url, "kind": kind})
    return found


def has_evasive_statement(text):
    return bool(EVASIVE_RX.search(text or ""))


def _open(req, timeout):
    """Open through the core opener that re-validates every redirect target (SSRF guard)."""
    return _nhttp._PUBLIC_OPENER.open(req, timeout=timeout)


def _request_status(url, method, timeout):
    headers = {"User-Agent": _nhttp._user_agent(), "Accept": "*/*"}
    if method == "GET":
        headers["Range"] = "bytes=0-1024"
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with _open(req, timeout) as resp:
            if method == "GET":
                resp.read(1025)
            return int(getattr(resp, "status", None) or resp.getcode())
    except urllib.error.HTTPError as e:
        e.close()
        return int(e.code)


def verify_url(url, timeout=VERIFY_TIMEOUT):
    """HEAD (falling back to a ranged GET on 403/405) -> {"url", "verified", "status_code", "error"?}."""
    try:
        _nhttp._validate_public_url(url)
    except _errors.HTTPError as e:
        return {"url": url, "verified": False, "status_code": None, "error": f"blocked: {e.message}"}
    try:
        status = _request_status(url, "HEAD", timeout)
        if status in (403, 405, 501):
            status = _request_status(url, "GET", timeout)
    except _errors.HTTPError as e:  # blocked redirect target
        return {"url": url, "verified": False, "status_code": None, "error": f"blocked: {e.message}"}
    except Exception as e:
        return {"url": url, "verified": False, "status_code": None, "error": f"{type(e).__name__}: {e}"}
    return {"url": url, "verified": 200 <= status < 400, "status_code": status}


def verify_artifact_urls(urls, timeout=VERIFY_TIMEOUT, max_workers=8):
    """Verify URLs in parallel; returns results in the input order."""
    urls = list(urls or [])
    if not urls:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(urls))) as ex:
        return list(ex.map(lambda u: verify_url(u, timeout), urls))


def _pick(entries, kind, verify_http):
    """(has, url, verified) for one artifact kind. When verifying, URLs answering 4xx/5xx are dead;
    network errors leave the link as unverified but still present."""
    of_kind = [e for e in entries if e["kind"] == kind]
    if verify_http:
        alive = [e for e in of_kind if not (isinstance(e.get("status_code"), int) and e["status_code"] >= 400)]
    else:
        alive = of_kind
    verified = [e for e in alive if e.get("verified")]
    best = (verified or alive or [None])[0]
    return bool(alive), (best or {}).get("url"), bool(verified)


def scan_artifacts_from_paper(paper, fulltext="", verify_http=True, timeout=VERIFY_TIMEOUT, max_urls=MAX_VERIFY_URLS):
    """Detect artifacts in a paper's abstract (+ optional full text), optionally verify them over HTTP,
    inject the result fields into `paper` (when it is a dict) and return them."""
    p = paper if isinstance(paper, dict) else (paper.model_dump(mode="json") if hasattr(paper, "model_dump") else {})
    text = "\n".join(x for x in (p.get("title"), p.get("abstract"), fulltext) if x)
    entries = extract_artifact_urls(text)[: max(1, int(max_urls))]
    for e in entries:
        e.update({"verified": False, "status_code": None})
    if verify_http and entries:
        for e, res in zip(entries, verify_artifact_urls([e["url"] for e in entries], timeout=timeout), strict=False):
            e.update({k: v for k, v in res.items() if k != "url"})

    has_code, code_url, code_ok = _pick(entries, "code", verify_http)
    has_weights, weights_url, weights_ok = _pick(entries, "weights", verify_http)
    has_dataset, dataset_url, dataset_ok = _pick(entries, "dataset", verify_http)
    evasive = has_evasive_statement(text)
    status = dict(
        has_code=has_code,
        code_url=code_url,
        code_verified=code_ok,
        has_weights=has_weights,
        weights_url=weights_url,
        weights_verified=weights_ok,
        has_dataset=has_dataset,
        dataset_url=dataset_url,
        dataset_verified=dataset_ok,
        urls=[e["url"] for e in entries],
        evasive_availability_statement=evasive,
    )
    try:
        artifacts = ArtifactStatus(**status).model_dump(mode="json")
        for key in ("code_url", "weights_url", "dataset_url"):
            artifacts[key] = status[key]  # keep the canonical string (pydantic may normalise it)
    except Exception:
        artifacts = dict(status)
    fields = {
        "has_code": has_code,
        "has_weights": has_weights,
        "has_dataset": has_dataset,
        "code_url": code_url,
        "weights_url": weights_url,
        "dataset_url": dataset_url,
        "artifact_urls": entries,
        "evasive_availability_statement": evasive,
        "artifacts": artifacts,
    }
    if isinstance(paper, dict):
        paper.update(fields)
    return fields
