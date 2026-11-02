"""Analysis and synthesis contracts (Phase 2: #04 comparison, #05 thematic insights, #07 boolean expansion)."""

from typing import Any

from pydantic import BaseModel, Field

from noul.schemas.evidence import Excerpt


class ComparisonDimension(BaseModel):
    paper_id: str
    paper_title: str
    year: int | None = None
    domain: str = "general_stem"
    evidence_level: str = "other"
    core_hypothesis: str
    methodology: str
    benchmarks_used: list[str] = Field(default_factory=list)
    primary_metrics: dict[str, Any] = Field(default_factory=dict)
    reported_limitations: list[str] = Field(default_factory=list)


class ComparativeMatrixResult(BaseModel):
    papers: list[ComparisonDimension] = Field(default_factory=list)
    divergence_summary: str
    convergence_summary: str
    markdown_table: str


class ThematicInsightsResult(BaseModel):
    topic: str
    consolidated_consensuses: list[str] = Field(default_factory=list)
    methodological_tensions: list[str] = Field(default_factory=list)
    emerging_future_work: list[str] = Field(default_factory=list)
    citations_map: dict[str, list[str]] = Field(default_factory=dict)


class BooleanQuerySuggestion(BaseModel):
    index: int
    strategy: str
    boolean_query: str
    rationale: str
    target_terms: list[str] = Field(default_factory=list)


class ResearchHypothesis(BaseModel):
    index: int
    title: str
    rationale: str
    source_papers: list[str] = Field(default_factory=list)
    proposed_methodology: str
    required_baselines: list[str] = Field(default_factory=list)
    target_datasets_or_population: list[str] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)


class ResearchGapReport(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    contradictions_found: list[str] = Field(default_factory=list)
    unexplored_complementarities: list[str] = Field(default_factory=list)
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list)
    # The paper sentences (assumptions, candidate failure modes) quoted inside the prose above, as excerpts.
    evidence: list[Excerpt] = Field(default_factory=list)
    untrusted_fields: list[str] = Field(default_factory=list)
    markdown_report: str = ""
