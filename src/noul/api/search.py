"""Finding papers: search, scope clarification, one paper's record, domain, open artifacts and references."""

import sqlite3
from typing import Any

from noul import errors as _errors
from noul import log as _log
from noul import registry as _registry
from noul.api import common as _common
from noul.evidence import integrity as _integrity
from noul.library import fulltext as _ft
from noul.library import store as _store
from noul.net import cache as _cache
from noul.providers import artifact_radar as _artifact_radar
from noul.providers import domain_classifier as _domain_classifier
from noul.schemas import tool_inputs as _inputs
from noul.search import engine as _engine
from noul.tools import search_tools as _search_tools


@_registry.tool(
    "search_papers",
    description=(
        "Search peer-reviewed and preprint literature across all scientific disciplines (OpenAlex, PubMed, "
        "Semantic Scholar, Crossref, arXiv, Europe PMC), deduplicated and ranked by study design and "
        "citations. Returns abstracts, evidence level (meta-analysis, systematic-review, rct, review, cohort, "
        "empirical, preclinical, other) and a citation/venue band (`tier`: top, high, standard, emerging — "
        "not a JCR quartile) and scientific `domain`. Papers a source indexes as retracted carry `retraction`. "
        "YOU classify each paper's stance and synthesize."
    ),
    input=_inputs.SearchPapersInput,
    profiles=("research",),
    write_args=("project",),
    write_note=(
        "With `project` (a research project slug) the search is logged in the project's search history and the "
        "papers returned are added to it as candidates."
    ),
)
def search_papers(
    query,
    sources=None,
    year_min=None,
    study_types=None,
    min_citations=None,
    open_access=False,
    sort=None,
    limit=15,
    offset=0,
    exclude_ids=None,
    domain=None,
    clarify=False,
    project=None,
):
    wanted_domain = _common._domain_override(domain)
    if project:
        if len(str(query)) > _store.MAX_QUERY:
            raise _errors.HTTPError(400, f"A search logged in a project takes at most {_store.MAX_QUERY} characters")
        _common._project_call(_store.get_project, project, include_papers=False)  # 404 before the network search
    f: dict[str, Any] = {"open_access": bool(open_access)}
    if year_min:
        f["year_min"] = int(year_min)
    if min_citations:
        f["min_citations"] = int(min_citations)
    if sort in ("date", "citations", "relevance"):
        f["sort"] = sort
    if study_types:
        f["study_types"] = [t for t in study_types if t in _common.EVIDENCE]
    r = _engine.search(query, f, sources)
    all_papers = r["papers"]
    if exclude_ids:
        excl = set(exclude_ids)
        all_papers = [p for p in all_papers if p.get("id") not in excl and p.get("doi") not in excl]
    if wanted_domain:
        all_papers = [p for p in all_papers if _common._paper_domain(p) == wanted_domain]
    start = max(0, int(offset or 0))
    lim = max(1, min(int(limit or 15), 40))
    papers = all_papers[start : start + lim]
    out = {
        "query": query,
        "total_found": len(all_papers),
        "offset": start,
        "sources": r["sources"],
        "warnings": r["warnings"],
        "papers": [_common._slim(p, i + 1 + start) for i, p in enumerate(papers)],
        "untrusted_fields": ["papers[].title", "papers[].abstract"],
    }
    if wanted_domain:
        out["domain"] = wanted_domain
    if project:
        filters: dict[str, Any] = {k: v for k, v in f.items() if v not in (None, False, [])}
        filters.update({k: v for k, v in (("sources", sources), ("domain", wanted_domain)) if v})
        filters.update(offset=start, limit=lim)
        if exclude_ids:
            filters["excluded_count"] = len(exclude_ids)  # `total_found` is counted after leaving these out
        try:
            logged = _common._project_call(
                _store.record_search,
                project,
                query,
                filters,
                len(all_papers),
                [_common._paper_record(p) for p in papers],
                writes=True,
            )
            out["project"] = {
                "slug": logged["project"]["slug"],
                "search_id": logged["search_id"],
                "added": logged["added"],
                "already_present": logged["already_present"],
            }
        except (_errors.HTTPError, sqlite3.Error) as e:  # the results still reach the model, with the reason
            why = e.message if isinstance(e, _errors.HTTPError) else f"project store error ({type(e).__name__})"
            out["warnings"] = [*out["warnings"], f"Search not recorded in the project: {why}"]
    if clarify:
        try:
            scope = _search_tools.clarify_search_scope(query, domain=wanted_domain)
            if scope["needs_clarification"]:
                out["clarification_suggested"] = {k: scope[k] for k in ("reason", "domain", "questions", "next_step")}
        except Exception as e:  # noqa: BLE001 - the scope hint is optional; it must not fail the search
            _log.logger.debug("clarification hint failed: %s", type(e).__name__)
    return out


@_registry.tool(
    "clarify_search_scope",
    description=(
        "Clarification loop for broad queries: says whether a query is too broad (needs >= 2 of: 5+ "
        "meaningful words, boolean operators/quoted phrase, time constraint, method + outcome) and returns 3 "
        'domain-specific multiple-choice questions. Ask the user, then call again with `answers` ({"q1": '
        "option label or 1-based index, ...}) to get `refined_query` and `recommended_filters` (year_min, "
        "study_types, sources, sort) for search_papers. Skip it when the user already gave a precise query."
    ),
    input=_inputs.ClarifySearchScopeInput,
    profiles=("research",),
    network=False,
)
def clarify_search_scope(query, answers=None, domain=None):
    return _search_tools.clarify_search_scope(query, answers=answers, domain=domain)


