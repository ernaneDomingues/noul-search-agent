"""Tool layer shared by the CLI and the MCP server. The host agent's own LLM does
stance classification and synthesis; these tools retrieve, structure, download, and persist evidence."""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import noul_core as _local_core  # noqa: E402

# Optional shared web-app core, used only when explicitly requested via NOUL_APP_DIR.
_explicit_app_dir = os.environ.get("NOUL_APP_DIR")
if _explicit_app_dir and (Path(_explicit_app_dir) / "noul" / "core.py").exists():
    sys.path.insert(0, _explicit_app_dir)
    from noul import core as c  # noqa: E402
else:
    c = _local_core

# Configuration always comes from the built-in core (an external core may not implement it).
_cfg = c if hasattr(c, "get_config") and hasattr(c, "set_config") else _local_core

EVIDENCE = list(c.EV_W)
STANCE_ALIASES = {
    "supportive": "supportive", "support": "supportive", "supports": "supportive", "yes": "supportive",
    "positive": "supportive", "favorable": "supportive", "favoravel": "supportive", "favorável": "supportive",
    "contrary": "contrary", "contradicts": "contrary", "against": "contrary", "no": "contrary",
    "negative": "contrary", "contrario": "contrary", "contrário": "contrary",
    "inconclusive": "inconclusive", "possibly": "inconclusive", "mixed": "inconclusive", "neutral": "inconclusive",
    "unclear": "inconclusive", "inconclusivo": "inconclusive",
}


def _safe_output_dir(requested_dir=None, default_subdir="papers") -> Path:
    """Resolve an output directory confined to the configured workspace (relative paths are workspace-relative)."""
    cfg = _cfg.get_config()
    workspace = Path(cfg["workspace"]).resolve()
    cfg_key = {"papers": "download_dir", "research": "research_dir"}.get(default_subdir, f"{default_subdir}_dir")
    raw = requested_dir or cfg.get(cfg_key) or f"./{default_subdir}"
    p = Path(str(raw)).expanduser()
    target = p.resolve() if p.is_absolute() else (workspace / p).resolve()
    if os.environ.get("NOUL_ALLOW_ANY_PATH") != "1":
        try:
            target.relative_to(workspace)
        except ValueError:
            raise c.HTTPError(
                403,
                f"Path '{target}' is outside the allowed workspace '{workspace}'. "
                "Use a path inside the workspace or change it with configure_noul(workspace=...).",
            )
    return target


def _slim(p, idx=None, abstract_chars=1500):
    out = {
        k: p.get(k)
        for k in (
            "id", "title", "authors", "year", "journal", "doi", "pmid", "arxiv_id",
            "citations", "evidence", "impact", "tier", "open_access", "pdf_url", "sources",
        )
        if p.get(k) is not None or k in ("id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access")
    }
    out["authors"] = (out.get("authors") or [])[:4]
    out["abstract"] = (p.get("abstract") or "")[:abstract_chars]
    if idx is not None:
        out["n"] = idx
    return out


def _resolve(id_or_doi):
    if hasattr(c, "resolve_paper"):
        return c.resolve_paper(id_or_doi)
    p = c.cache_get("paper:" + str(id_or_doi))
    if not p:
        raise c.HTTPError(404, f"Paper '{id_or_doi}' not in cache; run search_papers first")
    return p


def search_papers(query, sources=None, year_min=None, study_types=None, min_citations=None,
                  open_access=False, sort=None, limit=15, offset=0, exclude_ids=None):
    f = {"open_access": bool(open_access)}
    if year_min:
        f["year_min"] = int(year_min)
    if min_citations:
        f["min_citations"] = int(min_citations)
    if sort in ("date", "citations", "relevance"):
        f["sort"] = sort
    if study_types:
        f["study_types"] = [t for t in study_types if t in EVIDENCE]
    r = c.search(query, "quick", None, f, "en", sources)
    all_papers = r["papers"]
    if exclude_ids:
        excl = set(exclude_ids)
        all_papers = [p for p in all_papers if p.get("id") not in excl and p.get("doi") not in excl]
    start = max(0, int(offset or 0))
    lim = max(1, min(int(limit or 15), 40))
    papers = all_papers[start : start + lim]
    return {
        "query": query,
        "total_found": len(all_papers),
        "offset": start,
        "sources": r["sources"],
        "warnings": r["warnings"],
        "papers": [_slim(p, i + 1 + start) for i, p in enumerate(papers)],
    }


