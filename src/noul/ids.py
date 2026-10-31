"""Identifier normalisation (DOI, arXiv, PMID, OpenAlex, S2) and small text helpers."""

import hashlib
import html
import re

# Strict DOI match (prefix 10.NNNN/ required) so arXiv IDs like 2410.12345 are never treated as DOIs.
# The suffix accepts any non-space char because legacy DOIs contain `<`, `>`, `#`, etc.
DOI_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)


ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


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


def _unique(items):
    out = []
    for x in items or []:
        if x and x not in out:
            out.append(x)
    return out
