"""Input models of the MCP/CLI tools (one per tool). Generated once from the v1 hand-written JSON schemas.

These models are the source of truth for tool arguments: `noul.registry` validates calls with them and derives the
MCP `inputSchema` from them (`registry.input_schema`). Edit the model, never a JSON schema by hand."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ToolInput(_StrictModel):
    """Base class of every tool input model."""


class EvidenceMeterItemsItem(_StrictModel):
    id: str | None = None
    stance: str = Field(..., description="supportive | inconclusive | contrary")
    evidence: str | None = Field(None, description="Optional study design if the paper is not cached")


class SearchPapersInput(_ToolInput):
    """Arguments of `search_papers`."""

    query: str
    sources: list[Literal["openalex", "pubmed", "s2", "crossref", "arxiv", "europepmc"]] | None = Field(
        None, json_schema_extra={"enum_order": ["openalex", "pubmed", "s2", "crossref", "arxiv", "europepmc"]}
    )
    year_min: int | None = None
    study_types: (
        list[
            Literal[
                "meta-analysis", "systematic-review", "rct", "review", "cohort", "empirical", "preclinical", "other"
            ]
        ]
        | None
    ) = Field(
        None,
        json_schema_extra={
            "enum_order": [
                "meta-analysis",
                "systematic-review",
                "rct",
                "review",
                "cohort",
                "empirical",
                "preclinical",
                "other",
            ]
        },
    )
    min_citations: int | None = None
    open_access: bool | None = None
    sort: Literal["relevance", "date", "citations"] | None = Field(
        None, json_schema_extra={"enum_order": ["relevance", "date", "citations"]}
    )
    limit: int | None = 15
    offset: int | None = 0
    exclude_ids: list[str] | None = None
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )
    clarify: bool | None = Field(
        False, description="If the query is broad, add `clarification_suggested` questions to the response."
    )


class ClarifySearchScopeInput(_ToolInput):
    """Arguments of `clarify_search_scope`."""

    query: str
    answers: dict[str, Any] | None = None
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )


class GetPaperInput(_ToolInput):
    """Arguments of `get_paper`."""

    id: str
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )


class DetectPaperDomainInput(_ToolInput):
    """Arguments of `detect_paper_domain`."""

    paper_id: str
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )


class ScanPaperArtifactsInput(_ToolInput):
    """Arguments of `scan_paper_artifacts`."""

    id: str
    verify_http: bool | None = True
    inspect_fulltext: bool | None = False


class GetPaperReferencesInput(_ToolInput):
    """Arguments of `get_paper_references`."""

    paper_id: str
    limit: int | None = Field(10, ge=1, le=50)
    direction: Literal["both", "backward", "forward"] | None = Field(
        "both", json_schema_extra={"enum_order": ["both", "backward", "forward"]}
    )
    sort_by: Literal["influential", "citations", "year"] | None = Field(
        "influential", json_schema_extra={"enum_order": ["influential", "citations", "year"]}
    )


class ComparePapersInput(_ToolInput):
    """Arguments of `compare_papers`."""

    paper_ids: list[str]
    inspect_fulltext: bool | None = False


class GetThematicInsightsInput(_ToolInput):
    """Arguments of `get_thematic_insights`."""

    paper_ids: list[str]
    topic: str | None = None


class GenerateResearchHypothesesInput(_ToolInput):
    """Arguments of `generate_research_hypotheses`."""

    paper_ids: list[str]
    topic: str | None = None
    max_hypotheses: int | None = 3


class ExportObsidianNotesInput(_ToolInput):
    """Arguments of `export_obsidian_notes`."""

    paper_ids: list[str]
    vault_dir: str | None = Field(
        None,
        description="Vault folder inside the workspace (relative paths are workspace-relative). Default: ./obsidian.",
    )
    include_related_wikilinks: bool | None = True


class SuggestBooleanQueriesInput(_ToolInput):
    """Arguments of `suggest_boolean_queries`."""

    query: str | None = None
    paper_ids: list[str] | None = None


class ExtractBenchmarkTablesInput(_ToolInput):
    """Arguments of `extract_benchmark_tables`."""

    paper_id: str


class EstimateHardwareSpecsInput(_ToolInput):
    """Arguments of `estimate_hardware_specs`."""

    paper_id: str
    total_params_b: float | None = Field(None, description="Total parameters in billions (overrides detection).")
    active_params_b: float | None = Field(None, description="Active parameters per token in billions (MoE).")
    context_length: int | None = Field(8192, ge=1)


class VerifyPaperClaimsInput(_ToolInput):
    """Arguments of `verify_paper_claims`."""

    paper_id: str
    claims: list[str] | None = None


class ExtractPaperMathInput(_ToolInput):
    """Arguments of `extract_paper_math`."""

    paper_id: str
    mode: Literal["latex", "python", "both"] | None = Field(
        "latex", json_schema_extra={"enum_order": ["latex", "python", "both"]}
    )


class AnalyzeAblationTablesInput(_ToolInput):
    """Arguments of `analyze_ablation_tables`."""

    paper_id: str


class ExtractPromptsAndDecodingInput(_ToolInput):
    """Arguments of `extract_prompts_and_decoding`."""

    paper_id: str


class DetectFailureModesInput(_ToolInput):
    """Arguments of `detect_failure_modes`."""

    paper_id: str
    inspect_fulltext: bool | None = True


class DiffArxivVersionsInput(_ToolInput):
    """Arguments of `diff_arxiv_versions`."""

    arxiv_id: str
    from_version: str | None = "v1"
    to_version: str | None = "v2"


class ReviewPaperInput(_ToolInput):
    """Arguments of `review_paper`."""

    paper_id: str
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )
    inspect_fulltext: bool | None = True


class ReviewPaperScientificInput(_ToolInput):
    """Arguments of `review_paper_scientific`."""

    paper_id: str
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )
    inspect_fulltext: bool | None = True


class ScaffoldImplementationInput(_ToolInput):
    """Arguments of `scaffold_implementation`."""

    paper_id: str
    target: str | None = None
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )


class ScaffoldPaperCodeInput(_ToolInput):
    """Arguments of `scaffold_paper_code`."""

    paper_id: str
    target_language: str | None = None
    domain: str | None = Field(
        None,
        description="Scientific domain: computer_science (cs), biomedical (bio, med), economics (econ) or general_stem (stem). Filters search results / overrides the detected domain.",
    )


class RecommendCitationsInput(_ToolInput):
    """Arguments of `recommend_citations`."""

    draft_text: str
    max_per_claim: int | None = Field(2, ge=1, le=5)
    citation_style: Literal["latex", "markdown"] | None = Field(
        "latex", json_schema_extra={"enum_order": ["latex", "markdown"]}
    )


class SynthesizeRelatedWorkInput(_ToolInput):
    """Arguments of `synthesize_related_work`."""

    paper_ids: list[str]
    proposal: str
    citation_style: Literal["latex", "numeric"] | None = Field(
        "latex", json_schema_extra={"enum_order": ["latex", "numeric"]}
    )
    output_format: Literal["markdown", "latex"] | None = Field(
        "markdown", json_schema_extra={"enum_order": ["markdown", "latex"]}
    )


class GenerateManuscriptBlueprintInput(_ToolInput):
    """Arguments of `generate_manuscript_blueprint`."""

    topic: str
    venue: str | None = "neurips"
    hypothesis: str | None = None
    key_findings: list[str] | None = None
    output_format: Literal["markdown", "latex"] | None = Field(
        "markdown", json_schema_extra={"enum_order": ["markdown", "latex"]}
    )


class AuditManuscriptDraftInput(_ToolInput):
    """Arguments of `audit_manuscript_draft`."""

    draft_input: str
    venue: str | None = "neurips"
    output_format: Literal["markdown", "latex"] | None = Field(
        "markdown", json_schema_extra={"enum_order": ["markdown", "latex"]}
    )


class SanitizeBibtexInput(_ToolInput):
    """Arguments of `sanitize_bibtex`."""

    bibtex_input: str
    update_preprints: bool | None = True
    output_path: str | None = None


class ParseAndDraftRebuttalInput(_ToolInput):
    """Arguments of `parse_and_draft_rebuttal`."""

    reviews_input: str
    paper_id: str | None = None
    draft_input: str | None = None
    output_format: Literal["markdown", "latex"] | None = Field(
        "markdown", json_schema_extra={"enum_order": ["markdown", "latex"]}
    )


class AuditSubmissionReadinessInput(_ToolInput):
    """Arguments of `audit_submission_readiness`."""

    draft_path: str
    venue: str | None = "neurips"


class GetFulltextInput(_ToolInput):
    """Arguments of `get_fulltext`."""

    id: str
    question: str | None = None


class QueryFulltextsInput(_ToolInput):
    """Arguments of `query_fulltexts`."""

    ids: list[str]
    question: str
    max_chunks_per_paper: int | None = 4


class CitationGraphInput(_ToolInput):
    """Arguments of `citation_graph`."""

    id: str


class RelatedPapersInput(_ToolInput):
    """Arguments of `related_papers`."""

    id: str
    relation: Literal["all", "similar", "references", "citing"] | None = Field(
        "all", json_schema_extra={"enum_order": ["all", "similar", "references", "citing"]}
    )
    study_types: (
        list[
            Literal[
                "meta-analysis", "systematic-review", "rct", "review", "cohort", "empirical", "preclinical", "other"
            ]
        ]
        | None
    ) = Field(
        None,
        json_schema_extra={
            "enum_order": [
                "meta-analysis",
                "systematic-review",
                "rct",
                "review",
                "cohort",
                "empirical",
                "preclinical",
                "other",
            ]
        },
    )
    year_min: int | None = None
    limit: int | None = 15


class DownloadPapersInput(_ToolInput):
    """Arguments of `download_papers`."""

    ids: list[str]
    output_dir: str | None = Field(
        None,
        description="Folder inside the workspace (relative paths are workspace-relative). Default: configured download_dir (./papers).",
    )


class SaveResearchDossierInput(_ToolInput):
    """Arguments of `save_research_dossier`."""

    topic: str
    synthesis_markdown: str
    paper_ids: list[str] | None = None
    output_dir: str | None = Field(
        None,
        description="Folder inside the workspace (relative paths are workspace-relative). Default: configured research_dir (./research).",
    )


class ListResearchDossiersInput(_ToolInput):
    """Arguments of `list_research_dossiers`."""

    output_dir: str | None = Field(
        None,
        description="Folder inside the workspace (relative paths are workspace-relative). Default: configured research_dir (./research).",
    )


class GetResearchDossierInput(_ToolInput):
    """Arguments of `get_research_dossier`."""

    slug: str
    output_dir: str | None = Field(
        None,
        description="Folder inside the workspace (relative paths are workspace-relative). Default: configured research_dir (./research).",
    )


class ExportPapersInput(_ToolInput):
    """Arguments of `export_papers`."""

    ids: list[str]
    format: Literal["bibtex", "ris", "csv"] | None = Field(
        None, json_schema_extra={"enum_order": ["bibtex", "ris", "csv"]}
    )


class EvidenceMeterInput(_ToolInput):
    """Arguments of `evidence_meter`."""

    # Items are validated by evidence_meter itself (v1 behaviour: bad items are reported in `skipped`, extra keys
    # such as a title or rationale are ignored); EvidenceMeterItemsItem documents the advertised item schema.
    items: list[Any] = Field(..., json_schema_extra={"schema_items": "EvidenceMeterItemsItem"})


class ConfigureNoulInput(_ToolInput):
    """Arguments of `configure_noul`."""

    email: str | None = None
    workspace: str | None = None
    download_dir: str | None = None
    research_dir: str | None = None
    clear: bool | None = False


INPUT_MODELS: dict[str, type[_ToolInput]] = {
    "search_papers": SearchPapersInput,
    "clarify_search_scope": ClarifySearchScopeInput,
    "get_paper": GetPaperInput,
    "detect_paper_domain": DetectPaperDomainInput,
    "scan_paper_artifacts": ScanPaperArtifactsInput,
    "get_paper_references": GetPaperReferencesInput,
    "compare_papers": ComparePapersInput,
    "get_thematic_insights": GetThematicInsightsInput,
    "generate_research_hypotheses": GenerateResearchHypothesesInput,
    "export_obsidian_notes": ExportObsidianNotesInput,
    "suggest_boolean_queries": SuggestBooleanQueriesInput,
    "extract_benchmark_tables": ExtractBenchmarkTablesInput,
    "estimate_hardware_specs": EstimateHardwareSpecsInput,
    "verify_paper_claims": VerifyPaperClaimsInput,
    "extract_paper_math": ExtractPaperMathInput,
    "analyze_ablation_tables": AnalyzeAblationTablesInput,
    "extract_prompts_and_decoding": ExtractPromptsAndDecodingInput,
    "detect_failure_modes": DetectFailureModesInput,
    "diff_arxiv_versions": DiffArxivVersionsInput,
    "review_paper": ReviewPaperInput,
    "review_paper_scientific": ReviewPaperScientificInput,
    "scaffold_implementation": ScaffoldImplementationInput,
    "scaffold_paper_code": ScaffoldPaperCodeInput,
    "recommend_citations": RecommendCitationsInput,
    "synthesize_related_work": SynthesizeRelatedWorkInput,
    "generate_manuscript_blueprint": GenerateManuscriptBlueprintInput,
    "audit_manuscript_draft": AuditManuscriptDraftInput,
    "sanitize_bibtex": SanitizeBibtexInput,
    "parse_and_draft_rebuttal": ParseAndDraftRebuttalInput,
    "audit_submission_readiness": AuditSubmissionReadinessInput,
    "get_fulltext": GetFulltextInput,
    "query_fulltexts": QueryFulltextsInput,
    "citation_graph": CitationGraphInput,
    "related_papers": RelatedPapersInput,
    "download_papers": DownloadPapersInput,
    "save_research_dossier": SaveResearchDossierInput,
    "list_research_dossiers": ListResearchDossiersInput,
    "get_research_dossier": GetResearchDossierInput,
    "export_papers": ExportPapersInput,
    "evidence_meter": EvidenceMeterInput,
    "configure_noul": ConfigureNoulInput,
}
