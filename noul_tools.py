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


def _reject_symlink_path(path, requested_dir=None):
    """Refuse (HTTP 403) output paths with a symlink in any component below the workspace root, checking both the
    resolved path and the path as requested (whose symlinks `_safe_output_dir` already followed)."""
    workspace = Path(_cfg.get_config()["workspace"]).expanduser()
    roots = {Path(os.path.abspath(workspace)), workspace.resolve()}
    candidates = [Path(os.path.abspath(path))]
    if requested_dir:
        raw = Path(str(requested_dir)).expanduser()
        candidates.append(Path(os.path.abspath(raw if raw.is_absolute() else workspace / raw)))
    for cur in candidates:
        while cur not in roots and any(r in cur.parents for r in roots):
            if cur.is_symlink():
                raise c.HTTPError(403, f"Refusing to write through symbolic link '{cur}'.")
            cur = cur.parent
    return Path(path)


def _slim(p, idx=None, abstract_chars=1500):
    out = {
        k: p.get(k)
        for k in (
            "id", "title", "authors", "year", "journal", "doi", "pmid", "arxiv_id",
            "citations", "evidence", "impact", "tier", "open_access", "pdf_url", "sources", "domain",
            "has_code", "has_weights", "has_dataset", "artifacts",
        )
        if p.get(k) is not None or k in ("id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access")
    }
    out["authors"] = (out.get("authors") or [])[:4]
    out["abstract"] = (p.get("abstract") or "")[:abstract_chars]
    if idx is not None:
        out["n"] = idx
    return out


