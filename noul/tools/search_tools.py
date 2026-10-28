"""Phase 1 research tools built on the legacy core (registered in `noul_tools.TOOLS`)."""
from noul.providers import openalex_client, semantic_scholar
from noul.schemas.paper import PaperReference

DIRECTIONS = ("backward", "forward", "both")
SORTS = ("influential", "citations", "year")
MAX_LIMIT = 50


def _core():
    import noul_core

    return noul_core


def _sort_key(sort_by):
    if sort_by == "citations":
        return lambda r: int(r["citation_count"] or 0)
    if sort_by == "year":
        return lambda r: int(r["year"] or 0)
    return lambda r: (bool(r["is_influential"]), int(r["citation_count"] or 0), int(r["year"] or 0))


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def _cache_chained(core, paper):
    """Cache a reference so it can be chained (get_paper, get_paper_references, download_papers...).
    Fields already present in a richer cached record are never overwritten."""
    key = "paper:" + paper["id"]
    existing = core.cache_get(key)
    if existing:
        merged = dict(existing)
        for k, v in paper.items():
            if _empty(merged.get(k)) and not _empty(v):
                merged[k] = v
        merged["sources"] = list(dict.fromkeys((existing.get("sources") or []) + (paper.get("sources") or [])))
        paper = merged
    core.cache_set(key, paper)
    return paper


def _url(p):
    if p.get("doi"):
        return f"https://doi.org/{p['doi']}"
    if p.get("s2_id"):
        return f"https://www.semanticscholar.org/paper/{p['s2_id']}"
    if p.get("arxiv_id"):
        return f"https://arxiv.org/abs/{p['arxiv_id']}"
    if p.get("openalex_id"):
        return f"https://openalex.org/{p['openalex_id']}"
    return None


def _to_reference(p, influential):
    data = PaperReference(
        paper_id=p["id"], title=p.get("title") or "", authors=list(p.get("authors") or []),
        year=p.get("year"), venue=p.get("journal") or None, citation_count=max(0, int(p.get("citations") or 0)),
        is_influential=bool(influential), doi=p.get("doi"), url=_url(p),
    ).model_dump(mode="json")
    if p.get("domain"):
        data["domain"] = p["domain"]
    return data


def get_paper_references(paper_id, limit=10, direction="both", sort_by="influential"):
    """Backward references and/or forward citations of a paper, ranked and cached for chained navigation.
    Semantic Scholar first (with `isInfluential`); OpenAlex fallback on 429/errors or OpenAlex-only papers."""
    core = _core()
    direction = (direction or "both").lower()
    sort_by = (sort_by or "influential").lower()
    if direction not in DIRECTIONS:
        raise core.HTTPError(400, f"direction must be one of {', '.join(DIRECTIONS)}")
    if sort_by not in SORTS:
        raise core.HTTPError(400, f"sort_by must be one of {', '.join(SORTS)}")
    try:
        limit = int(limit if limit is not None else 10)
    except (TypeError, ValueError):
        raise core.HTTPError(400, "limit must be an integer between 1 and 50")
    if not 1 <= limit <= MAX_LIMIT:
        raise core.HTTPError(400, "limit must be an integer between 1 and 50")

    anchor = core.resolve_paper(paper_id)
    wanted = ("backward", "forward") if direction == "both" else (direction,)
    edges, warnings, source = {}, [], "semantic_scholar"
    ident = semantic_scholar.paper_identifier(anchor)
    if ident:
        try:
            for d in wanted:
                edges[d] = semantic_scholar.fetch_edges(ident, "references" if d == "backward" else "citations")
        except Exception as e:
            if hasattr(e, "close"):
                e.close()
            code = getattr(e, "code", None)
            reason = f"HTTP {code}" if code else type(e).__name__
            warnings.append(f"Semantic Scholar unavailable ({reason}); fell back to OpenAlex "
                            f"(is_influential = citations >= {openalex_client.INFLUENTIAL_CITATIONS}).")
            edges = {}
    else:
        warnings.append("Paper has no Semantic Scholar identifier; used OpenAlex.")
    if not edges:
        source = "openalex"
        try:
            graph = openalex_client.fetch_citation_graph(anchor["id"])
            edges = {d: graph[d] for d in wanted}
        except Exception as e:
            warnings.append(f"OpenAlex citation graph failed ({type(e).__name__}: {e}).")
            edges = {d: [] for d in wanted}

    out = {
        "paper": {"id": anchor["id"], "title": anchor.get("title"), "year": anchor.get("year"), "doi": anchor.get("doi")},
        "direction": direction,
        "sort_by": sort_by,
        "source": source,
    }
    for d in wanted:
        seen, refs = set(), []
        for p, influential in edges.get(d) or []:
            if p["id"] in seen or p["id"] == anchor["id"]:
                continue
            seen.add(p["id"])
            refs.append(_to_reference(_cache_chained(core, p), influential))
        refs.sort(key=_sort_key(sort_by), reverse=True)
        out[f"{d}_citations"] = refs[:limit]
        out[f"total_{d}"] = len(refs)
    out["warnings"] = warnings
    return out


def clarify_search_scope(query, answers=None, domain=None):
    """Clarification loop (#01): without `answers`, say whether the query is broad and return 3 domain-specific
    multiple-choice questions; with `answers` ({"q1": label | 1-based index | free text, ...}) also return the
    enriched query and the filters to pass to search_papers."""
    from noul.prompts import clarification

    core = _core()
    if not str(query or "").strip():
        raise core.HTTPError(400, "query must be a non-empty string")
    if answers is not None and not isinstance(answers, dict):
        raise core.HTTPError(400, "answers must be an object such as {\"q1\": \"...\", \"q2\": 2}")
    try:
        check = clarification.needs_clarification(query)
        qs = clarification.generate_clarification_questions(query, domain=domain)
        out = {
            "query": query,
            "needs_clarification": check["needs_clarification"],
            "reason": check["reason"],
            "criteria": check["criteria"],
            "domain": qs["domain"],
            "questions": qs["questions"],
        }
        if answers:
            refined = clarification.build_refined_query(query, answers, domain=domain)
            out.update({k: v for k, v in refined.items() if k != "original_query"})
            out["next_step"] = "Call search_papers with `refined_query` and the non-null `recommended_filters`."
        else:
            out["next_step"] = ("Ask the user these questions (or skip if the query is already specific), then call "
                                "clarify_search_scope again with `answers`.")
    except ValueError as e:
        raise core.HTTPError(400, str(e))
    return out
