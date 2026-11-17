"""Multi-source search orchestration, deduplication, ranking and paper resolution."""

import concurrent.futures
import contextvars
import hashlib
import json
import re
import threading
from collections.abc import Callable

from noul import errors as _errors
from noul import ids as _ids
from noul import log as _log
from noul.evidence import classify as _classify
from noul.evidence import impact as _impact
from noul.net import cache as _cache
from noul.net import http as _nhttp
from noul.providers import annotate as _annotate
from noul.providers import arxiv_search as _arxiv_search
from noul.providers import crossref as _crossref
from noul.providers import endpoints as _endpoints
from noul.providers import europepmc as _europepmc
from noul.providers import openalex as _openalex
from noul.providers import pubmed as _pubmed
from noul.providers import s2 as _s2

SOURCES = ("openalex", "pubmed", "s2", "crossref", "arxiv", "europepmc")


DEFAULT_SOURCES = ("openalex", "pubmed", "s2", "crossref")


SEARCH_TIMEOUT = 18


def _merge_papers(lists):
    by_key = {}
    for p in lists:
        doi = _ids._norm_doi(p.get("doi"))
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
                cur["impact"], cur["tier"] = _impact._estimate_impact(
                    cur["citations"], cur.get("year"), cur.get("journal")
                )
            # Keep identifiers and open access URLs
            for field in (
                "doi",
                "pmid",
                "pmc_id",
                "openalex_id",
                "s2_id",
                "arxiv_id",
                "pdf_url",
                "journal",
                "year",
                "primary_category",
            ):
                if not cur.get(field) and p.get(field):
                    cur[field] = p[field]
            for field in ("categories", "fields_of_study", "mesh_terms"):
                if p.get(field):
                    cur[field] = _ids._unique((cur.get(field) or []) + list(p[field]))
            if p.get("open_access"):
                cur["open_access"] = True
            # Integrity facts from any source win: one database flagging a retraction is enough to show it.
            for field in ("retraction", "concern"):
                if p.get(field) and not cur.get(field):
                    cur[field] = p[field]
            if p.get("versions"):
                cur["versions"] = {**p["versions"], **(cur.get("versions") or {})}
            if _classify.EV_W.get(p.get("evidence") or "", 0) > _classify.EV_W.get(cur.get("evidence") or "", 0):
                cur["evidence"] = p["evidence"]
            by_key[dedup_key] = _annotate.annotate_paper(cur)
    return list(by_key.values())


def _score_paper(p, query=""):
    ev_score = _classify.EV_W.get(p.get("evidence"), 0.3) * 40.0
    cites = min(int(p.get("citations") or 0), 500) / 500.0 * 25.0
    has_abs = 15.0 if len(p.get("abstract") or "") > 80 else 0.0
    q_terms = [w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())]
    text = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
    overlap = sum(1 for w in set(q_terms) if w in text) / max(1, len(set(q_terms))) * 20.0 if q_terms else 10.0
    return ev_score + cites + has_abs + overlap


# Optional progress callback `(source, completed, total)`, set by servers that stream progress (MCP). A context
# variable keeps search()'s signature unchanged; the callback runs in worker threads and must never raise.
SOURCE_PROGRESS: contextvars.ContextVar[Callable[[str, int, int], None] | None] = contextvars.ContextVar(
    "noul_source_progress", default=None
)


def _report_progress(futs, total):
    callback = SOURCE_PROGRESS.get()
    if callback is None:
        return
    lock, done = threading.Lock(), [0]

    def notify(fut, source):
        with lock:
            done[0] += 1
            completed = done[0]
        try:
            callback(source, completed, total)
        except Exception:
            pass  # progress is best-effort: it must never break a search

    for fut, source in futs.items():
        fut.add_done_callback(lambda f, s=source: notify(f, s))


def _remember_paper(p):
    """Cache a search result without losing what is known about it: most search sources do not report retractions,
    so a later search must not erase one (the meter would then count the paper)."""
    prior = _cache.get_paper(p["id"]) or {}
    for field in ("retraction", "concern"):
        if prior.get(field) and not p.get(field):
            p[field] = prior[field]
    if prior.get("versions"):
        p["versions"] = {**prior["versions"], **(p.get("versions") or {})}
    _cache.put_paper(p["id"], p)


