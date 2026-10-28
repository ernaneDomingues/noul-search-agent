"""Canonical paper entities (docs/TECHNICAL_SPECIFICATION.md §3.2)."""
from datetime import date
from typing import List, Optional

from noul.schemas._compat import BaseModel, Field, HttpUrl
from noul.schemas.domain import ScientificDomain


class PaperReference(BaseModel):
    paper_id: str
    title: str
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    citation_count: int = Field(default=0, ge=0)
    is_influential: bool = False
    doi: Optional[str] = None
    url: Optional[str] = None


class ArtifactStatus(BaseModel):
    has_code: bool = False
    code_url: Optional[HttpUrl] = None
    code_verified: bool = False
    has_weights: bool = False
    weights_url: Optional[HttpUrl] = None
    weights_verified: bool = False
    has_dataset: bool = False
    dataset_url: Optional[HttpUrl] = None
    dataset_verified: bool = False
    urls: List[str] = Field(default_factory=list)
    evasive_availability_statement: bool = False


class UnifiedPaper(BaseModel):
    id: str
    title: str
    abstract: str = ""
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    published_date: Optional[date] = None
    venue: Optional[str] = None
    domain: ScientificDomain = ScientificDomain.GENERAL_STEM
    primary_category: str = "general"
    evidence_level: str = "other"
    citation_count: int = Field(default=0, ge=0)
    open_access: bool = False
    pdf_url: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    arxiv_id: Optional[str] = None
    artifacts: ArtifactStatus = Field(default_factory=ArtifactStatus)
    backward_citations: List[PaperReference] = Field(default_factory=list)
    forward_citations: List[PaperReference] = Field(default_factory=list)
    bibtex: Optional[str] = None
