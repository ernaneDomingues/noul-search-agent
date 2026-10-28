"""Technical deep-dive contracts (Phase 3: #08 benchmarks, #10 grounding, #11 math, #12 ablation, #13 prompts)."""
from typing import Any, Dict, List, Optional

from noul.schemas._compat import BaseModel, Field


class BenchmarkTable(BaseModel):
    table_id: str
    caption: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    normalized_metrics: Dict[str, Any] = Field(default_factory=dict)


class GroundedClaim(BaseModel):
    claim: str
    status: str  # "verified" | "partially_supported" | "extrapolated"
    citation_tag: str  # e.g. '[pág. 4, Tabela 2: "...trecho..."]'
    page: Optional[int] = None
    table_or_section: Optional[str] = None
    verbatim_quote: Optional[str] = None
    numbers_checked: List[str] = Field(default_factory=list)


class MathItem(BaseModel):
    index: int
    kind: str  # "equation" | "algorithm"
    label: str
    latex: str
    python_code: str
    variables: List[str] = Field(default_factory=list)


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
    prompts: List[Dict[str, Any]] = Field(default_factory=list)
    hyperparameters: Dict[str, Any] = Field(default_factory=dict)
    json_schemas: List[str] = Field(default_factory=list)
