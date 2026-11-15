"""Canonical paper entities."""

from datetime import date
from typing import Any, TypedDict

from pydantic import BaseModel, Field, HttpUrl

from noul.schemas.domain import ScientificDomain


class Paper(TypedDict, total=False):
    """The paper record every provider returns and the cache stores (a plain dict; all keys but the first block
    are optional). `noul.providers.assemble.assemble_paper` builds it."""

    # Always present.
    id: str
    title: str
    authors: list[str]
    year: int | None
    journal: str
    citations: int
    abstract: str
    evidence: str
    impact: str
    tier: str
    open_access: bool
    pdf_url: str | None
    sources: list[str]
    # Identifiers: the ones the source knows.
    doi: str | None
    pmid: str | None
    pmc_id: str | None
    arxiv_id: str | None
    s2_id: str | None
    openalex_id: str | None
    # Classification.
    primary_category: str | None
    fields_of_study: list[str]
    categories: list[str]
    mesh_terms: list[str]
    domain: str
    # Citation graph (OpenAlex ids).
    referenced_works: list[str]
    related_works: list[str]
    # Open artifacts (noul.providers.artifact_radar).
    has_code: bool
    has_weights: bool
    has_dataset: bool
    code_url: str | None
    weights_url: str | None
    dataset_url: str | None
    artifact_urls: list[str]
    evasive_availability_statement: bool
    artifacts: dict[str, Any]
    # Integrity facts (noul.evidence.integrity).
    retraction: dict[str, Any]
    concern: dict[str, Any]
    versions: dict[str, Any]


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
