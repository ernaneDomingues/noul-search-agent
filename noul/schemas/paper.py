"""Canonical paper entities (docs/TECHNICAL_SPECIFICATION.md §3.2)."""

from datetime import date

from noul.schemas._compat import BaseModel, Field, HttpUrl
from noul.schemas.domain import ScientificDomain


class PaperReference(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    citation_count: int = Field(default=0, ge=0)
    is_influential: bool = False
    doi: str | None = None
    url: str | None = None


class ArtifactStatus(BaseModel):
    has_code: bool = False
    code_url: HttpUrl | None = None
    code_verified: bool = False
    has_weights: bool = False
    weights_url: HttpUrl | None = None
    weights_verified: bool = False
    has_dataset: bool = False
    dataset_url: HttpUrl | None = None
    dataset_verified: bool = False
    urls: list[str] = Field(default_factory=list)
    evasive_availability_statement: bool = False


class UnifiedPaper(BaseModel):
    id: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    published_date: date | None = None
    venue: str | None = None
    domain: ScientificDomain = ScientificDomain.GENERAL_STEM
    primary_category: str = "general"
    evidence_level: str = "other"
    citation_count: int = Field(default=0, ge=0)
    open_access: bool = False
    pdf_url: str | None = None
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    artifacts: ArtifactStatus = Field(default_factory=ArtifactStatus)
    backward_citations: list[PaperReference] = Field(default_factory=list)
    forward_citations: list[PaperReference] = Field(default_factory=list)
    bibtex: str | None = None