def search(query, filters=None, sources=None):
    filters = filters or {}
    chosen = [s for s in (sources or DEFAULT_SOURCES) if s in SOURCES]
    if not chosen:
        chosen = ["openalex", "pubmed", "s2"]
    year_min = filters.get("year_min")
    open_access = bool(filters.get("open_access"))

    cache_key = (
        "search:"
        + hashlib.sha1(json.dumps({"q": query, "f": filters, "s": chosen}, sort_keys=True).encode("utf-8")).hexdigest()
    )
    cached = _cache.cache_get(cache_key, max_age=3600 * 12)
    if cached:
        for p in cached.get("papers") or []:
            _remember_paper(p)
        return cached

    warnings = []
    raw_papers = []
    active_sources = []

    def _run_source(src):
        if src == "openalex":
            return _openalex._search_openalex(query, limit=25, year_min=year_min, open_access=open_access)
        if src == "pubmed":
            return _pubmed._search_pubmed(query, limit=20, year_min=year_min)
        if src == "s2":
            return _s2._search_s2(query, limit=20, year_min=year_min, open_access=open_access)
        if src == "crossref":
            return _crossref._search_crossref(query, limit=15, year_min=year_min)
        if src == "arxiv":
            return _arxiv_search._search_arxiv(query, limit=15, year_min=year_min)
        if src == "europepmc":
            return _europepmc._search_europepmc(query, limit=20, year_min=year_min, open_access=open_access)
        return []

    # No `with` block: its implicit shutdown(wait=True) would block on the slowest source.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen))
    futs = {ex.submit(_run_source, s): s for s in chosen}
    _report_progress(futs, len(chosen))
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
        _remember_paper(p)

    out = {"papers": merged, "sources": active_sources, "warnings": warnings}
    if merged:
        _cache.cache_set(cache_key, out)
    return out


def _resolve_arxiv(ax):
    """An arXiv id via Semantic Scholar, then OpenAlex (arXiv DOI 10.48550/arXiv.<id>), then the arXiv API: one
    rate-limited source (S2 answers 429 often without a key) must not make the paper unresolvable."""
    base = re.sub(r"v\d+$", "", ax)
    attempts = (
        lambda: _s2._parse_s2_paper(
            _nhttp._http_json(_endpoints.s2_paper(ax, _s2.S2_PAPER_FIELDS, prefix="ARXIV:", safe="/."))
        ),
        lambda: _openalex._parse_openalex_work(
            _nhttp._http_json(_endpoints.openalex_work(doi="10.48550/arXiv." + base))
        ),
        lambda: next(iter(_arxiv_search.fetch_by_ids([ax])), None),
    )
    for attempt in attempts:
        try:
            paper = attempt()
        except Exception as e:  # one failure per source is expected
            _log.logger.info("arXiv %s: source failed (%s), trying the next one", ax, type(e).__name__)
            continue
        if paper:
            return paper
    return None


def resolve_paper(identifier):
    """Retrieve a paper from cache or fetch on-demand by ID, DOI, OpenAlex ID, PMID, arXiv ID, or S2 ID."""
    if not identifier:
        raise _errors.HTTPError(400, "Empty paper identifier")
    ident = str(identifier).strip()

    # 1. Try direct cache hit
    p = _cache.get_paper(ident)
    if p:
        return p

    # 2. Try normalized DOI or prefixed identifier (arXiv first: "2410.12345" is not a DOI)
    is_arxiv = ident.lower().startswith("arxiv:") or bool(_ids.ARXIV_RE.match(ident))
    norm = None if is_arxiv else _ids._norm_doi(ident)
    if norm:
        cand_id = _ids._paper_id(doi=norm)
        p = _cache.get_paper(cand_id)
        if p:
            return p

    # 3. Fetch on demand from OpenAlex or Semantic Scholar
    fetched = None
    unavailable = None
    try:
        if is_arxiv:
            fetched = _resolve_arxiv(ident.split(":", 1)[-1].strip())
        elif norm:
            url = _endpoints.openalex_work(doi=norm)
            fetched = _openalex._parse_openalex_work(_nhttp._http_json(url))
        elif ident.startswith("oa:") or re.match(r"^W\d+$", ident, flags=re.I):
            oa_id = ident.split(":", 1)[-1].upper()
            url = _endpoints.openalex_work(openalex_id=oa_id)
            fetched = _openalex._parse_openalex_work(_nhttp._http_json(url))
        elif ident.startswith("pmid:") or ident.isdigit():
            pmid = ident.split(":", 1)[-1]
            url = _endpoints.openalex_work(pmid=pmid)
            fetched = _openalex._parse_openalex_work(_nhttp._http_json(url))
        elif ident.startswith("s2:") or re.match(r"^[0-9a-f]{40}$", ident, flags=re.I):
            s2_id = ident.split(":", 1)[-1]
            url = _endpoints.s2_paper(s2_id, _s2.S2_PAPER_FIELDS)
            fetched = _s2._parse_s2_paper(_nhttp._http_json(url))
    except _errors.HTTPError as e:
        # "The source is rate limited or down" is not "the paper does not exist": keep the two apart.
        if e.code == 429 or e.code >= 500:
            unavailable = e.code
    except Exception as e:
        _log.logger.info("paper lookup failed (%s)", type(e).__name__)

    if fetched:
        _cache.put_paper(fetched["id"], fetched)
        if fetched["id"] != ident:
            _cache.put_paper(ident, fetched)
        return fetched

    if unavailable:
        raise _errors.HTTPError(
            429 if unavailable == 429 else 503,
            f"Paper '{ident}' could not be looked up: the source answered {unavailable} "
            "(rate limited or unavailable). It may well exist: try again shortly.",
        )
    raise _errors.HTTPError(
        404, f"Paper '{ident}' not found in cache or public APIs; run search_papers or pass a valid DOI/ID."
    )
