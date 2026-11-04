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

import re
import unicodedata
import urllib.parse
from typing import Any

from noul import ids as _ids
from noul.net import cache as _cache
from noul.net import http as _nhttp

# Crossref update types that withdraw the work, and the ones that only flag it.
WITHDRAWN = frozenset({"retraction", "withdrawal", "removal", "partial_retraction"})
CONCERN = frozenset({"expression_of_concern"})
RETRACTED_PUB_TYPES = frozenset({"retracted publication", "retracted-publication"})
NOTICE_TYPES = frozenset({"retraction", "erratum", "expression-of-concern"})
ARXIV_DOI_PREFIX = "10.48550/arxiv."
CHECK_FAILED = "integrity_check_failed"
CHECK_FAILED_WARNING = (
    "Crossref did not answer, so the retraction check is incomplete: no `retraction` here does not clear the paper."
)
_CACHE_TTL = 86400 * 7  # retractions are rare but not instant: re-check weekly
# Crossref fields are written by depositors. The pattern keeps markup and spaces out, but a DOI suffix can still spell
# words: keep notice DOIs in structured fields listed in `untrusted_fields`, never in prose lines (see `describe`).
_STRICT_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9]{1,200}$")
_KNOWN_SOURCES = frozenset({"publisher", "retraction-watch"})


def _safe_doi(raw: Any) -> str | None:
    doi = _ids._norm_doi(raw)
    return doi if doi and _STRICT_DOI.match(doi) else None


def _date(parts: Any) -> str | None:
    try:
        dp = (parts or {}).get("date-parts") or []
        return "-".join(f"{int(x):02d}" if i else str(int(x)) for i, x in enumerate(dp[0])) if dp and dp[0] else None
    except (AttributeError, TypeError, ValueError, IndexError):
        return None


def _dicts(items: Any) -> list[dict[str, Any]]:
    """The object entries of a depositor list: one malformed entry must not hide the valid ones."""
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def from_pub_types(pub_types: list[Any] | None, source: str) -> dict[str, Any] | None:
    """`retraction` record when a database indexes the work as a Retracted Publication."""
    if any(str(t or "").strip().lower() in RETRACTED_PUB_TYPES for t in pub_types or []):
        return {"is_retracted": True, "kind": "retraction", "source": source}
    return None


def from_openalex(work: dict[str, Any]) -> dict[str, Any] | None:
    # OpenAlex also sets is_retracted on the retraction *notice* (type "retraction"): the notice is not retracted.
    if not work.get("is_retracted") or str(work.get("type") or "").lower() in NOTICE_TYPES:
        return None
    return {"is_retracted": True, "kind": "retraction", "source": "openalex"}


def parse_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """Integrity facts from a Crossref work record: `retraction`, `concern`, `versions` (all optional)."""
    out: dict[str, Any] = {}
    for upd in _dicts(message.get("updated-by")):
        kind = str(upd.get("type") or "").strip().lower().replace(" ", "_")
        origin = str(upd.get("source") or "").strip().lower()
        entry = {
            "kind": kind,
            "notice_doi": _safe_doi(upd.get("DOI")),
            "date": _date(upd.get("updated")),
            "source": "crossref" + (f"/{origin if origin in _KNOWN_SOURCES else 'other'}" if origin else ""),
        }
        if kind in WITHDRAWN and "retraction" not in out:
            out["retraction"] = {"is_retracted": True, **entry}
        elif kind in CONCERN and "concern" not in out:
            out["concern"] = entry
    relation = message.get("relation")
    rel = relation if isinstance(relation, dict) else {}
    versions: dict[str, str] = {}
    for key, field in (("is-preprint-of", "published_as"), ("has-preprint", "preprint")):
        for item in _dicts(rel.get(key)):
            doi = _safe_doi(item.get("id")) if str(item.get("id-type") or "doi").lower() == "doi" else None
            if doi:
                versions[field] = doi  # a bare DOI: `get_paper` resolves it even when the paper is not cached
                break
    if versions:
        out["versions"] = versions
    return out


def crossref_facts(doi: str) -> dict[str, Any]:
    """Crossref integrity facts for a DOI (cached; {} when Crossref has no record, `check_failed` when it could not
    answer, so that a failed check is never mistaken for a clean paper)."""
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
            return {"check_failed": True}
        facts = {}
    _cache.cache_set(key, facts)
    return facts


def _arxiv_id(paper: dict[str, Any]) -> str | None:
    """The arXiv id of a preprint record. OpenAlex-resolved records only carry it inside the arXiv DataCite DOI."""
    if paper.get("arxiv_id"):
        return str(paper["arxiv_id"])
    doi = (_ids._norm_doi(paper.get("doi")) or "").lower()
    return doi.removeprefix(ARXIV_DOI_PREFIX) if doi.startswith(ARXIV_DOI_PREFIX) else None


