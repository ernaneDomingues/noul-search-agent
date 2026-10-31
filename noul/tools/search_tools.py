"""Research tools built on the legacy core (registered in `noul_tools.TOOLS`)."""

import json
import re
from collections import Counter

from noul.providers import openalex_client, semantic_scholar
from noul.schemas.analysis import BooleanQuerySuggestion
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
        paper_id=p["id"],
        title=p.get("title") or "",
        authors=list(p.get("authors") or []),
        year=p.get("year"),
        venue=p.get("journal") or None,
        citation_count=max(0, int(p.get("citations") or 0)),
        is_influential=bool(influential),
        doi=p.get("doi"),
        url=_url(p),
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
        raise core.HTTPError(400, "limit must be an integer between 1 and 50") from None
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
            warnings.append(
                f"Semantic Scholar unavailable ({reason}); fell back to OpenAlex "
                f"(is_influential = citations >= {openalex_client.INFLUENTIAL_CITATIONS})."
            )
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
        "paper": {
            "id": anchor["id"],
            "title": anchor.get("title"),
            "year": anchor.get("year"),
            "doi": anchor.get("doi"),
        },
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
        raise core.HTTPError(400, 'answers must be an object such as {"q1": "...", "q2": 2}')
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
            out["next_step"] = (
                "Ask the user these questions (or skip if the query is already specific), then call "
                "clarify_search_scope again with `answers`."
            )
    except ValueError as e:
        raise core.HTTPError(400, str(e)) from e
    return out


# --------------------------------------------------------------------------- #07 boolean query expansion

MAX_EXPAND_PAPERS = 30
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9-]{1,9}\b")
_MIXED_ACRONYM = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{1,9}\b")
_NOT_ACRONYMS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "THE",
        "FOR",
        "WITH",
        "IN",
        "OF",
        "TO",
        "ON",
        "AT",
        "BY",
        "AN",
        "AS",
        "IS",
        "WE",
        "II",
        "III",
        "VS",
        "ET",
        "AL",
        "CI",
        "NA",
        "USA",
        "UK",
        "US",
        "EU",
        "OUR",
        "THIS",
        "ALL",
        "NEW",
        "NO",
        "YES",
        "GB",
        "MB",
        "KB",
        "TB",
        "HR",
        "RR",
        "SD",
        "SE",
        "IQR",
        "AUC",
        "MS",
        "KG",
        "MG",
        "ML",
        "DOI",
        "URL",
        "PDF",
    }
)
_GENERIC_WORDS = frozenset(
    {
        "increased",
        "increase",
        "increases",
        "decreased",
        "decrease",
        "reduced",
        "reduces",
        "improved",
        "improves",
        "achieves",
        "achieved",
        "including",
        "include",
        "includes",
        "limitation",
        "limitations",
        "result",
        "results",
        "show",
        "shows",
        "showed",
        "induces",
        "induced",
        "suggest",
        "suggests",
        "found",
        "observed",
        "significant",
        "significantly",
        "effect",
        "effects",
        "study",
        "studies",
        "paper",
        "propose",
        "proposed",
        "present",
        "using",
        "used",
        "based",
        "available",
        "data",
        "code",
        "method",
        "methods",
        "approach",
        "work",
        "future",
        "ci",
        "hr",
    }
)
_BOOLEAN = re.compile(r"\b(?:AND|OR|NOT)\b|\"")
_METHOD_TERMS = (
    "fine-tuning",
    "quantization",
    "post-training quantization",
    "quantization-aware training",
    "distillation",
    "knowledge distillation",
    "pruning",
    "low-rank adaptation",
    "retrieval-augmented generation",
    "transformer",
    "reinforcement learning",
    "contrastive learning",
    "graph neural network",
    "state space model",
    "mixture of experts",
    "instruction tuning",
    "randomized controlled trial",
    "double-blind",
    "cohort",
    "case-control",
    "meta-analysis",
    "systematic review",
    "cross-sectional",
    "mendelian randomization",
    "in vivo",
    "longitudinal",
    "difference-in-differences",
    "instrumental variable",
    "regression discontinuity",
    "panel data",
    "synthetic control",
    "structural model",
    "event study",
    "natural experiment",
)
_DOMAIN_DEFAULTS = {  # (precision terms, adjacent methods, noise to exclude)
    "computer_science": (
        ["benchmark", "ablation"],
        ["fine-tuning", "evaluation"],
        ["survey", "review", "position paper"],
    ),
    "biomedical": (
        ["randomized controlled trial", "meta-analysis"],
        ["cohort", "case-control"],
        ["review", "editorial", "case report"],
    ),
    "economics": (
        ["causal effect", "quasi-experimental"],
        ["difference-in-differences", "instrumental variable"],
        ["survey", "review", "comment"],
    ),
    "general_stem": (["experimental", "empirical"], ["measurement", "simulation"], ["review", "editorial"]),
}
_HUMAN_CLINICAL = re.compile(r"\b(?:patients?|clinical|humans?|adults|participants|trial)\b", re.I)


