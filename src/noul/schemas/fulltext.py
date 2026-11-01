"""Results of the full-text reading tools (v2 Phase 3b): passages retrieved with BM25, with section and page."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from noul.schemas.evidence import Excerpt


class FulltextResult(BaseModel):
    """`get_fulltext`: up to 6 passages relevant to `question` (or the first passages without one). When no open
    full text is available, `abstract_fallback` holds the abstract instead."""

    id: str
    title: str | None = None
    available: bool
    source: str | None = None
    url: str | None = None
    method: Literal["retrieval"] = "retrieval"
    question: str = ""
    excerpts: list[Excerpt] = Field(default_factory=list)
    abstract_fallback: str | None = None
    untrusted_fields: list[str] = Field(default_factory=list)


class PaperPassages(BaseModel):
    id: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    evidence: str | None = None
    content_source: Literal["fulltext", "abstract_only"] | None = None
    excerpts: list[Excerpt] = Field(default_factory=list)
    error: str | None = None


class QueryFulltextsResult(BaseModel):
    """`query_fulltexts`: the best passages per paper for one question."""

    question: str
    method: Literal["retrieval"] = "retrieval"
    results: list[PaperPassages] = Field(default_factory=list)
    untrusted_fields: list[str] = Field(default_factory=list)
