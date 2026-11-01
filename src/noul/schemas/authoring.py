"""Schemas for Phase 6 (#22-#27): Academic Authoring, BibTeX Sanitizer, Rebuttal & Submission Gatekeeper."""

from pydantic import BaseModel as _Model
from pydantic import Field as _field

from noul.schemas.evidence import AnalysisResult, Excerpt


class CitationRecommendationItem(_Model):
    claim_sentence: str
    marker_or_trigger: str = ""
    search_query: str = ""
    recommended_papers: list[dict] = _field(default_factory=list)
    justifications: list[str] = _field(default_factory=list)
    citation_keys: list[str] = _field(default_factory=list)
    inline_replacement: str = ""


class CitationInfillerReport(_Model):
    input_format: str = "markdown"  # plain | markdown | latex
    annotated_text: str = ""
    claims_detected: int = 0
    recommendations: list[CitationRecommendationItem] = _field(default_factory=list)
    bibtex_block: str = ""
    warnings: list[str] = _field(default_factory=list)


class BlueprintSection(_Model):
    section_title: str
    imrad_role: str  # abstract | introduction | related_work | methodology | experiments | discussion | conclusion | venue_mandatory
    paragraph_prompts: list[str] = _field(default_factory=list)
    recommended_elements: list[str] = _field(default_factory=list)


class ManuscriptBlueprint(_Model):
    topic: str
    venue: str = "general"
    hypothesis: str = ""
    key_findings: list[str] = _field(default_factory=list)
    output_format: str = "markdown"  # markdown | latex
    sections: list[BlueprintSection] = _field(default_factory=list)
    rendered_template: str = ""


class DraftAuditFinding(_Model):
    """Internal finding of the draft-audit heuristics (converted to a `Signal` in the result)."""

    category: str  # weak_claim | notation_inconsistency | reproducibility_omission
    severity: str  # high | medium | low
    excerpt: str
    issue_description: str
    suggested_rewrite: str


class DraftAuditReport(AnalysisResult):
    """Draft findings are `signals` (category weak_claim | notation_inconsistency | reproducibility_omission,
    excerpts from the user's draft); `rendered_report` is Markdown or LaTeX per `output_format`."""

    venue: str = "general"
    output_format: str = "markdown"
    rendered_report: str = ""


class RelatedWorkThemeCluster(_Model):
    theme_name: str
    approach_family: str
    paper_ids: list[str] = _field(default_factory=list)
    citation_keys: list[str] = _field(default_factory=list)
    comparative_narrative: str = ""


class RelatedWorkSynthesis(_Model):
    proposal_summary: str
    citation_style: str = "latex"  # latex | numeric
    output_format: str = "markdown"  # markdown | latex
    thematic_clusters: list[RelatedWorkThemeCluster] = _field(default_factory=list)
    positioning_paragraph: str = ""
    rendered_related_work: str = ""
    bibtex_block: str = ""


class BibtexEntryUpdate(_Model):
    original_key: str
    canonical_key: str
    title: str
    action: str  # kept | upgraded_preprint | deduplicated | doi_completed | latex_sanitized
    old_venue_or_type: str = ""
    new_venue_or_type: str = ""
    doi: str | None = None
    notes: list[str] = _field(default_factory=list)


class BibtexSanitizeReport(_Model):
    entries_total: int = 0
    entries_out: int = 0
    duplicates_removed: int = 0
    preprints_upgraded: int = 0
    dois_completed: int = 0
    latex_escapes_fixed: int = 0
    updates: list[BibtexEntryUpdate] = _field(default_factory=list)
    sanitized_bibtex: str = ""
    warnings: list[str] = _field(default_factory=list)


class RebuttalPoint(_Model):
    point_id: str
    reviewer_id: str
    category: str  # clarity_question | new_experiment_request | baseline_criticism | notation_or_formatting | methodological_or_statistical
    priority_hint: str = "medium"  # high | medium | low (pattern-based guess)
    requires_new_experiment: bool = False
    comment: Excerpt  # the reviewer's words (untrusted)
    response_template: str = ""  # with [TODO] placeholders: never a claim about results
    manuscript_action_template: str = ""


class RebuttalReport(AnalysisResult):
    """Parsed reviewer points with response templates; `excerpts` hold the paper abstract / draft context."""

    reviewers_detected: list[str] = _field(default_factory=list)
    total_points: int = 0
    new_experiments_requested: int = 0
    points: list[RebuttalPoint] = _field(default_factory=list)
    markdown_rebuttal: str = ""
    latex_rebuttal: str = ""


class GatekeeperCheckItem(_Model):
    """Internal finding of the gatekeeper heuristics (converted to signals / checklist items in the result)."""

    check_id: str
    category: str  # anonymity | checklist | overclaiming | mandatory_section
    passed: bool
    severity: str  # critical | high | medium | low
    description: str
    evidence_or_location: str = ""
    remediation: str = ""


class SubmissionGatekeeperReport(AnalysisResult):
    """Pre-submission evidence: anonymity and overclaiming findings are `signals`; venue checklist and mandatory
    sections are `checklist` items (detected yes/no). No readiness score: the client judges readiness."""

    venue: str
    double_blind: bool = False
    suggested_actions: list[str] = _field(default_factory=list)