def _phase1(module):
    """Import a Phase 1 module from the local `noul` package (HTTP 501 when it is not deployed)."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise c.HTTPError(501, f"Feature unavailable: module '{module}' could not be imported ({e})")


def _domain_override(domain):
    """Validate a user-supplied domain ('cs', 'bio', 'econ', 'stem', ...) -> canonical value or None."""
    if domain is None or not str(domain).strip():
        return None
    dc = _phase1("noul.providers.domain_classifier")
    try:
        return dc.resolve_domain(override_domain=domain).value
    except ValueError as e:
        raise c.HTTPError(400, str(e))


def _paper_domain(p):
    if p.get("domain"):
        return p["domain"]
    try:
        return _phase1("noul.providers.domain_classifier").resolve_domain(paper=p).value
    except Exception:
        return None


def _resolve(id_or_doi):
    if hasattr(c, "resolve_paper"):
        return c.resolve_paper(id_or_doi)
    p = c.cache_get("paper:" + str(id_or_doi))
    if not p:
        raise c.HTTPError(404, f"Paper '{id_or_doi}' not in cache; run search_papers first")
    return p


def search_papers(query, sources=None, year_min=None, study_types=None, min_citations=None,
                  open_access=False, sort=None, limit=15, offset=0, exclude_ids=None, domain=None, clarify=False):
    wanted_domain = _domain_override(domain)
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
    if wanted_domain:
        all_papers = [p for p in all_papers if _paper_domain(p) == wanted_domain]
    start = max(0, int(offset or 0))
    lim = max(1, min(int(limit or 15), 40))
    papers = all_papers[start : start + lim]
    out = {
        "query": query,
        "total_found": len(all_papers),
        "offset": start,
        "sources": r["sources"],
        "warnings": r["warnings"],
        "papers": [_slim(p, i + 1 + start) for i, p in enumerate(papers)],
    }
    if wanted_domain:
        out["domain"] = wanted_domain
    if clarify:
        try:
            scope = _phase1("noul.tools.search_tools").clarify_search_scope(query, domain=wanted_domain)
            if scope["needs_clarification"]:
                out["clarification_suggested"] = {k: scope[k] for k in ("reason", "domain", "questions", "next_step")}
        except Exception:
            pass
    return out


def clarify_search_scope(query, answers=None, domain=None):
    return _phase1("noul.tools.search_tools").clarify_search_scope(query, answers=answers, domain=domain)


def get_paper(id, domain=None):
    forced = _domain_override(domain)
    p = _resolve(id)
    out = _slim(p, abstract_chars=10000)
    if forced:
        out["domain"] = forced
        out["domain_overridden"] = True
    elif not out.get("domain"):
        dom = _paper_domain(p)
        if dom:
            out["domain"] = dom
    return out


def detect_paper_domain(paper_id, domain=None):
    """Classify a paper into computer_science | biomedical | economics | general_stem and explain why."""
    dc = _phase1("noul.providers.domain_classifier")
    p = _resolve(paper_id)
    try:
        info = dc.explain_domain(paper=p, override_domain=domain)
    except ValueError as e:
        raise c.HTTPError(400, str(e))
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


def scan_paper_artifacts(id, verify_http=True, inspect_fulltext=False):
    """Detect code repositories, model weights and datasets linked from a paper and check they are reachable."""
    radar = _phase1("noul.providers.artifact_radar")
    p = _resolve(id)
    pid = p["id"]
    text, warnings = "", []
    if inspect_fulltext:
        try:
            if c.fulltext(pid).get("available"):
                text = (c.cache_get("ft:" + pid) or {}).get("text") or ""
            else:
                warnings.append("Full text not available; scanned title/abstract only.")
        except Exception as e:
            warnings.append(f"Full text unavailable ({type(e).__name__}); scanned title/abstract only.")
    res = radar.scan_artifacts_from_paper(p, fulltext=text, verify_http=bool(verify_http),
                                          max_urls=radar.MAX_VERIFY_URLS)
    c.cache_set("paper:" + pid, p)
    out = {"id": pid, "title": p.get("title"), "verified_http": bool(verify_http),
           "scanned_fulltext": bool(text), **res}
    if res["evasive_availability_statement"]:
        warnings.append("Paper states artifacts are only available on request/after publication (evasive statement).")
    if warnings:
        out["warnings"] = warnings
    return out


def get_paper_references(paper_id, limit=10, direction="both", sort_by="influential"):
    return _phase1("noul.tools.search_tools").get_paper_references(paper_id, limit, direction, sort_by)


def compare_papers(paper_ids, inspect_fulltext=False):
    return _phase1("noul.tools.comparative_tools").compare_papers(paper_ids, inspect_fulltext=inspect_fulltext)


def get_thematic_insights(paper_ids, topic=None):
    return _phase1("noul.tools.comparative_tools").get_thematic_insights(paper_ids, topic=topic)


def generate_research_hypotheses(paper_ids, topic=None, max_hypotheses=3):
    return _phase1("noul.tools.comparative_tools").generate_research_hypotheses(
        paper_ids, topic=topic, max_hypotheses=max_hypotheses)



def export_obsidian_notes(paper_ids, vault_dir=None, include_related_wikilinks=True):
    return _phase1("noul.tools.comparative_tools").export_obsidian_notes(
        paper_ids, vault_dir=vault_dir, include_related_wikilinks=include_related_wikilinks)


def suggest_boolean_queries(query="", paper_ids=None):
    return _phase1("noul.tools.search_tools").suggest_boolean_queries(query=query, paper_ids=paper_ids)


def extract_benchmark_tables(paper_id):
    return _phase1("noul.tools.technical_tools").extract_benchmark_tables(paper_id)


def estimate_hardware_specs(paper_id, total_params_b=None, active_params_b=None, context_length=8192):
    return _phase1("noul.tools.hardware_tools").estimate_hardware_specs(
        paper_id, total_params_b=total_params_b, active_params_b=active_params_b, context_length=context_length)


def verify_paper_claims(paper_id, claims=None):
    return _phase1("noul.tools.technical_tools").verify_paper_claims(paper_id, claims=claims)


def extract_paper_math(paper_id, mode="latex"):
    return _phase1("noul.tools.technical_tools").extract_paper_math(paper_id, mode=mode)


def analyze_ablation_tables(paper_id):
    return _phase1("noul.tools.technical_tools").analyze_ablation_tables(paper_id)


def extract_prompts_and_decoding(paper_id):
    return _phase1("noul.tools.technical_tools").extract_prompts_and_decoding(paper_id)


def detect_failure_modes(paper_id, inspect_fulltext=True):
    return _phase1("noul.tools.technical_tools").detect_failure_modes(
        paper_id, inspect_fulltext=bool(inspect_fulltext))


def diff_arxiv_versions(arxiv_id, from_version="v1", to_version="v2"):
    return _phase1("noul.tools.technical_tools").diff_arxiv_versions(
        arxiv_id, from_version=from_version, to_version=to_version)


def review_paper(paper_id, domain=None, inspect_fulltext=True):
    return _phase1("noul.tools.review_tools").review_paper(
        paper_id, domain=domain, inspect_fulltext=bool(inspect_fulltext))


def review_paper_scientific(paper_id, domain=None, inspect_fulltext=True):
    return _phase1("noul.tools.review_tools").review_paper_scientific(
        paper_id, domain=domain, inspect_fulltext=bool(inspect_fulltext))


def scaffold_implementation(paper_id, target=None, domain=None):
    return _phase1("noul.tools.scaffold_tools").scaffold_implementation(
        paper_id, target=target, domain=domain)


def scaffold_paper_code(paper_id, target_language=None, domain=None):
    return _phase1("noul.tools.scaffold_tools").scaffold_paper_code(
        paper_id, target_language=target_language, domain=domain)




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
_DOMAIN = {"type": "string", "description": "Scientific domain: computer_science (cs), biomedical (bio, med), "
           "economics (econ) or general_stem (stem). Filters search results / overrides the detected domain."}
TOOLS = {
    "search_papers": (
        "Search peer-reviewed and preprint literature across all scientific disciplines (OpenAlex, PubMed, Semantic Scholar, "
        "Crossref, arXiv, Europe PMC), deduplicated and ranked by study design and citations. Returns abstracts, evidence level "
        "(meta-analysis, systematic-review, rct, review, cohort, empirical, preclinical, other) and a citation/venue band "
        "(`tier`: top, high, standard, emerging — not a JCR quartile) and scientific `domain`. YOU classify each paper's "
        "stance and synthesize.",
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
            "domain": _DOMAIN,
            "clarify": {"type": "boolean", "default": False,
                        "description": "If the query is broad, add `clarification_suggested` questions to the response."},
        },
        ["query"],
        search_papers,
    ),
    "clarify_search_scope": (
        "Clarification loop for broad queries: says whether a query is too broad (needs >= 2 of: 5+ meaningful words, "
        "boolean operators/quoted phrase, time constraint, method + outcome) and returns 3 domain-specific "
        "multiple-choice questions. Ask the user, then call again with `answers` ({\"q1\": option label or 1-based "
        "index, ...}) to get `refined_query` and `recommended_filters` (year_min, study_types, sources, sort) for "
        "search_papers. Skip it when the user already gave a precise query.",
        {"query": S, "answers": {"type": "object"}, "domain": _DOMAIN},
        ["query"],
        clarify_search_scope,
    ),
    "get_paper": (
        "Get full record and abstract of a paper by cached ID, DOI, PMID, OpenAlex ID, arXiv ID, or Semantic Scholar ID. "
        "Includes its scientific `domain`; pass `domain` to override it.",
        {"id": S, "domain": _DOMAIN},
        ["id"],
        get_paper,
    ),
    "detect_paper_domain": (
        "Detect a paper's scientific macro-domain (computer_science, biomedical, economics, general_stem) from arXiv "
        "categories, fields of study, PubMed indexing and title/abstract vocabulary, explaining the signals used. "
        "Use it to pick domain-appropriate evaluation criteria.",
        {"paper_id": S, "domain": _DOMAIN},
        ["paper_id"],
        detect_paper_domain,
    ),
    "scan_paper_artifacts": (
        "Open-artifact radar: find code repositories (GitHub/GitLab/...), model weights (Hugging Face, Civitai, "
        "Kaggle, PyTorch Hub) and datasets (Zenodo, Figshare, OSF, Dataverse, HF datasets) linked from a paper, "
        "verify that the links are alive (HEAD, 5s timeout, max 20 URLs) and flag evasive 'available on request' "
        "statements. Set inspect_fulltext=true to also scan the open-access full text.",
        {"id": S, "verify_http": {"type": "boolean", "default": True},
         "inspect_fulltext": {"type": "boolean", "default": False}},
        ["id"],
        scan_paper_artifacts,
    ),
    "get_paper_references": (
        "Citation graph with influence ranking: papers this paper cites (`backward_citations`) and papers citing it "
        "(`forward_citations`), with title, year, venue, citation_count and Semantic Scholar's `is_influential` flag "
        "(OpenAlex fallback: >= 50 citations). Every returned `paper_id` is cached, so you can chain get_paper, "
        "get_paper_references or download_papers on it.",
        {"paper_id": S, "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
         "direction": {"type": "string", "enum": ["both", "backward", "forward"], "default": "both"},
         "sort_by": {"type": "string", "enum": ["influential", "citations", "year"], "default": "influential"}},
        ["paper_id"],
        get_paper_references,
    ),
    "compare_papers": (
        "Comparative matrix of 1-10 papers (3-5 typical): for each paper extracts the core problem/hypothesis, "
        "methodology (design + domain + method sentence), datasets/benchmarks/population, key results/metrics "
        "(headline finding, %, p-values, AUC, F1, HR, ...) and declared limitations, then returns a Markdown table "
        "plus convergence and divergence summaries. Set inspect_fulltext=true to also mine open-access full text.",
        {"paper_ids": {"type": "array", "items": S}, "inspect_fulltext": {"type": "boolean", "default": False}},
        ["paper_ids"],
        compare_papers,
    ),
    "get_thematic_insights": (
        "Thematic synthesis of 1-20 papers across 3 cross-cutting axes (no isolated per-paper summaries): "
        "`consolidated_consensuses` (shared concepts, benchmarks and highest-level evidence), "
        "`methodological_tensions` (evidence-pyramid gaps, dataset/scale differences, open-artifact asymmetry, "
        "mixed results) and `emerging_future_work` (cross-referenced limitations and future work). Every statement "
        "cites [Author et al., Year]; `citations_map` maps each tag to paper ids.",
        {"paper_ids": {"type": "array", "items": S}, "topic": S},
        ["paper_ids"],
        get_thematic_insights,
    ),
    "generate_research_hypotheses": (
        "Autonomous research gap & hypothesis engine (#18): cross-analyzes 2-15 papers to detect methodological "
        "contradictions (`contradictions_found`) and unexplored synergies where one paper's method mitigates another's "
        "limitation (`unexplored_complementarities`), then formulates 1-`max_hypotheses` structured, testable "
        "`ResearchHypothesis` proposals with `source_papers` (>= 2), `proposed_methodology`, `required_baselines`, "
        "`target_datasets_or_population`, and `evaluation_metrics`.",
        {"paper_ids": {"type": "array", "items": S}, "topic": S, "max_hypotheses": {"type": "integer", "default": 3}},
        ["paper_ids"],
        generate_research_hypotheses,
    ),
    "export_obsidian_notes": (
        "Export papers as atomic Obsidian/Markdown notes (`<Year> - <FirstAuthor> - <ShortTitle>.md`) into `vault_dir` "
        "(inside the workspace; default ./obsidian): YAML frontmatter (title, paper_id, doi, year, authors, venue, "
        "domain, evidence_level, citations, open_access, has_code, has_weights, tags), TL;DR, key findings & "
        "methodology, open artifacts, [[wikilinks]] between the exported notes and a BibTeX block.",
        {"paper_ids": {"type": "array", "items": S},
         "vault_dir": {"type": "string", "description": "Vault folder inside the workspace (relative paths are "
                       "workspace-relative). Default: ./obsidian."},
         "include_related_wikilinks": {"type": "boolean", "default": True}},
        ["paper_ids"],
        export_obsidian_notes,
    ),
    "suggest_boolean_queries": (
        "Dynamic search expansion: mine acronyms (e.g. LoRA, GPTQ) and emerging technical terms from `paper_ids` "
        "(or from cached papers matching `query`) and return 3 ready-to-run boolean queries for search_papers: "
        "[1] precision deepening (AND), [2] synonym/method expansion (OR + AND), [3] noise exclusion (NOT).",
        {"query": S, "paper_ids": {"type": "array", "items": S}},
        [],
        suggest_boolean_queries,
    ),
    "extract_benchmark_tables": (
        "Benchmark table extractor: detects Markdown, LaTeX (tabular) and plain-text/PDF tables plus inline "
        "`Benchmark: value` pairs in the paper's open-access full text (abstract fallback), keeps the exact "
        "model-row x metric-column correspondence, normalizes metric names (gsm-8k -> GSM8K, human-eval -> "
        "HumanEval, acc -> Accuracy, ppl -> Perplexity, ...) and converts values like '84.5%' or '84.5 ± 0.3' to "
        "numbers. Returns `tables` and a cross-table `normalized_summary` (by_metric, best_by_metric).",
        {"paper_id": S},
        ["paper_id"],
        extract_benchmark_tables,
    ),
    "estimate_hardware_specs": (
        "Hardware feasibility / inference footprint: detects the model size (7B, 1.5B, 350M, 8x7B MoE, '671B "
        "parameters with 37B activated', family defaults) and architecture (dense, moe, encoder_only, rnn_ssm), "
        "then computes FP16/INT8/INT4 weights, KV cache (2 x layers x kv_heads x head_dim x context x bytes) and "
        "recommended minimum VRAM (+15% overhead), extracts the authors' reported training hardware (e.g. '64 NVIDIA "
        "A100 80GB GPUs', 'TPU v4-1024') and classifies the operational tier (edge_consumer_gpu <= 16 GB, "
        "workstation <= 48 GB, datacenter_cluster) from the INT4 footprint. Override sizes with total_params_b / "
        "active_params_b.",
        {"paper_id": S,
         "total_params_b": {"type": "number", "description": "Total parameters in billions (overrides detection)."},
         "active_params_b": {"type": "number", "description": "Active parameters per token in billions (MoE)."},
         "context_length": {"type": "integer", "default": 8192, "minimum": 1}},
        ["paper_id"],
        estimate_hardware_specs,
    ),
    "verify_paper_claims": (
        "Evidence grounding with strict attribution: checks each claim (or, when `claims` is empty, the numeric/"
        "comparative sentences auto-extracted from the abstract/text) against the paper's literal text, indexed by "
        "page and table/section. Returns `status` verified (all numbers literally in the same passage), "
        "partially_supported (topic found, some numbers diverge) or extrapolated, with a mandatory citation tag "
        "'[pág. X, Tabela Y: \"...\"]' / '[pág. X, Seção Y: \"...\"]' or an explicit '[EXTRAPOLAÇÃO NÃO LITERAL ...]' "
        "warning. Use it before stating numbers from a paper.",
        {"paper_id": S, "claims": {"type": "array", "items": S}},
        ["paper_id"],
        verify_paper_claims,
    ),
    "extract_paper_math": (
        "Math & pseudocode extractor: finds equations (\\begin{equation|align}, $$..$$, \\[..\\], inline $a = b$, "
        "plain-text 'L_total = ...' / 'Attention(Q, K, V) = ...') and algorithms (\\begin{algorithm}, 'Algorithm N:' "
        "blocks). mode='latex' returns clean delimited LaTeX; mode='python' returns typed Python functions "
        "(`def equation_1(...) -> float` / `def algorithm_1(...) -> Any`, `import math`, original LaTeX in the "
        "docstring), always validated with ast.parse; mode='both' returns both.",
        {"paper_id": S, "mode": {"type": "string", "enum": ["latex", "python", "both"], "default": "latex"}},
        ["paper_id"],
        extract_paper_math,
    ),
    "analyze_ablation_tables": (
        "Ablation analyzer: locates ablation tables/lists (rows 'Full model' / 'Ours' / 'Baseline' and variants "
        "'w/o X', 'without X', '- X', '+ X', 'no X'), takes the full model (else baseline) as reference and computes, "
        "per component and metric, delta and delta_percent, flags is_marginal (|Δ%| < 1%), ranks components by "
        "|Δ%| and emits warnings_and_alerts for marginal components and for a single component accounting for >= 60% "
        "of the total reported gain.",
        {"paper_id": S},
        ["paper_id"],
        analyze_ablation_tables,
    ),
    "extract_prompts_and_decoding": (
        "Prompt & inference protocol extractor: finds prompt templates (after 'System Prompt:', 'User Prompt:', "
        "'Prompt Template:', 'Instruction:', in code fences/quotes, or text with {variable}, {{variable}}, <context>, "
        "\"Let's think step by step\", \"You are an expert\") keeping placeholders verbatim and listing `variables`; "
        "extracts decoding hyperparameters (temperature, top_p, top_k, max_tokens, repetition_penalty, beam_size, "
        "seed, decoding_strategy) with literal evidence, and the JSON output schemas shown in the paper.",
        {"paper_id": S},
        ["paper_id"],
        extract_prompts_and_decoding,
    ),
    "detect_failure_modes": (
        "Failure mode & critical assumption detector: scans Limitations, Error Analysis, Failure Cases, Discussion, "
        "Threats to Validity and Appendix (plus abstract/body) to classify failure modes into long_context, "
        "noise_sensitivity, out_of_distribution, latency_or_compute, sample_or_population_bias and general_limitation "
        "(with severity high/medium/low), extracts critical_assumptions, identifies baseline_superiority_scenarios "
        "(both from text and from benchmark tables where a baseline beats Ours), and extracts operational_limits.",
        {"paper_id": S, "inspect_fulltext": {"type": "boolean", "default": True}},
        ["paper_id"],
        detect_failure_modes,
    ),
    "diff_arxiv_versions": (
        "arXiv temporal version diff analyzer: fetches and compares two revisions of an arXiv preprint (e.g. v1 vs v2), "
        "tracking title and authorship changes, metric and table number deltas (`metric_deltas`), claim attenuations or "
        "strengthenings (`claim_shifts`), newly added baselines or datasets (`new_baselines_or_datasets`) and a "
        "structured `markdown_diff` report.",
        {"arxiv_id": S, "from_version": {"type": "string", "default": "v1"},
         "to_version": {"type": "string", "default": "v2"}},
        ["arxiv_id"],
        diff_arxiv_versions,
    ),
    "review_paper": (
        "Multi-persona peer-review committee (#17, #20): detects the paper's scientific domain (or uses `domain` override) "
        "and runs 3 specialized reviewer personas (NeurIPS/ICLR Theoretical, Skeptical Empirical, and Systems/Reproducibility "
        "for CS/AI; CONSORT/STROBE Clinical Trial Methodologist, Biostatistician, and Translational Safety Reviewer for "
        "Biomedical; Causal Identification Econometrician, Robustness Auditor, and Policy Economist for Economics) "
        "consolidated by a Meta-Reviewer into a PeerReviewReport with verdict, scores, cited flaws/omissions, and questions.",
        {"paper_id": S, "domain": _DOMAIN, "inspect_fulltext": {"type": "boolean", "default": True}},
        ["paper_id"],
        review_paper,
    ),
    "review_paper_scientific": (
        "Alias for review_paper: domain-adaptive 3-persona scientific peer-review committee (#17, #20).",
        {"paper_id": S, "domain": _DOMAIN, "inspect_fulltext": {"type": "boolean", "default": True}},
        ["paper_id"],
        review_paper_scientific,
    ),
    "scaffold_implementation": (
        "Polyglot code & experimental protocol scaffolder (#16, #21): translates a paper's mathematical formulations, "
        "architecture, econometric specification or clinical trial design into executable, ast-validated code + unit tests "
        "(PyTorch `nn.Module` with shape assertions for CS/AI; Statsmodels or R `fixest` panel + robustness matrix for "
        "Economics; CONSORT/STROBE operational checklist + ITT analytical pipeline for Biomedical/STEM).",
        {"paper_id": S, "target": S, "domain": _DOMAIN},
        ["paper_id"],
        scaffold_implementation,
    ),
    "scaffold_paper_code": (
        "Alias for scaffold_implementation (#16, #21): generates PyTorch, Statsmodels/R, or Clinical Protocol code + unit tests.",
        {"paper_id": S, "target_language": S, "domain": _DOMAIN},
        ["paper_id"],
        scaffold_paper_code,
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
