"""Code and experimental protocol scaffolding data contracts (Phase 5: #16, #21)."""

from noul.schemas._compat import BaseModel, Field


class CodeScaffold(BaseModel):
    paper_id: str
    title: str = ""
    domain: str = "computer_science"
    target: str = "pytorch"  # "pytorch" | "statsmodels" | "r" | "clinical_protocol"
    language: str = "python"  # "python" | "r" | "markdown"
    framework: str = "torch"
    source_code: str
    unit_tests: str
    syntax_valid: bool = True
    shape_verified: bool = True
    dependencies: list[str] = Field(default_factory=list)
    requirements_txt: str = ""
    execution_notes: str = ""
