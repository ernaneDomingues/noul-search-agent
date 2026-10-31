"""Schemas for Phase 6 (#22-#27): Academic Authoring, BibTeX Sanitizer, Rebuttal & Submission Gatekeeper."""

from pydantic import BaseModel as _Model
from pydantic import Field as _field


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
    category: str  # weak_claim | notation_inconsistency | reproducibility_omission
    severity: str  # high | medium | low
    excerpt: str
    issue_description: str
    suggested_rewrite: str


class DraftAuditReport(_Model):
    source_label: str = "inline_draft"
    venue: str = "general"
    output_format: str = "markdown"
    weak_or_unsubstantiated_claims: list[DraftAuditFinding] = _field(default_factory=list)
    notation_or_terminology_inconsistencies: list[DraftAuditFinding] = _field(default_factory=list)
    reproducibility_omissions: list[DraftAuditFinding] = _field(default_factory=list)
    pre_submission_checklist: list[str] = _field(default_factory=list)
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
    severity: str = "medium"  # high | medium | low
    original_comment: str = ""
    requires_new_experiment: bool = False
    draft_response: str = ""
    manuscript_action_plan: str = ""


class RebuttalReport(_Model):
    reviewers_detected: list[str] = _field(default_factory=list)
    total_points: int = 0
    new_experiments_required: int = 0
    points: list[RebuttalPoint] = _field(default_factory=list)
    markdown_rebuttal: str = ""
    latex_rebuttal: str = ""


class GatekeeperCheckItem(_Model):
    check_id: str
    category: str  # anonymity | checklist | overclaiming | mandatory_section
    passed: bool
    severity: str  # critical | high | medium | low
    description: str
    evidence_or_location: str = ""
    remediation: str = ""


class SubmissionGatekeeperReport(_Model):
    venue: str
    readiness_score: int = _field(default=100, ge=0, le=100)  # 0 to 100
    desk_reject_risk: str  # low | medium | critical
    anonymity_violations: list[GatekeeperCheckItem] = _field(default_factory=list)
    venue_checklist: list[GatekeeperCheckItem] = _field(default_factory=list)
    overclaiming_flags: list[GatekeeperCheckItem] = _field(default_factory=list)
    mandatory_sections: list[GatekeeperCheckItem] = _field(default_factory=list)
    mandatory_corrective_actions: list[str] = _field(default_factory=list)
    summary_markdown: str = ""