def get_paper(id):
    p = _resolve(id)
    return _slim(p, abstract_chars=10000)


def get_fulltext(id, question=""):
    p = _resolve(id)
    pid = p["id"]
    meta = c.fulltext(pid)
    if not meta.get("available"):
        # Provide full abstract fallback so the agent can still answer
        meta["abstract_fallback"] = (p.get("abstract") or "")[:10000]
        return meta
    text = (c.cache_get("ft:" + pid) or {}).get("text") or ""
    meta["excerpts"] = c.pick_chunks(text, question, n=6) if question else text[:9000]
    return meta


def query_fulltexts(ids, question, max_chunks_per_paper=4):
    """Query across multiple papers (full text when Open Access, or abstract fallback) to answer follow-up questions."""
    if not ids:
        raise c.HTTPError(400, "Provide at least one paper id in `ids`")
    results = []
    max_chunks = max(1, min(int(max_chunks_per_paper or 4), 10))
    for raw_id in ids[:20]:
        try:
            p = _resolve(raw_id)
            pid = p["id"]
            ft_meta = c.fulltext(pid)
            if ft_meta.get("available"):
                text = (c.cache_get("ft:" + pid) or {}).get("text") or ""
                chunks = c.pick_chunks(text, question, n=max_chunks)
                results.append({
                    "id": pid,
                    "title": p.get("title"),
                    "authors": (p.get("authors") or [])[:3],
                    "year": p.get("year"),
                    "doi": p.get("doi"),
                    "evidence": p.get("evidence"),
                    "content_source": "fulltext",
                    "excerpts": chunks,
                })
            else:
                abs_txt = p.get("abstract") or ""
                chunks = c.pick_chunks(abs_txt, question, n=2) if abs_txt else []
                results.append({
                    "id": pid,
                    "title": p.get("title"),
                    "authors": (p.get("authors") or [])[:3],
                    "year": p.get("year"),
                    "doi": p.get("doi"),
                    "evidence": p.get("evidence"),
                    "content_source": "abstract_only",
                    "excerpts": chunks,
                })
        except Exception as e:
            results.append({"id": raw_id, "error": str(e)})
    return {"question": question, "results": results}


def citation_graph(id):
    p = _resolve(id)
    res = c.citation_graph(p["id"])
    return {
        "id": p["id"],
        "title": p.get("title"),
        "references": [_slim(x, i + 1, abstract_chars=600) for i, x in enumerate(res.get("references") or [])],
        "citing": [_slim(x, i + 1, abstract_chars=600) for i, x in enumerate(res.get("citing") or [])],
    }


def related_papers(id, relation="all", study_types=None, year_min=None, limit=15):
    """Find similar papers, cited references, or citing papers for a given paper ID/DOI."""
    p = _resolve(id)
    valid_types = [t for t in (study_types or []) if t in EVIDENCE] or None
    if hasattr(c, "related_papers"):
        res = c.related_papers(p["id"], relation=relation, study_types=valid_types, year_min=year_min, limit=limit)
        return {
            "anchor_paper": res["anchor_paper"],
            "relation": res["relation"],
            "similar": [_slim(x, i + 1, abstract_chars=800) for i, x in enumerate(res.get("similar") or [])],
            "references": [_slim(x, i + 1, abstract_chars=800) for i, x in enumerate(res.get("references") or [])],
            "citing": [_slim(x, i + 1, abstract_chars=800) for i, x in enumerate(res.get("citing") or [])],
        }
    # Fallback to citation_graph if external core is used
    cg = citation_graph(p["id"])
    return {
        "anchor_paper": {"id": p["id"], "title": p.get("title")},
        "relation": relation,
        "similar": [],
        "references": cg["references"],
        "citing": cg["citing"],
    }