def _norm_word(w):
    w = w.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _query_words(query):
    return {
        _norm_word(w)
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", query or "")
        if w.upper() not in ("AND", "OR", "NOT")
    }


def _quote(term):
    return '"' + str(term).replace('"', "").strip() + '"'


def _acronyms(text):
    found = [m.group(0) for m in _ACRONYM.finditer(text)]
    for m in _MIXED_ACRONYM.finditer(text):  # LoRA, QLoRA, DiD, LLMs
        tok = m.group(0)
        upper = sum(ch.isupper() for ch in tok)
        if upper >= 2 and upper / len(tok) >= 0.4 and not tok.isupper():
            found.append(tok)
    out = []
    for tok in found:
        if tok.endswith("s") and tok[:-1].isupper() and len(tok) > 2:
            tok = tok[:-1]
        tok = tok.strip("-")
        if len(tok) >= 2 and tok.upper() not in _NOT_ACRONYMS and not tok.replace("-", "").isdigit():
            out.append(tok)
    return out


def _cached_corpus(core, query, limit=MAX_EXPAND_PAPERS):
    """Cached papers whose title/abstract mention at least half of the query's words (most recent first)."""
    from noul.tools.comparative_tools import STOPWORDS

    words = [w for w in _query_words(query) if w not in STOPWORDS and len(w) > 2]
    if not words or not hasattr(core, "_conn"):
        return []
    try:
        with core._conn() as db:
            rows = db.execute("SELECT v FROM kv WHERE k LIKE 'paper:%' ORDER BY ts DESC LIMIT 2000").fetchall()
    except Exception:
        return []
    need, scored = max(1, (len(words) + 1) // 2), []
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except (TypeError, ValueError):
            continue
        text = {
            _norm_word(w)
            for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", f"{p.get('title') or ''} {p.get('abstract') or ''}")
        }
        hits = sum(w in text for w in words)
        if hits >= need:
            scored.append((hits, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def _corpus_domain(papers, query):
    from noul.tools.comparative_tools import _domain

    domains = Counter(_domain(p) for p in papers)
    if domains:
        return domains.most_common(1)[0][0]
    try:
        from noul.providers import domain_classifier

        return domain_classifier.resolve_domain(title=query).value
    except Exception:
        return "general_stem"


def suggest_boolean_queries(query="", paper_ids=None):
    """Dynamic search expansion (#07): mine acronyms and emerging technical terms from the given papers (or cached
    papers matching `query`) and return 3 boolean queries: precision deepening (AND), synonym/method expansion
    (OR + AND) and noise exclusion (NOT)."""
    from noul.tools.comparative_tools import STOPWORDS, key_terms

    core = _core()
    query = str(query or "").strip()
    if paper_ids is not None and (
        not isinstance(paper_ids, (list, tuple)) or any(not isinstance(x, str) for x in paper_ids)
    ):
        raise core.HTTPError(400, "paper_ids must be a list of paper IDs/DOIs")
    ids = list(dict.fromkeys(x.strip() for x in paper_ids or [] if x.strip()))[:MAX_EXPAND_PAPERS]
    if not query and not ids:
        raise core.HTTPError(400, "Provide `query` and/or `paper_ids`")

    warnings, papers = [], []
    for pid in ids:
        try:
            papers.append(core.resolve_paper(pid))
        except Exception as e:
            warnings.append(f"Could not resolve '{pid}': {e}")
    if not ids:
        papers = _cached_corpus(core, query)
        if not papers:
            warnings.append(
                "No cached papers match the query; terms come from the query and domain defaults. "
                "Run search_papers first for corpus-driven suggestions."
            )

    qwords = _query_words(query)
    texts = [
        re.sub(r"(?:https?://|www\.)\S+|\S+@\S+", " ", f"{p.get('title') or ''}. {p.get('abstract') or ''}")
        for p in papers
    ]
    acr_df, acr_n = Counter(), Counter()
    for text in texts + ([query] if not papers else []):
        found = _acronyms(text)
        acr_n.update(found)
        acr_df.update(set(found))
    acronyms = [
        a for a, _ in sorted(acr_n.items(), key=lambda x: (-acr_df[x[0]], -x[1], x[0])) if _norm_word(a) not in qwords
    ]
    acr_lower = {a.lower() for a in acronyms}

    term_df, term_n = Counter(), Counter()
    for text in texts:
        terms = key_terms(text)
        term_n.update(terms)
        term_df.update(set(terms))
    candidates = [
        t
        for t in term_n
        if not any(_norm_word(w) in qwords for w in t.split())
        and t not in acr_lower
        and not re.search(r"(?:^|[\s-])\d+(?:[.,]\d+)?(?:[\s-]|$)", t)
        and not re.fullmatch(r"[\d.%x-]+", t)
        and all(w not in STOPWORDS and w not in _GENERIC_WORDS for w in t.split())
    ]
    candidates.sort(key=lambda t: (-term_df[t], -(" " in t), -term_n[t], t))
    emerging = []
    for t in candidates:
        if any(t in e.split() for e in emerging if " " in e):
            continue
        emerging.append(t)
        if len(emerging) >= 10:
            break

    domain = _corpus_domain(papers, query)
    precision_defaults, method_defaults, noise = _DOMAIN_DEFAULTS.get(domain, _DOMAIN_DEFAULTS["general_stem"])
    corpus_text = " ".join(texts).lower()
    if domain == "biomedical" and _HUMAN_CLINICAL.search(query + " " + corpus_text):
        noise = ["mice", "rats", "in vitro", "animal model"]

    if query:
        core_expr = query if _BOOLEAN.search(query) else " AND ".join(query.split())
    else:
        seeds = []
        for t in emerging or acronyms:
            if not any(t in x or x in t for x in seeds):
                seeds.append(t)
            if len(seeds) == 2:
                break
        emerging = [e for e in emerging if e not in seeds]
        acronyms = [a for a in acronyms if a not in seeds]
        core_expr = " OR ".join(_quote(t) for t in seeds)
    core_expr = f"({core_expr})"

    precision = list(dict.fromkeys(acronyms[:1] + emerging[:2]))[:3]
    if len(precision) < 2:
        precision = list(dict.fromkeys(precision + precision_defaults))[:3]
    lexicon = [
        m
        for m in _METHOD_TERMS
        if re.search(r"\b" + re.escape(m) + r"\b", corpus_text)
        and not any(_norm_word(w) in qwords for w in re.split(r"[\s-]+", m))
    ]
    synonym = next((a for a in acronyms if a not in precision), None) or (acronyms[0] if acronyms else None)
    methods = []
    for m in dict.fromkeys(lexicon + [e for e in emerging if e not in precision] + method_defaults):
        if not any(m in x or x in m for x in methods + ([synonym.lower()] if synonym else [])):
            methods.append(m)
    methods = methods[:2]
    noise = [n for n in noise if not any(_norm_word(w) in qwords for w in n.split())] or ["retracted", "erratum"]

    expanded = f"({core_expr} OR {_quote(synonym)})" if synonym else core_expr
    suggestions = [
        BooleanQuerySuggestion(
            index=1,
            strategy="precision_deepening",
            boolean_query=f"{core_expr} AND ({' OR '.join(_quote(t) for t in precision)})",
            rationale="Narrows the original query with the most frequent technical acronyms/terms of the corpus "
            "that are not yet in the query.",
            target_terms=precision,
        ),
        BooleanQuerySuggestion(
            index=2,
            strategy="synonym_and_method_expansion",
            boolean_query=f"{expanded} AND ({' OR '.join(_quote(t) for t in methods)})",
            rationale=(
                "Broadens the core "
                + (f"with the synonym/acronym {_quote(synonym)} and " if synonym else "")
                + "anchors it on adjacent methods, metrics or populations found in the corpus."
            ),
            target_terms=([synonym] if synonym else []) + methods,
        ),
        BooleanQuerySuggestion(
            index=3,
            strategy="noise_exclusion",
            boolean_query=f"{core_expr} NOT ({' OR '.join(_quote(t) for t in noise)})",
            rationale=f"Excludes common noise for {domain} searches ("
            + ", ".join(noise)
            + ") to keep primary studies.",
            target_terms=noise,
        ),
    ]
    out = {
        "original_query": query,
        "domain": domain,
        "corpus_size": len(papers),
        "extracted_acronyms": acronyms[:10],
        "emerging_terms": emerging[:10],
        "suggestions": [s.model_dump(mode="json") for s in suggestions],
    }
    if warnings:
        out["warnings"] = warnings
    return out
