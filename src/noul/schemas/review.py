"""Peer-review committee contract (v2 Phase 3: evidence, not a verdict).

Each reviewer persona of the domain rubric (CS / biomedical / economics / general STEM) contributes heuristic
`signals`; the client's model weighs them with the rubric and writes the review and the recommendation."""

from pydantic import BaseModel, Field

from noul.schemas.evidence import AnalysisResult


class ReviewPerspective(BaseModel):
    role: str
    focus: str = ""
    signal_ids: list[str] = Field(default_factory=list)
    questions_for_authors: list[str] = Field(default_factory=list)


class PeerReviewReport(AnalysisResult):
    rubric_applied: str
    perspectives: list[ReviewPerspective] = Field(default_factory=list)
    missing_experiments_or_controls: list[str] = Field(default_factory=list)
    questions_for_authors: list[str] = Field(default_factory=list)