def download_papers(ids, output_dir=None):
    """Download Open Access PDFs for the given paper IDs/DOIs to `output_dir` (confined to the workspace)."""
    if not ids:
        raise c.HTTPError(400, "Provide at least one paper id or DOI in `ids`")
    dest = _safe_output_dir(output_dir, "papers")
    results = []
    for raw_id in ids[:25]:
        try:
            if hasattr(c, "download_paper_pdf"):
                results.append(c.download_paper_pdf(raw_id, output_dir=str(dest)))
            else:
                p = _resolve(raw_id)
                results.append({
                    "id": p["id"],
                    "title": p.get("title"),
                    "status": "unsupported_in_external_core",
                    "doi_url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
                })
        except Exception as e:
            results.append({"id": raw_id, "status": "error", "error": str(e)})
    downloaded_count = sum(1 for r in results if r.get("status") == "downloaded")
    return {
        "output_dir": str(dest),
        "downloaded_count": downloaded_count,
        "total_requested": len(results),
        "files": results,
    }


def export_papers(ids, format="bibtex"):
    papers = []
    for i in ids:
        try:
            papers.append(_resolve(i))
        except Exception:
            pass
    if not papers:
        raise c.HTTPError(404, "None of the paper IDs could be found in cache or APIs.")
    return c.export(papers, format)[0]


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "research").lower()).strip("-")
    return slug[:60] or "research-session"


def _dossier_paths(directory):
    base = _safe_output_dir(directory, "research")
    return (base, *(
        _safe_output_dir(base / name, "research")
        for name in ("README.md", "papers.json", "references.bib")
    ))


