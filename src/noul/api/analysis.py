"""Comparing papers: comparison matrix, thematic insights, hypotheses, Obsidian notes and boolean queries."""

from noul import registry as _registry
from noul.schemas import tool_inputs as _inputs
from noul.tools import comparative_tools as _comparative_tools
from noul.tools import search_tools as _search_tools


@_registry.tool(
    "compare_papers",
    description=(
        "Comparative matrix of 1-10 papers (3-5 typical): for each paper extracts the core "
        "problem/hypothesis, methodology (design + domain + method sentence), datasets/benchmarks/population, "
        "key results/metrics (headline finding, %, p-values, AUC, F1, HR, ...) and declared limitations, then "
        "returns a Markdown table plus convergence and divergence summaries. Set inspect_fulltext=true to "
        "also mine open-access full text."
    ),
    input=_inputs.ComparePapersInput,
    profiles=("review",),
)
def compare_papers(paper_ids, inspect_fulltext=False):
    return _comparative_tools.compare_papers(paper_ids, inspect_fulltext=inspect_fulltext)


@_registry.tool(
    "get_thematic_insights",
    description=(
        "Thematic synthesis of 1-20 papers across 3 cross-cutting axes (no isolated per-paper summaries): "
        "`consolidated_consensuses` (shared concepts, benchmarks and highest-level evidence), "
        "`methodological_tensions` (evidence-pyramid gaps, dataset/scale differences, open-artifact "
        "asymmetry, mixed results) and `emerging_future_work` (cross-referenced limitations and future work). "
        "Every statement cites [Author et al., Year]; `citations_map` maps each tag to paper ids."
    ),
    input=_inputs.GetThematicInsightsInput,
    profiles=("review",),
)
def get_thematic_insights(paper_ids, topic=None):
    return _comparative_tools.get_thematic_insights(paper_ids, topic=topic)


@_registry.tool(
    "generate_research_hypotheses",
    description=(
        "Autonomous research gap & hypothesis engine: cross-analyzes 2-15 papers to detect "
        "methodological contradictions (`contradictions_found`) and unexplored synergies where one paper's "
        "method mitigates another's limitation (`unexplored_complementarities`), then formulates "
        "1-`max_hypotheses` structured, testable `ResearchHypothesis` proposals with `source_papers` (>= 2), "
        "`proposed_methodology`, `required_baselines`, `target_datasets_or_population`, and "
        "`evaluation_metrics`."
    ),
    input=_inputs.GenerateResearchHypothesesInput,
    profiles=("review",),
)
def generate_research_hypotheses(paper_ids, topic=None, max_hypotheses=3):
    return _comparative_tools.generate_research_hypotheses(paper_ids, topic=topic, max_hypotheses=max_hypotheses)


@_registry.tool(
    "export_obsidian_notes",
    description=(
        "Export papers as atomic Obsidian/Markdown notes (`<Year> - <FirstAuthor> - <ShortTitle>.md`) into "
        "`vault_dir` (inside the workspace; default ./obsidian): YAML frontmatter (title, paper_id, doi, "
        "year, authors, venue, domain, evidence_level, citations, open_access, has_code, has_weights, tags), "
        "TL;DR, key findings & methodology, open artifacts, [[wikilinks]] between the exported notes and a "
        "BibTeX block."
    ),
    input=_inputs.ExportObsidianNotesInput,
    writes=True,
    profiles=("write",),
    destructive=True,
    idempotent=True,
    title="Export Obsidian notes",
)
def export_obsidian_notes(paper_ids, vault_dir=None, include_related_wikilinks=True):
    return _comparative_tools.export_obsidian_notes(
        paper_ids, vault_dir=vault_dir, include_related_wikilinks=include_related_wikilinks
    )


@_registry.tool(
    "suggest_boolean_queries",
    description=(
        "Dynamic search expansion: mine acronyms (e.g. LoRA, GPTQ) and emerging technical terms from "
        "`paper_ids` (or from cached papers matching `query`) and return 3 ready-to-run boolean queries for "
        "search_papers: [1] precision deepening (AND), [2] synonym/method expansion (OR + AND), [3] noise "
        "exclusion (NOT)."
    ),
    input=_inputs.SuggestBooleanQueriesInput,
    profiles=("research",),
    network=False,
)
def suggest_boolean_queries(query="", paper_ids=None):
    return _search_tools.suggest_boolean_queries(query=query, paper_ids=paper_ids)
