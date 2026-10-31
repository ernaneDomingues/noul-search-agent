"""Citation graph and related-paper discovery."""

import urllib.error
import urllib.parse
import urllib.request

from noul import ids as _ids
from noul.net import cache as _cache
from noul.net import http as _nhttp
from noul.providers import openalex as _openalex
from noul.providers import s2 as _s2
from noul.search import engine as _engine


def _ensure_openalex_work(p):
    """Fetch full OpenAlex record for a paper dict to obtain referenced_works / related_works / openalex_id."""
    if p.get("openalex_id") and (p.get("referenced_works") is not None):
        return p
    oa_id = p.get("openalex_id")
    doi = _ids._norm_doi(p.get("doi"))
    pmid = p.get("pmid")
    url = None
    if oa_id:
        url = f"https://api.openalex.org/works/{urllib.parse.quote(oa_id, safe='')}{_nhttp._mailto_qs()}"
    elif doi:
        url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi, safe='')}{_nhttp._mailto_qs()}"
    elif pmid:
        url = f"https://api.openalex.org/works/pmid:{urllib.parse.quote(str(pmid), safe='')}{_nhttp._mailto_qs()}"
    if url:
        try:
            w = _nhttp._http_json(url, timeout=12)
            parsed = _openalex._parse_openalex_work(w)
            if parsed:
                p.update({k: v for k, v in parsed.items() if v and not p.get(k)})
                p["referenced_works"] = parsed.get("referenced_works") or []
                p["related_works"] = parsed.get("related_works") or []
                _cache.cache_set("paper:" + p["id"], p)
        except Exception:
            pass
    return p


def _fetch_openalex_batch(oa_ids, limit=15):
    ids = [x.rstrip("/").split("/")[-1] for x in (oa_ids or []) if x][: min(limit, 25)]
    if not ids:
        return []
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        _nhttp._with_mailto({"filter": "openalex_id:" + "|".join(ids), "per-page": len(ids)})
    )
    try:
        data = _nhttp._http_json(url, timeout=15)
        out = []
        for w in data.get("results") or []:
            p = _openalex._parse_openalex_work(w)
            if p:
                _cache.cache_set("paper:" + p["id"], p)
                out.append(p)
        return out
    except Exception:
        return []


def citation_graph(id):
    p = _ensure_openalex_work(_engine.resolve_paper(id))
    refs = _fetch_openalex_batch(p.get("referenced_works") or [], limit=15)
    citing = []
    oa_id = p.get("openalex_id")
    if oa_id:
        try:
            url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
                _nhttp._with_mailto({"filter": f"cites:{oa_id}", "sort": "cited_by_count:desc", "per-page": 15})
            )
            data = _nhttp._http_json(url, timeout=15)
            for w in data.get("results") or []:
                cp = _openalex._parse_openalex_work(w)
                if cp:
                    _cache.cache_set("paper:" + cp["id"], cp)
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
    p = _ensure_openalex_work(_engine.resolve_paper(id))
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
                url = (
                    f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"
                    f"{urllib.parse.quote(ident_for_s2, safe=':')}?fields={_s2.S2_PAPER_FIELDS}&limit={min(limit, 20)}"
                )
                data = _nhttp._http_json(url, timeout=12)
                for item in data.get("recommendedPapers") or []:
                    sp = _s2._parse_s2_paper(item)
                    if sp:
                        _cache.cache_set("paper:" + sp["id"], sp)
                        similar.append(sp)
            except Exception:
                pass

        # 2. Supplement with OpenAlex related_works
        if len(similar) < limit and p.get("related_works"):
            oa_rel = _fetch_openalex_batch(p.get("related_works"), limit=limit)
            similar = _engine._merge_papers(similar + oa_rel)

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
        res.sort(key=lambda x: _engine._score_paper(x, p.get("title") or ""), reverse=True)
        for x in res:
            _cache.cache_set("paper:" + x["id"], x)
        return res[: max(1, min(int(limit), 40))]

    return {
        "anchor_paper": {"id": p["id"], "title": p.get("title"), "year": p.get("year"), "doi": p.get("doi")},
        "relation": relation,
        "similar": _filter_list(similar) if relation in ("similar", "all") else [],
        "references": _filter_list(references) if relation in ("references", "all") else [],
        "citing": _filter_list(citing) if relation in ("citing", "all") else [],
    }