def save_research_dossier(topic, synthesis_markdown, paper_ids=None, output_dir=None):
    """Save a structured research dossier (README.md, papers.json, references.bib) so the user can
    converse over the research documentation and revisit it across sessions."""
    slug = _slugify(topic)
    base, md_path, json_path, bib_path = _dossier_paths(_safe_output_dir(output_dir, "research") / slug)
    base.mkdir(parents=True, exist_ok=True)

    papers = []
    for pid in paper_ids or []:
        try:
            papers.append(_resolve(pid))
        except Exception:
            pass

    header = (
        f"# Research Dossier: {topic}\n\n"
        f"- **Updated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"- **Papers Indexed:** {len(papers)}\n\n"
    )
    full_md = header + synthesis_markdown.strip() + "\n"
    md_path.write_text(full_md, encoding="utf-8")

    dossier_data = {
        "slug": slug,
        "topic": topic,
        "updated_at": int(time.time()),
        "papers": [_slim(p, i + 1, abstract_chars=10000) for i, p in enumerate(papers)],
    }
    json_path.write_text(json.dumps(dossier_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if papers:
        bib_txt = c.export(papers, "bibtex")[0]
        bib_path.write_text(bib_txt + "\n", encoding="utf-8")

    # Also register dossier in SQLite cache for MCP resource discovery
    index = c.cache_get("dossiers:index", max_age=0) or {}
    index[slug] = {
        "slug": slug,
        "topic": topic,
        "path": str(base),
        "readme_path": str(md_path),
        "paper_count": len(papers),
        "updated_at": int(time.time()),
    }
    c.cache_set("dossiers:index", index)

    return {
        "slug": slug,
        "topic": topic,
        "directory": str(base),
        "readme_path": str(md_path),
        "papers_json_path": str(json_path),
        "bibtex_path": str(bib_path) if papers else None,
        "paper_count": len(papers),
    }


def list_research_dossiers(output_dir=None):
    """List saved research dossiers from both the workspace directory and the index."""
    base = _safe_output_dir(output_dir, "research")
    items = {}
    index = c.cache_get("dossiers:index", max_age=0) or {}
    for slug, info in index.items():
        try:
            sub, readme, _, _ = _dossier_paths(info["path"])
        except c.HTTPError:
            continue
        if readme.is_file():
            items[slug] = {**info, "path": str(sub), "readme_path": str(readme)}

    if base.exists() and base.is_dir():
        for sub in sorted(base.iterdir()):
            try:
                directory, readme, papers_path, _ = _dossier_paths(sub)
            except c.HTTPError:
                continue
            if directory.is_dir() and readme.is_file():
                slug = sub.name
                meta = {}
                if papers_path.exists():
                    try:
                        meta = json.loads(papers_path.read_text(encoding="utf-8"))
                    except Exception:
                        meta = {}
                items[slug] = {
                    "slug": slug,
                    "topic": meta.get("topic") or slug,
                    "path": str(directory),
                    "readme_path": str(readme),
                    "paper_count": len(meta.get("papers") or []),
                    "updated_at": meta.get("updated_at"),
                }
    return {"dossiers": list(items.values())}


def get_research_dossier(slug, output_dir=None):
    """Load a saved research dossier (Markdown synthesis + cached paper records) to continue a discussion."""
    listing = list_research_dossiers(output_dir=output_dir)
    match = next((d for d in listing["dossiers"] if d["slug"] == slug or d["topic"].lower() == slug.lower()), None)
    if not match:
        raise c.HTTPError(404, f"Research dossier '{slug}' not found")
    sub, readme, papers_path, _ = _dossier_paths(match["path"])
    md_text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    papers = []
    if papers_path.exists():
        try:
            data = json.loads(papers_path.read_text(encoding="utf-8"))
            papers = data.get("papers") or []
            for p in papers:
                if p.get("id"):
                    c.cache_set("paper:" + p["id"], p)
        except Exception:
            pass
    return {
        "slug": match["slug"],
        "topic": match["topic"],
        "path": str(sub),
        "markdown": md_text,
        "papers": papers,
    }


def configure_noul(email=None, workspace=None, download_dir=None, research_dir=None, clear=False):
    """View or update persistent Noul configuration (email for Unpaywall/polite pool, workspace, default folders).
    Called without arguments it only reads the configuration."""
    if clear or any(v is not None for v in (email, workspace, download_dir, research_dir)):
        return _cfg.set_config(email=email, workspace=workspace, download_dir=download_dir,
                               research_dir=research_dir, clear=bool(clear))
    return _cfg.get_config()


def _cached_paper(pid):
    if not pid:
        return None
    p = c.cache_get("paper:" + str(pid))
    if p:
        return p
    doi = _local_core._norm_doi(pid)
    if doi:
        return c.cache_get("paper:" + _local_core._paper_id(doi=doi))
    return None


def evidence_meter(items):
    """Compute reproducible raw and design-weighted Evidence Meter (Noul Meter) percentages from LLM stance
    classifications. Each item: {"id": str, "stance": "supportive"|"inconclusive"|"contrary", "evidence"?: str}.
    The study-design weight comes from the cached paper record when available."""
    if not isinstance(items, list) or not items:
        raise c.HTTPError(400, "Provide at least one classified paper in `items`")
    raw_counts = {"supportive": 0, "inconclusive": 0, "contrary": 0}
    weighted = {"supportive": 0.0, "inconclusive": 0.0, "contrary": 0.0}
    breakdown = []
    skipped = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            skipped.append({"item": it, "reason": "not an object"})
            continue
        pid = it.get("id")
        stance = STANCE_ALIASES.get(str(it.get("stance") or "").lower().strip())
        if stance is None:
            skipped.append({"id": pid, "reason": f"unknown stance {it.get('stance')!r}"})
            continue
        if pid:
            if str(pid) in seen:
                skipped.append({"id": pid, "reason": "duplicate id"})
                continue
            seen.add(str(pid))
        p = _cached_paper(pid)
        ev = (p or {}).get("evidence") or it.get("evidence") or "other"
        w = float(c.EV_W.get(ev, c.EV_W.get("other", 0.3)))
        raw_counts[stance] += 1
        weighted[stance] += w
        breakdown.append({"id": pid, "stance": stance, "evidence": ev, "weight": w})
    total_n = sum(raw_counts.values())
    if total_n == 0:
        raise c.HTTPError(400, "No valid stances provided (use supportive, inconclusive or contrary)")
    total_w = sum(weighted.values()) or 1.0
    return {
        "total_papers": total_n,
        "raw_counts": raw_counts,
        "raw_percentages": {k: round(v * 100.0 / total_n, 1) for k, v in raw_counts.items()},
        "weighted_percentages": {k: round(v * 100.0 / total_w, 1) for k, v in weighted.items()},
        "weights": dict(c.EV_W),
        "breakdown": breakdown,
        "skipped": skipped,
    }


S = {"type": "string"}
_OUT_PAPERS = {"type": "string", "description": "Folder inside the workspace (relative paths are workspace-relative). "
               "Default: configured download_dir (./papers)."}
_OUT_RESEARCH = {"type": "string", "description": "Folder inside the workspace (relative paths are workspace-relative). "
                 "Default: configured research_dir (./research)."}
TOOLS = {
    "search_papers": (
        "Search peer-reviewed and preprint literature across all scientific disciplines (OpenAlex, PubMed, Semantic Scholar, "
        "Crossref, arXiv, Europe PMC), deduplicated and ranked by study design and citations. Returns abstracts, evidence level "
        "(meta-analysis, systematic-review, rct, review, cohort, empirical, preclinical, other) and a citation/venue band "
        "(`tier`: top, high, standard, emerging — not a JCR quartile). YOU classify each paper's stance and synthesize.",
        {
            "query": S,
            "sources": {"type": "array", "items": {"type": "string", "enum": list(c.SOURCES)}},
            "year_min": {"type": "integer"},
            "study_types": {"type": "array", "items": {"type": "string", "enum": EVIDENCE}},
            "min_citations": {"type": "integer"},
            "open_access": {"type": "boolean"},
            "sort": {"type": "string", "enum": ["relevance", "date", "citations"]},
            "limit": {"type": "integer", "default": 15},
            "offset": {"type": "integer", "default": 0},
            "exclude_ids": {"type": "array", "items": S},
        },
        ["query"],
        search_papers,
    ),
    "get_paper": (
        "Get full record and abstract of a paper by cached ID, DOI, PMID, OpenAlex ID, arXiv ID, or Semantic Scholar ID.",
        {"id": S},
        ["id"],
        get_paper,
    ),
    "get_fulltext": (
        "Fetch open-access full text (via OpenAlex, EuropePMC, arXiv, Unpaywall) and return excerpts relevant to `question`.",
        {"id": S, "question": S},
        ["id"],
        get_fulltext,
    ),
    "query_fulltexts": (
        "Query across multiple papers simultaneously for a specific question (uses full text when Open Access or abstract fallback). "
        "Ideal for conversing over a set of papers.",
        {"ids": {"type": "array", "items": S}, "question": S, "max_chunks_per_paper": {"type": "integer", "default": 4}},
        ["ids", "question"],
        query_fulltexts,
    ),
    "citation_graph": (
        "Papers this paper cites (references) and papers citing it (OpenAlex), automatically cached for follow-up.",
        {"id": S},
        ["id"],
        citation_graph,
    ),
    "related_papers": (
        "Find papers semantically similar to the target paper, papers it cites (references), or papers citing it. "
        "Supports filtering by study_types and year_min.",
        {
            "id": S,
            "relation": {"type": "string", "enum": ["all", "similar", "references", "citing"], "default": "all"},
            "study_types": {"type": "array", "items": {"type": "string", "enum": EVIDENCE}},
            "year_min": {"type": "integer"},
            "limit": {"type": "integer", "default": 15},
        },
        ["id"],
        related_papers,
    ),
    "download_papers": (
        "Download Open Access PDFs for one or more paper IDs or DOIs into `output_dir` (inside the workspace; "
        "default: configured download_dir, ./papers).",
        {"ids": {"type": "array", "items": S}, "output_dir": _OUT_PAPERS},
        ["ids"],
        download_papers,
    ),
    "save_research_dossier": (
        "Save a persistent research dossier (README.md, papers.json, references.bib) in `output_dir` (inside the "
        "workspace; default: configured research_dir, ./research) so the user can converse over the research documentation.",
        {
            "topic": S,
            "synthesis_markdown": S,
            "paper_ids": {"type": "array", "items": S},
            "output_dir": _OUT_RESEARCH,
        },
        ["topic", "synthesis_markdown"],
        save_research_dossier,
    ),
    "list_research_dossiers": (
        "List saved research dossiers available for follow-up conversation.",
        {"output_dir": _OUT_RESEARCH},
        [],
        list_research_dossiers,
    ),
    "get_research_dossier": (
        "Load a saved research dossier (Markdown report and paper metadata) by slug or topic.",
        {"slug": S, "output_dir": _OUT_RESEARCH},
        ["slug"],
        get_research_dossier,
    ),
    "export_papers": (
        "Export papers as bibtex, ris or csv.",
        {"ids": {"type": "array", "items": S}, "format": {"type": "string", "enum": ["bibtex", "ris", "csv"]}},
        ["ids"],
        export_papers,
    ),
    "evidence_meter": (
        "Compute the Evidence Meter deterministically from YOUR stance classification of each paper: raw counts and "
        "percentages plus study-design-weighted percentages (meta-analysis 1.0 … preclinical 0.2). Use its output "
        "instead of computing percentages yourself.",
        {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": S,
                        "stance": {"type": "string", "description": "supportive | inconclusive | contrary"},
                        "evidence": {"type": "string", "description": "Optional study design if the paper is not cached"},
                    },
                    "required": ["stance"],
                },
            },
        },
        ["items"],
        evidence_meter,
    ),
    "configure_noul": (
        "View (no arguments) or update the persistent Noul configuration: contact email (enables Unpaywall and the "
        "OpenAlex/Crossref polite pool), workspace folder, and default download/research folders. `clear` resets it.",
        {
            "email": S,
            "workspace": S,
            "download_dir": S,
            "research_dir": S,
            "clear": {"type": "boolean", "default": False},
        },
        [],
        configure_noul,
    ),
}

_JSON_TYPES = {"array": list, "object": dict, "string": str, "boolean": bool}


def call(name, args):
    if name not in TOOLS:
        raise c.HTTPError(404, f"Unknown tool {name}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise c.HTTPError(400, f"Arguments for {name} must be a JSON object")
    props, required = TOOLS[name][1], TOOLS[name][2]
    unknown = sorted(k for k in args if k not in props)
    if unknown:
        raise c.HTTPError(400, f"Unknown argument(s) for {name}: {', '.join(unknown)}")
    missing = [k for k in required if args.get(k) is None]
    if missing:
        raise c.HTTPError(400, f"Missing required argument(s) for {name}: {', '.join(missing)}")
    for k, v in args.items():
        expected = _JSON_TYPES.get(props[k].get("type"))
        if v is not None and expected and not isinstance(v, expected):
            raise c.HTTPError(400, f"Argument '{k}' for {name} must be of type {props[k]['type']}")
    return TOOLS[name][3](**args)
