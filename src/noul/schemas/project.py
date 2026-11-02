"""Research project results (v2 Phase 3b-2): `project_*` tools and the noul://project/<slug> resource."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["candidate", "included", "excluded"]


class StatusCounts(BaseModel):
    candidate: int = 0
    included: int = 0
    excluded: int = 0


class ProjectSummary(BaseModel):
    """A research project: a question and the papers considered for it."""

    id: int
    slug: str
    title: str
    question: str | None = None
    created_at: int
    updated_at: int
    counts: StatusCounts
    note_count: int = 0


class ProjectPaper(BaseModel):
    id: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    evidence: str | None = None
    status: Status
    reason: str | None = None
    added_at: int
    updated_at: int


class ProjectNote(BaseModel):
    id: int
    paper_id: str | None = None
    text: str
    created_at: int


class ProjectDetail(ProjectSummary):
    """`project_get`: the project with its papers (included first) and notes. Paper titles are third-party text."""

    papers: list[ProjectPaper] = Field(default_factory=list)
    notes: list[ProjectNote] = Field(default_factory=list)
    untrusted_fields: list[str] = Field(default_factory=list)


class ProjectCreated(ProjectSummary):
    created: bool  # False when a project with the same slug already existed (it is returned unchanged)


class ProjectList(BaseModel):
    projects: list[ProjectSummary] = Field(default_factory=list)


class ProjectPapersChange(BaseModel):
    """`project_add_papers`: ids added, ids already in the project (status unchanged) and ids not found."""

    project: ProjectSummary
    added: list[str] = Field(default_factory=list)
    already_present: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    id_map: dict[str, str] = Field(default_factory=dict)  # input id -> id stored in the project


class ProjectStatusChange(BaseModel):
    """`project_set_status`: ids updated and ids that are not in the project."""

    project: ProjectSummary
    updated: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ProjectNoteAdded(BaseModel):
    project: ProjectSummary
    note_id: int