@_registry.tool(
    "get_paper",
    description=(
        "Get full record and abstract of a paper by cached ID, DOI, PMID, OpenAlex ID, arXiv ID, or Semantic "
        "Scholar ID. Includes its scientific `domain` (pass `domain` to override it) and integrity facts with "
        "their source: `retraction` (retracted/withdrawn, notice DOI and date), `concern` (expression of concern) "
        "and `versions` (`published_as` for a preprint, `preprint` for a published paper; DOIs for get_paper; "
        "`published_as_match` means the link comes from matching title and authors, so confirm it). A `warnings` "
        "entry means the check could not run, which does not clear the paper."
    ),
    input=_inputs.GetPaperInput,
    profiles=("research",),
)
def get_paper(id, domain=None):
    forced = _common._domain_override(domain)
    p = _integrity.enrich(_common._resolve(id))
    check_failed = p.pop(_integrity.CHECK_FAILED, False)
    _cache.put_paper(p["id"], p)  # offline tools (evidence_meter) see the integrity facts too
    out = _common._slim(p, abstract_chars=10000)
    if check_failed:
        out["warnings"] = [_integrity.CHECK_FAILED_WARNING]
    out["untrusted_fields"] = ["title", "abstract", "retraction.notice_doi", "concern.notice_doi", "versions"]
    if forced:
        out["domain"] = forced
        out["domain_overridden"] = True
    elif not out.get("domain"):
        dom = _common._paper_domain(p)
        if dom:
            out["domain"] = dom
    return out


@_registry.tool(
    "detect_paper_domain",
    description=(
        "Detect a paper's scientific macro-domain (computer_science, biomedical, economics, general_stem) "
        "from arXiv categories, fields of study, PubMed indexing, the journal/venue name and title/abstract "
        "vocabulary, explaining the signals used. `method` says which signal decided, strongest first: "
        "override, categories, fields_of_study, pubmed, venue, text, default; treat venue, text and default "
        "as weaker and confirm them against the abstract (`text_scores`). Use it to pick domain-appropriate "
        "evaluation criteria."
    ),
    input=_inputs.DetectPaperDomainInput,
    profiles=("research",),
)
def detect_paper_domain(paper_id, domain=None):
    """Classify a paper into computer_science | biomedical | economics | general_stem and explain why."""
    dc = _domain_classifier
    p = _common._resolve(paper_id)
    try:
        info = dc.explain_domain(paper=p, override_domain=domain)
    except ValueError as e:
        raise _errors.HTTPError(400, str(e)) from e
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "domain": info["domain"].value,
        "method": info["method"],
        "signals": info["signals"],
        "text_scores": info["text_scores"],
        "primary_category": p.get("primary_category") or "general",
        "categories": p.get("categories") or [],
        "fields_of_study": p.get("fields_of_study") or [],
        "overridden": info["method"] == "override",
    }


@_registry.tool(
    "scan_paper_artifacts",
    description=(
        "Open-artifact radar: find code repositories (GitHub/GitLab/...), model weights (Hugging Face, "
        "Civitai, Kaggle, PyTorch Hub) and datasets (Zenodo, Figshare, OSF, Dataverse, HF datasets) linked "
        "from a paper, verify that the links are alive (HEAD, 5s timeout, max 20 URLs) and flag evasive "
        "'available on request' statements. Set inspect_fulltext=true to also scan the open-access full text."
    ),
    input=_inputs.ScanPaperArtifactsInput,
    profiles=("review",),
)
def scan_paper_artifacts(id, verify_http=True, inspect_fulltext=False):
    """Detect code repositories, model weights and datasets linked from a paper and check they are reachable."""
    radar = _artifact_radar
    p = _common._resolve(id)
    pid = p["id"]
    text, warnings = "", []
    if inspect_fulltext:
        try:
            if _ft.fulltext(pid).get("available"):
                text = (_cache.cache_get("ft:" + pid) or {}).get("text") or ""
            else:
                warnings.append("Full text not available; scanned title/abstract only.")
        except _errors.UPSTREAM_ERRORS as e:
            warnings.append(f"Full text unavailable ({type(e).__name__}); scanned title/abstract only.")
    res = radar.scan_artifacts_from_paper(
        p, fulltext=text, verify_http=bool(verify_http), max_urls=radar.MAX_VERIFY_URLS
    )
    _cache.put_paper(pid, p)
    out = {
        "id": pid,
        "title": p.get("title"),
        "verified_http": bool(verify_http),
        "scanned_fulltext": bool(text),
        **res,
    }
    if res["evasive_availability_statement"]:
        warnings.append("Paper states artifacts are only available on request/after publication (evasive statement).")
    if warnings:
        out["warnings"] = warnings
    return out


@_registry.tool(
    "get_paper_references",
    description=(
        "Citation graph with influence ranking: papers this paper cites (`backward_citations`) and papers "
        "citing it (`forward_citations`), with title, year, venue, citation_count and Semantic Scholar's "
        "`is_influential` flag (OpenAlex fallback: >= 50 citations). Every returned `paper_id` is cached, so "
        "you can chain get_paper, get_paper_references or download_papers on it."
    ),
    input=_inputs.GetPaperReferencesInput,
    profiles=("research",),
)
def get_paper_references(paper_id, limit=10, direction="both", sort_by="influential"):
    return _search_tools.get_paper_references(paper_id, limit, direction, sort_by)
