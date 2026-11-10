"""Research project results (v2 Phase 3b-2): `project_*` tools and the noul://project/<slug> resource."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from noul.schemas.evidence import Retraction

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
    search_count: int = 0
    archived: bool = False  # hidden from project_list unless include_archived


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
    retraction: Retraction | None = None  # set when the paper is known to be retracted


class ProjectNote(BaseModel):
    id: int
    paper_id: str | None = None
    text: str
    created_at: int


class ProjectSearch(BaseModel):
    """One search logged with `search_papers(project=...)`. The query is text written by a client model."""

    id: int
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    total_found: int | None = None  # papers matching the query after de-duplication and filters
    result_count: int = 0  # papers on the page returned
    added_count: int = 0  # of those, papers that were not yet in the project (added as candidates)
    created_at: int


class ProjectDetail(ProjectSummary):
    """`project_get`: the project with its papers (included first) and notes. Paper titles are third-party text."""

    papers: list[ProjectPaper] = Field(default_factory=list)
    notes: list[ProjectNote] = Field(default_factory=list)
    searches: list[ProjectSearch] = Field(default_factory=list)  # the most recent 50, oldest first
    untrusted_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # e.g. included papers that are retracted


class ProjectCreated(ProjectSummary):
    created: bool  # False when a project with the same slug already existed (it is returned unchanged)


class ProjectArchived(ProjectSummary):
    """`project_archive`: the project after the change. Title and question are text written by a client model."""

    untrusted_fields: list[str] = Field(default_factory=list)


class ProjectDeleted(BaseModel):
    """`project_delete`: `deleted` is False (and nothing changed) unless the call had `confirm: true`."""

    deleted: bool
    project: ProjectSummary  # what was, or would be, deleted
    preview_token: str | None = None  # from a preview: repeat it, with confirm, to delete what was shown
    untrusted_fields: list[str] = Field(default_factory=list)
    next_step: str | None = None


class ProjectList(BaseModel):
    projects: list[ProjectSummary] = Field(default_factory=list)


class ProjectPapersChange(BaseModel):
    """`project_add_papers`: ids added, ids already in the project (status unchanged) and ids not found."""

    project: ProjectSummary
    added: list[str] = Field(default_factory=list)
    already_present: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    id_map: dict[str, str] = Field(default_factory=dict)  # input id -> id stored in the project


class ZoteroItemResult(BaseModel):
    """One paper in a `zotero_push` result."""

    id: str | None = None
    title: str | None = None
    zotero_key: str | None = None
    reason: str | None = None


class ZoteroPushResult(BaseModel):
    """`zotero_push`: papers created, skipped (already in the library) and failed."""

    zotero_user_id: str | None = None  # the account written to
    created: list[ZoteroItemResult] = Field(default_factory=list)
    skipped: list[ZoteroItemResult] = Field(default_factory=list)
    failed: list[ZoteroItemResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retracted_ids: list[str] = Field(default_factory=list)
    unchecked_ids: list[str] = Field(default_factory=list)  # Crossref did not answer for these
    incomplete_ids: list[str] = Field(default_factory=list)  # stored record may list only the first authors
    untrusted_fields: list[str] = Field(default_factory=list)


class ProjectStatusChange(BaseModel):
    """`project_set_status`: ids updated and ids that are not in the project."""

    project: ProjectSummary
    updated: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ProjectNoteAdded(BaseModel):
    project: ProjectSummary
    note_id: int
