"""Evidence integrity: retractions and preprint <-> published links.

Sources, cheapest first:
- the record itself: OpenAlex `is_retracted`, PubMed/Europe PMC publication type "Retracted Publication";
- Crossref `updated-by` (retraction / withdrawal / removal notices, Retraction Watch data included) and `relation`
  (`is-preprint-of`, `has-preprint`), fetched once per DOI and cached;
- for arXiv preprints, the published-venue resolver already used by `sanitize_bibtex`.

Everything here returns facts with their source; whether a retracted paper may still be cited (e.g. as a cautionary
example) is the client model's call.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from noul import ids as _ids
from noul.net import cache as _cache
from noul.net import http as _nhttp

# Crossref update types that withdraw the work, and the ones that only flag it.
WITHDRAWN = frozenset({"retraction", "withdrawal", "removal", "partial_retraction"})
CONCERN = frozenset({"expression_of_concern"})
RETRACTED_PUB_TYPES = frozenset({"retracted publication", "retracted-publication"})
ARXIV_DOI_PREFIX = "10.48550/arxiv."
_CACHE_TTL = 86400 * 7  # retractions are rare but not instant: re-check weekly


def _date(parts: Any) -> str | None:
    try:
        dp = (parts or {}).get("date-parts") or []
        return "-".join(f"{int(x):02d}" if i else str(int(x)) for i, x in enumerate(dp[0])) if dp and dp[0] else None
    except (TypeError, ValueError, IndexError):
        return None


def from_pub_types(pub_types: list[Any] | None, source: str) -> dict[str, Any] | None:
    """`retraction` record when a database indexes the work as a Retracted Publication."""
    if any(str(t or "").strip().lower() in RETRACTED_PUB_TYPES for t in pub_types or []):
        return {"is_retracted": True, "kind": "retraction", "source": source}
    return None


def from_openalex(work: dict[str, Any]) -> dict[str, Any] | None:
    return {"is_retracted": True, "kind": "retraction", "source": "openalex"} if work.get("is_retracted") else None


def parse_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """Integrity facts from a Crossref work record: `retraction`, `concern`, `versions` (all optional)."""
    out: dict[str, Any] = {}
    for upd in message.get("updated-by") or []:
        kind = str(upd.get("type") or "").strip().lower().replace(" ", "_")
        entry = {
            "kind": kind,
            "notice_doi": _ids._norm_doi(upd.get("DOI")),
            "date": _date(upd.get("updated")),
            "source": "crossref" + (f"/{upd['source']}" if upd.get("source") else ""),
        }
        if kind in WITHDRAWN and "retraction" not in out:
            out["retraction"] = {"is_retracted": True, **entry}
        elif kind in CONCERN and "concern" not in out:
            out["concern"] = entry
    rel = message.get("relation") or {}
    versions: dict[str, str] = {}
    for key, field in (("is-preprint-of", "published_as"), ("has-preprint", "preprint")):
        for item in rel.get(key) or []:
            doi = _ids._norm_doi(item.get("id")) if str(item.get("id-type") or "doi").lower() == "doi" else None
            if doi:
                versions[field] = _ids._paper_id(doi=doi)
                break
    if versions:
        out["versions"] = versions
    return out


def crossref_facts(doi: str) -> dict[str, Any]:
    """Crossref integrity facts for a DOI (cached; {} when Crossref has no record or is unreachable)."""
    norm = _ids._norm_doi(doi)
    if not norm or norm.lower().startswith(ARXIV_DOI_PREFIX):  # DataCite DOIs are not in Crossref
        return {}
    key = "integrity:crossref:" + norm.lower()
    cached = _cache.cache_get(key, max_age=_CACHE_TTL)
    if isinstance(cached, dict):
        return cached
    url = "https://api.crossref.org/works/" + urllib.parse.quote(norm, safe="") + _nhttp._mailto_qs()
    try:
        facts = parse_crossref((_nhttp._http_json(url) or {}).get("message") or {})
    except Exception as e:  # 404 (not a Crossref DOI) or network trouble: no facts, but never break the caller
        if getattr(e, "code", None) != 404:
            return {}
        facts = {}
    _cache.cache_set(key, facts)
    return facts


def _arxiv_published_as(paper: dict[str, Any]) -> str | None:
    from noul.providers import arxiv_client  # heavy module: imported only for arXiv preprints

    doi = _ids._norm_doi(paper.get("doi"))
    if doi and not doi.lower().startswith(ARXIV_DOI_PREFIX):
        return None  # already the published record
    entry = {"fields": {"title": paper.get("title") or "", "eprint": paper.get("arxiv_id") or ""}}
    try:
        found = arxiv_client.resolve_published_venue_for_preprint(entry)
    except Exception:
        return None
    pub_doi = _ids._norm_doi((found or {}).get("doi"))
    return _ids._paper_id(doi=pub_doi) if pub_doi and not pub_doi.lower().startswith(ARXIV_DOI_PREFIX) else None


def enrich(paper: dict[str, Any], network: bool = True) -> dict[str, Any]:
    """Return a copy of `paper` with `retraction`, `concern` and `versions` filled in from every available source.

    `network=False` keeps to what the record and the cache already know (for offline tools such as the meter)."""
    p = dict(paper)
    facts = crossref_facts(p.get("doi") or "") if network else {}
    if not network and p.get("doi"):
        cached = _cache.cache_get("integrity:crossref:" + str(_ids._norm_doi(p["doi"]) or "").lower())
        facts = cached if isinstance(cached, dict) else {}
    if facts.get("retraction") and not (p.get("retraction") or {}).get("notice_doi"):
        p["retraction"] = {**(p.get("retraction") or {}), **facts["retraction"]}
    if facts.get("concern"):
        p["concern"] = facts["concern"]
    versions = {**(p.get("versions") or {}), **(facts.get("versions") or {})}
    if network and p.get("arxiv_id") and "published_as" not in versions:
        published = _arxiv_published_as(p)
        if published:
            versions["published_as"] = published
    if versions:
        p["versions"] = versions
    return p


def is_retracted(paper: dict[str, Any] | None) -> bool:
    return bool(((paper or {}).get("retraction") or {}).get("is_retracted"))


def describe(paper: dict[str, Any]) -> str:
    """One line for warnings and signals, e.g. 'retracted (retraction notice doi:10.x, 2010-02-02; crossref)'."""
    r = paper.get("retraction") or {}
    bits = [b for b in (f"notice doi:{r['notice_doi']}" if r.get("notice_doi") else "", r.get("date") or "") if b]
    detail = ", ".join(bits)
    return f"{r.get('kind') or 'retraction'}{f' ({detail})' if detail else ''}; source: {r.get('source') or 'unknown'}"