def _surnames(authors: Any) -> set[str]:
    names = (str(a).split()[-1] for a in authors if str(a).strip()) if isinstance(authors, list) else ()
    return {unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower() for n in names} - {""}


def _same_work(paper: dict[str, Any], found: dict[str, Any]) -> bool:
    """The resolver can match on a similar title alone, which also fits a different study ("type 1" / "type 2") or a
    deposit made to look like the paper: require the same title, a shared author, and a year that fits (a preprint can
    be posted up to a year after the publication)."""
    from noul.providers import arxiv_client

    title = arxiv_client._norm_title_key(str(paper.get("title") or ""))
    if not title or title != arxiv_client._norm_title_key(str(found.get("title") or "")):
        return False
    if not _surnames(paper.get("authors")) & _surnames(found.get("authors")):
        return False
    try:
        return int(found["year"]) >= int(paper["year"]) - 1
    except (KeyError, TypeError, ValueError):
        return True  # no year to compare: the shared author and the title still hold


def _arxiv_published_as(paper: dict[str, Any], arxiv_id: str) -> str | None:
    from noul.providers import arxiv_client  # heavy module: imported only for arXiv preprints

    doi = _ids._norm_doi(paper.get("doi"))
    if doi and not doi.lower().startswith(ARXIV_DOI_PREFIX):
        return None  # already the published record
    key = "integrity:arxiv-published:" + arxiv_id.lower()
    cached = _cache.cache_get(key, max_age=_CACHE_TTL)
    if isinstance(cached, dict):  # {"published_as": id | None}, negatives included
        return cached.get("published_as")
    entry = {"fields": {"title": paper.get("title") or "", "eprint": arxiv_id}}
    failures: list[str] = []
    try:
        found = arxiv_client.resolve_published_venue_for_preprint(entry, failures=failures)
    except Exception:
        return None  # transient: not cached
    pub_doi = _safe_doi(found.get("doi")) if found and _same_work(paper, found) else None
    published = pub_doi if pub_doi and not pub_doi.lower().startswith(ARXIV_DOI_PREFIX) else None
    if published or (paper.get("title") and not failures):  # "none found" only counts if every source could answer
        _cache.cache_set(key, {"published_as": published})
    return published


def enrich(paper: dict[str, Any], network: bool = True, versions: bool = True) -> dict[str, Any]:
    """Return a copy of `paper` with `retraction`, `concern` and `versions` filled in from every available source.

    `network=False` keeps to what the record and the cache already know (for offline tools such as the meter);
    `versions=False` skips the arXiv published-version lookup (callers that only need the retraction). When Crossref
    could not answer, the copy carries `CHECK_FAILED`: callers pop it and tell the model the check did not run."""
    p = dict(paper)
    facts = crossref_facts(p.get("doi") or "") if network else {}
    if not network and p.get("doi"):
        key = "integrity:crossref:" + str(_ids._norm_doi(p["doi"]) or "").lower()
        cached = _cache.cache_get(key, max_age=_CACHE_TTL)
        facts = cached if isinstance(cached, dict) else {}
    if facts.get("retraction") and not (p.get("retraction") or {}).get("notice_doi"):
        p["retraction"] = {**(p.get("retraction") or {}), **facts["retraction"]}
    if facts.get("concern"):
        p["concern"] = facts["concern"]
    if facts.get("check_failed"):
        p[CHECK_FAILED] = True
    found = {**(p.get("versions") or {}), **(facts.get("versions") or {})}
    arxiv_id = _arxiv_id(p)
    if network and versions and arxiv_id and "published_as" not in found:
        published = _arxiv_published_as(p, arxiv_id)
        if published:
            found["published_as"] = published
            found["published_as_match"] = "title-and-authors"  # not deposited by the publisher: worth confirming
    if found:
        p["versions"] = found
    return p


def is_retracted(paper: dict[str, Any] | None) -> bool:
    return bool(((paper or {}).get("retraction") or {}).get("is_retracted"))


def describe(paper: dict[str, Any]) -> str:
    """One line for warnings and signals, e.g. 'retraction (2010-02-02); source: crossref/publisher'. It leaves out
    the notice DOI, which depositors write: that stays in the structured field the tool lists as untrusted."""
    r = paper.get("retraction") or {}
    when = f" ({r['date']})" if r.get("date") else ""
    return f"{r.get('kind') or 'retraction'}{when}; source: {r.get('source') or 'unknown'}"
