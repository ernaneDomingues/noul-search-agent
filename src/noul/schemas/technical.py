"""Technical deep-dive contracts (Phase 3: #08 benchmarks, #10 grounding, #11 math, #12 ablation, #13 prompts)."""

from typing import Any

from pydantic import BaseModel, Field

from noul.schemas.evidence import AnalysisResult, Excerpt


class BenchmarkTable(BaseModel):
    table_id: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    normalized_metrics: dict[str, Any] = Field(default_factory=dict)


class GroundedClaim(BaseModel):
    """Where a claim's numbers and terms appear in the paper. Lexical facts only: whether the passage *supports*
    the claim is for the client's model to judge from `best_match` / `other_matches`."""

    claim: str
    claim_untrusted: bool = False  # true when the claim was auto-extracted from the paper (third-party text)
    numbers_checked: list[str] = Field(default_factory=list)
    numbers_found: list[str] = Field(default_factory=list)  # present in the best-matching passage
    numbers_missing: list[str] = Field(default_factory=list)
    keyword_overlap: float = Field(default=0.0, ge=0.0, le=1.0)  # share of the claim's content words in it
    hedging_cues_missing_in_text: list[str] = Field(default_factory=list)  # claim words like "always" absent there
    citation_tag: str = ""  # location only, e.g. "[p. 4, Table 2]"
    best_match: Excerpt | None = None
    other_matches: list[Excerpt] = Field(default_factory=list)


class ClaimsGroundingReport(AnalysisResult):
    auto_extracted_claims: bool = False
    claims: list[GroundedClaim] = Field(default_factory=list)


class MathItem(BaseModel):
    index: int
    kind: str  # "equation" | "algorithm"
    label: str
    latex: str
    python_code: str
    variables: list[str] = Field(default_factory=list)


class AblationEntry(BaseModel):
    component: str
    metric: str
    baseline_value: float
    variant_value: float
    delta: float
    delta_percent: float
    is_marginal: bool = False


class PromptExtractionResult(BaseModel):
    paper_id: str
    prompts: list[dict[str, Any]] = Field(default_factory=list)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)  # decoding
    training_hyperparameters: dict[str, str] = Field(default_factory=dict)  # values as written in the paper
    json_schemas: list[str] = Field(default_factory=list)


class FailureModesReport(AnalysisResult):
    """Candidate failure modes are the `signals` (category = long_context | noise_sensitivity |
    out_of_distribution | latency_or_compute | sample_or_population_bias | general_limitation), each with the
    sentence that triggered it."""

    critical_assumptions: list[Excerpt] = Field(default_factory=list)
    baseline_superiority_scenarios: list[Excerpt] = Field(default_factory=list)
    operational_limits: dict[str, Any] = Field(default_factory=dict)


class VersionChangeItem(BaseModel):
    category: str  # "metric_change" | "claim_attenuation" | "claim_strengthening" | "new_baseline_or_experiment" | "author_or_title_change" | "methodology_change"
    summary: str
    before: str | None = None
    after: str | None = None


class ArxivVersionDiffReport(BaseModel):
    arxiv_base_id: str
    from_version: str
    to_version: str
    title_changed: bool = False
    authors_added: list[str] = Field(default_factory=list)
    authors_removed: list[str] = Field(default_factory=list)
    metric_deltas: list[dict[str, Any]] = Field(default_factory=list)
    claim_shifts: list[VersionChangeItem] = Field(default_factory=list)
    new_baselines_or_datasets: list[str] = Field(default_factory=list)
    markdown_diff: str = ""
