"""Analysis and synthesis contracts (Phase 2: #04 comparison, #05 thematic insights, #07 boolean expansion)."""
from typing import Any, Dict, List, Optional

from noul.schemas._compat import BaseModel, Field


class ComparisonDimension(BaseModel):
    paper_id: str
    paper_title: str
    year: Optional[int] = None
    domain: str = "general_stem"
    evidence_level: str = "other"
    core_hypothesis: str
    methodology: str
    benchmarks_used: List[str] = Field(default_factory=list)
    primary_metrics: Dict[str, Any] = Field(default_factory=dict)
    reported_limitations: List[str] = Field(default_factory=list)


class ComparativeMatrixResult(BaseModel):
    papers: List[ComparisonDimension] = Field(default_factory=list)
    divergence_summary: str
    convergence_summary: str
    markdown_table: str


class ThematicInsightsResult(BaseModel):
    topic: str
    consolidated_consensuses: List[str] = Field(default_factory=list)
    methodological_tensions: List[str] = Field(default_factory=list)
    emerging_future_work: List[str] = Field(default_factory=list)
    citations_map: Dict[str, List[str]] = Field(default_factory=dict)


class BooleanQuerySuggestion(BaseModel):
    index: int
    strategy: str
    boolean_query: str
    rationale: str
    target_terms: List[str] = Field(default_factory=list)
