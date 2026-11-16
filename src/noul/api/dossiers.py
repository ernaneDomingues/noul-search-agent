"""Research dossiers: the Markdown/JSON/BibTeX export of a research project in the workspace."""

import json
import re
import time

from noul import errors as _errors
from noul import registry as _registry
from noul import workspace as _workspace
from noul.api import common as _common
from noul.library import export as _exporting
from noul.library import store as _store
from noul.net import cache as _cache
from noul.schemas import tool_inputs as _inputs


def _slugify(text):
    # The same slug names the project (store.create_project), so both go through one function.
    return _store.slugify(text or "research", fallback="research-session")


def _dossier_slug(topic, parent):
    """The topic's slug, or the one an earlier version gave it when that dossier is already in `parent`.

    Before 2.1.1 accents were dropped instead of transliterated ("Café" -> "caf"); saving such a topic again
    must update the existing dossier and project, not start a second one."""
    slug = _slugify(topic)
    legacy = re.sub(r"[^a-z0-9]+", "-", (topic or "research").lower()).strip("-")[:60].strip("-")
    if legacy and legacy != slug and (parent / legacy / "papers.json").is_file() and not (parent / slug).exists():
        return legacy
    return slug


@_registry.tool(
    "save_research_dossier",
    description=(
        "Save a persistent research dossier (README.md, papers.json, references.bib) in `output_dir` (inside "
        "the workspace; default: configured research_dir, ./research) so the user can converse over the "
        "research documentation. It is the export of the research project with the same slug (created if "
        "needed, in the user's Noul data folder): cited papers not yet in the project are added as included."
    ),
    input=_inputs.SaveResearchDossierInput,
    writes=True,
    network=False,
    profiles=("research",),
    destructive=True,
    idempotent=True,
)
def save_research_dossier(topic, synthesis_markdown, paper_ids=None, output_dir=None):
    """Save a structured research dossier (README.md, papers.json, references.bib) so the user can
    converse over the research documentation and revisit it across sessions."""
    parent = _workspace.safe_output_dir(output_dir, "research")
    slug = _dossier_slug(topic, parent)
    base, md_path, json_path, bib_path = _common._dossier_paths(parent / slug)
    base.mkdir(parents=True, exist_ok=True)

    papers, left_out = [], []
    for pid in paper_ids or []:
        try:
            papers.append(_common._resolve(pid))
        except Exception:
            left_out.append(str(pid))

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
        "papers": [_common._slim(p, i + 1, abstract_chars=10000) for i, p in enumerate(papers)],
    }
    json_path.write_text(json.dumps(dossier_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if papers:
        bib_txt = _exporting.export(papers, "bibtex")[0]
        bib_path.write_text(bib_txt + "\n", encoding="utf-8")

    # Also register dossier in SQLite cache for MCP resource discovery
    index = _cache.cache_get("dossiers:index", max_age=0) or {}
    index[slug] = {
        "slug": slug,
        "topic": topic,
        "path": str(base),
        "readme_path": str(md_path),
        "paper_count": len(papers),
        "updated_at": int(time.time()),
    }
    _cache.cache_set("dossiers:index", index)

    # The dossier is an export of the research project with the same slug: cited papers not yet in the project
    # are added as included; papers already there keep the status the user gave them.
    _common._import_dossiers_once()
    project = _common._project_call(_store.create_project, topic, slug=slug)
    kept = []
    if papers:
        records = [_common._paper_record(p) for p in papers][: _store.MAX_IDS]
        change = _store.add_papers(slug, records, status="included", reason="Cited in the research dossier")
        kept = change["already_present"]

    return {
        "slug": slug,
        "project": project["slug"],
        "project_papers_unchanged": kept,
        "topic": topic,
        "directory": str(base),
        "readme_path": str(md_path),
        "papers_json_path": str(json_path),
        "bibtex_path": str(bib_path) if papers else None,
        "paper_count": len(papers),
        "papers_left_out": left_out,  # ids that could not be resolved (unknown, or their source did not answer)
    }


@_registry.tool(
    "list_research_dossiers",
    description=("List saved research dossiers available for follow-up conversation."),
    input=_inputs.ListResearchDossiersInput,
    folder_args=("output_dir",),
    network=False,
    profiles=("research",),
)
def list_research_dossiers(output_dir=None):
    """List saved research dossiers from both the workspace directory and the index."""
    base = _workspace.safe_output_dir(output_dir, "research")
    items = {}
    index = _cache.cache_get("dossiers:index", max_age=0) or {}
    for slug, info in index.items():
        try:
            sub, readme, _, _ = _common._dossier_paths(info["path"])
        except _errors.HTTPError:
            continue
        if readme.is_file():
            items[slug] = {**info, "path": str(sub), "readme_path": str(readme)}

    if base.exists() and base.is_dir():
        for sub in sorted(base.iterdir()):
            try:
                directory, readme, papers_path, _ = _common._dossier_paths(sub)
            except _errors.HTTPError:
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


@_registry.tool(
    "get_research_dossier",
    description=("Load a saved research dossier (Markdown report and paper metadata) by slug or topic."),
    input=_inputs.GetResearchDossierInput,
    folder_args=("output_dir",),
    network=False,
    profiles=("research",),
)
def get_research_dossier(slug, output_dir=None):
    """Load a saved research dossier (Markdown synthesis + cached paper records) to continue a discussion."""
    listing = list_research_dossiers(output_dir=output_dir)
    match = next((d for d in listing["dossiers"] if d["slug"] == slug or d["topic"].lower() == slug.lower()), None)
    if not match:
        raise _errors.HTTPError(404, f"Research dossier '{slug}' not found")
    sub, readme, papers_path, _ = _common._dossier_paths(match["path"])
    md_text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    papers = []
    if papers_path.exists():
        try:
            data = json.loads(papers_path.read_text(encoding="utf-8"))
            papers = data.get("papers") or []
            for p in papers:
                if p.get("id"):
                    _cache.put_paper(p["id"], p)
        except Exception:
            pass
    return {
        "slug": match["slug"],
        "topic": match["topic"],
        "path": str(sub),
        "markdown": md_text,
        "papers": papers,
    }
