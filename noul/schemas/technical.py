"""Technical deep-dive contracts (Phase 3: #08 benchmarks, #10 grounding, #11 math, #12 ablation, #13 prompts)."""

from typing import Any

from noul.schemas._compat import BaseModel, Field


class BenchmarkTable(BaseModel):
    table_id: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    normalized_metrics: dict[str, Any] = Field(default_factory=dict)


class GroundedClaim(BaseModel):
    claim: str
    status: str  # "verified" | "partially_supported" | "extrapolated"
    citation_tag: str  # e.g. '[pág. 4, Tabela 2: "...trecho..."]'
    page: int | None = None
    table_or_section: str | None = None
    verbatim_quote: str | None = None
    numbers_checked: list[str] = Field(default_factory=list)


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
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    json_schemas: list[str] = Field(default_factory=list)


class FailureModeEntry(BaseModel):
    category: str  # "long_context" | "noise_sensitivity" | "out_of_distribution" | "latency_or_compute" | "sample_or_population_bias" | "general_limitation"
    severity: str = "medium"  # "high" | "medium" | "low"
    description: str
    trigger_condition: str = ""
    evidence_quote: str = ""


class FailureModesReport(BaseModel):
    paper_id: str
    title: str = ""
    domain: str = "general_stem"
    failure_modes: list[FailureModeEntry] = Field(default_factory=list)
    critical_assumptions: list[str] = Field(default_factory=list)
    baseline_superiority_scenarios: list[str] = Field(default_factory=list)
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
