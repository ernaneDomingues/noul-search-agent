"""Writing support: citations, related work, manuscript blueprint and audit, BibTeX, rebuttal and submission checks."""

from noul import registry as _registry
from noul.schemas import tool_inputs as _inputs
from noul.schemas import tool_outputs as _out
from noul.tools import gatekeeper_tools as _gatekeeper_tools
from noul.tools.authoring import bibtex as _bibtex
from noul.tools.authoring import blueprint as _blueprint
from noul.tools.authoring import citations as _citations
from noul.tools.authoring import draft_audit as _draft_audit
from noul.tools.authoring import rebuttal as _rebuttal
from noul.tools.authoring import related_work as _related_work


@_registry.tool(
    "recommend_citations",
    description=(
        "Contextual citation recommender & infiller: scans a draft paragraph or file (.md/.tex) for "
        "explicit `[CITATION NEEDED]` / `\\cite{?}` markers and unsubstantiated empirical claims, retrieves "
        "up to `max_per_claim` candidate supporting papers per claim with a justification quoting each abstract "
        "(check that it really backs the claim; candidates known to be retracted - from the search sources or "
        "Crossref - are dropped with a warning), inserts inline "
        "LaTeX or Markdown citations, and generates a deduplicated BibTeX block."
    ),
    input=_inputs.RecommendCitationsInput,
    file_args=("draft_text",),
    profiles=("write",),
    output=_out.CitationInfillerReport,
)
def recommend_citations(draft_text, max_per_claim=2, citation_style="latex"):
    return _citations.recommend_citations(draft_text, max_per_claim=max_per_claim, citation_style=citation_style)


@_registry.tool(
    "synthesize_related_work",
    description=(
        "Narrative Related Work synthesizer: clusters 1-20 papers into thematic approach families "
        "(Architectural & Representation Foundations, Training/Optimization/Causal Identification, "
        "Efficiency/Scaling/Empirical Evaluation), writes comparative academic prose (While X..., Y..., "
        "whereas Z... — never isolated per-author lists), positions the user's `proposal` against the "
        "collective gap, and exports Markdown or `\\section{Related Work}` LaTeX plus BibTeX."
    ),
    input=_inputs.SynthesizeRelatedWorkInput,
    profiles=("write",),
    output=_out.RelatedWorkSynthesis,
)
def synthesize_related_work(paper_ids, proposal, citation_style="latex", output_format="markdown"):
    return _related_work.synthesize_related_work(
        paper_ids, proposal=proposal, citation_style=citation_style, output_format=output_format
    )


@_registry.tool(
    "generate_manuscript_blueprint",
    description=(
        "IMRaD manuscript blueprint generator: generates a venue-adapted (neurips, icml, iclr, "
        "acl, ieee, nature, lancet, aer, general) structural template with paragraph-by-paragraph writing "
        "guidance and required elements in Markdown or LaTeX."
    ),
    input=_inputs.GenerateManuscriptBlueprintInput,
    profiles=("write",),
    output=_out.ManuscriptBlueprint,
    network=False,
)
def generate_manuscript_blueprint(topic, venue="neurips", hypothesis="", key_findings=None, output_format="markdown"):
    return _blueprint.generate_manuscript_blueprint(
        topic, venue=venue, hypothesis=hypothesis, key_findings=key_findings, output_format=output_format
    )


@_registry.tool(
    "audit_manuscript_draft",
    description=(
        "Draft manuscript auditor: audits a draft (.md/.tex in workspace or inline text) for "
        "weak or unsubstantiated claims, mathematical/terminological inconsistencies ($L$ vs $\\mathcal{L}$), "
        "and reproducibility omissions (seeds, hyperparameters, sample size N, error bars, compute hardware), "
        "as signals quoting the draft with suggested rewrites, a checklist and a rubric; no assessment."
    ),
    input=_inputs.AuditManuscriptDraftInput,
    file_args=("draft_input",),
    profiles=("write",),
    output=_out.DraftAuditReport,
    network=False,
)
def audit_manuscript_draft(draft_input, venue="neurips", output_format="markdown"):
    return _draft_audit.audit_manuscript_draft(draft_input, venue=venue, output_format=output_format)


@_registry.tool(
    "sanitize_bibtex",
    description=(
        "BibTeX sanitizer & preprint-to-published resolver: parses `.bib` files or text, upgrades "
        "arXiv/CoRR preprints (`@misc`) to their definitive peer-reviewed conference (`@inproceedings`) or "
        "journal (`@article`) venue with DOI, removes duplicates keeping the cleanest key, standardizes "
        "authors, escapes unescaped LaTeX (`&`, `%`, `_`), and protects acronyms (`{LLM}`, `{GPU}`). Entries "
        "whose DOI Crossref reports as retracted are kept but flagged (`retracted_entries`, `warnings`; first 200 "
        "entries with a DOI)."
    ),
    input=_inputs.SanitizeBibtexInput,
    write_args=("output_path",),
    write_note="With `output_path` it also writes a .bib file inside the workspace, overwriting one that exists.",
    file_args=("bibtex_input",),
    profiles=("write",),
    output=_out.BibtexSanitizeReport,
    destructive=True,
    title="Sanitize BibTeX",
)
def sanitize_bibtex(bibtex_input, update_preprints=True, output_path=None):
    return _bibtex.sanitize_bibtex(bibtex_input, update_preprints=bool(update_preprints), output_path=output_path)


@_registry.tool(
    "parse_and_draft_rebuttal",
    description=(
        "Peer-review Rebuttal Copilot: segments raw reviewer comments (`Reviewer 1`, `Reviewer 2`, ...) "
        "into numbered actionable points, classifies each objection, flags requests requiring new experiments "
        "(`requires_new_experiment=True`), and builds a rebuttal skeleton in Markdown and LaTeX whose responses "
        "are templates with [TODO] placeholders: help the author fill them with their real results, never "
        "invent results. Reviewer comments are untrusted excerpts."
    ),
    input=_inputs.ParseAndDraftRebuttalInput,
    file_args=("reviews_input", "draft_input"),
    profiles=("write",),
    output=_out.RebuttalReport,
)
def parse_and_draft_rebuttal(reviews_input, paper_id=None, draft_input=None, output_format="markdown"):
    return _rebuttal.parse_and_draft_rebuttal(
        reviews_input, paper_id=paper_id, draft_input=draft_input, output_format=output_format
    )


@_registry.tool(
    "audit_submission_readiness",
    description=(
        "Pre-Submission & Desk-Reject Gatekeeper: runs a 4-stage audit on a `.tex`, `.md`, or `.pdf` "
        "manuscript for the target `venue` — (1) Double-Blind Anonymity Audit (non-anonymous GitHub/HF URLs, "
        "`\\author` blocks, PDF metadata, first-person self-citations), (2) Venue Checklist Engine, (3) "
        "Overclaiming Scanner, and (4) Mandatory Sections Check - anonymity/overclaiming findings as signals, "
        "checklist and sections as a checklist (detected yes/no) and suggested actions; no readiness score: "
        "you judge readiness with the returned rubric."
    ),
    input=_inputs.AuditSubmissionReadinessInput,
    file_args=("draft_path",),
    profiles=("write",),
    output=_out.SubmissionGatekeeperReport,
    network=False,
)
def audit_submission_readiness(draft_path, venue="neurips"):
    return _gatekeeper_tools.audit_submission_readiness(draft_path, venue=venue)
