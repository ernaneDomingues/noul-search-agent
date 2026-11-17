"""The tool layer shared by the CLI, the MCP server and the HTTP/OpenAPI server: the 50 tool functions.

The host agent's own LLM does stance classification and synthesis; these tools retrieve, structure, download
and persist evidence. Each submodule holds the tools of one journey and registers them when imported, so the
import order below is the order of `tools/list`: keep it. Public names are re-exported here for callers; to
patch a function, patch it in the submodule that defines it."""

# isort: skip_file
# ruff: noqa: F401
from noul import registry as _registry
from noul.api.common import EVIDENCE, STANCE_ALIASES
from noul.api.search import (
    search_papers,
    clarify_search_scope,
    get_paper,
    detect_paper_domain,
    scan_paper_artifacts,
    get_paper_references,
)
from noul.api.analysis import (
    compare_papers,
    get_thematic_insights,
    generate_research_hypotheses,
    export_obsidian_notes,
    suggest_boolean_queries,
)
from noul.api.technical import (
    extract_benchmark_tables,
    estimate_hardware_specs,
    verify_paper_claims,
    extract_paper_math,
    analyze_ablation_tables,
    extract_prompts_and_decoding,
    detect_failure_modes,
    diff_arxiv_versions,
)
from noul.api.review import review_paper, review_paper_scientific, scaffold_implementation, scaffold_paper_code
from noul.api.authoring import (
    recommend_citations,
    synthesize_related_work,
    generate_manuscript_blueprint,
    audit_manuscript_draft,
    sanitize_bibtex,
    parse_and_draft_rebuttal,
    audit_submission_readiness,
)
from noul.api.library import (
    get_fulltext,
    query_fulltexts,
    citation_graph,
    related_papers,
    download_papers,
    export_papers,
    zotero_push,
)
from noul.api.dossiers import save_research_dossier, list_research_dossiers, get_research_dossier
from noul.api.settings import configure_noul
from noul.api.meter import evidence_meter
from noul.api.projects import (
    project_create,
    project_list,
    project_get,
    project_add_papers,
    project_set_status,
    project_note,
    project_archive,
    project_delete,
    project_markdown,
)

TOOLS = _registry.TOOLS
call = _registry.call
