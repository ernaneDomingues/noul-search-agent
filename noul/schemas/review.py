"""Peer-review multi-persona data contracts (Phase 5: #17, #20)."""

from noul.schemas._compat import BaseModel, Field
from noul.schemas.domain import ReviewVerdict


class PersonaEvaluation(BaseModel):
    persona_role: str
    rubric_focus: str = ""
    strengths: list[str] = Field(default_factory=list)
    critical_flaws: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    score: float = Field(default=3.0, ge=1.0, le=5.0)
    confidence_score: float = Field(default=3.5, ge=1.0, le=5.0)


class PeerReviewReport(BaseModel):
    paper_id: str
    title: str = ""
    domain_evaluated: str
    rubric_applied: str
    verdict: ReviewVerdict
    overall_score: float = Field(default=3.0, ge=1.0, le=5.0)
    executive_summary: str
    evaluations: list[PersonaEvaluation] = Field(default_factory=list)
    meta_reviewer_notes: str
    missing_experiments_or_controls: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    markdown_report: str = ""
