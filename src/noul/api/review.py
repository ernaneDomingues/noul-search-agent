"""Peer-review evidence and implementation scaffolds for one paper."""

from noul import registry as _registry
from noul.api import common as _common
from noul.schemas import tool_inputs as _inputs
from noul.schemas import tool_outputs as _out
from noul.tools import review_tools as _review_tools
from noul.tools import scaffold_tools as _scaffold_tools


@_registry.tool(
    "review_paper",
    description=(
        "Multi-persona peer-review committee: detects the paper's scientific domain (or uses "
        "`domain` override) and runs 3 specialized reviewer personas (NeurIPS/ICLR Theoretical, Skeptical "
        "Empirical, and Systems/Reproducibility for CS/AI; CONSORT/STROBE Clinical Trial Methodologist, "
        "Biostatistician, and Translational Safety Reviewer for Biomedical; Causal Identification "
        "Econometrician, Robustness Auditor, and Policy Economist for Economics). Returns each persona's "
        "strengths, concerns and gaps as signals with excerpts, missing experiments/controls, questions for "
        "the authors and the domain rubric - no verdict or score: you write the review and the "
        "recommendation, citing the excerpts."
    ),
    input=_inputs.ReviewPaperInput,
    profiles=("review",),
    output=_out.PeerReviewReport,
)
def review_paper(paper_id, domain=None, inspect_fulltext=True):
    return _common._with_integrity(
        _review_tools.review_paper(paper_id, domain=domain, inspect_fulltext=bool(inspect_fulltext)), paper_id
    )


@_registry.tool(
    "review_paper_scientific",
    description=(
        "Alias for review_paper: evidence for a domain-adaptive 3-persona scientific peer review; "
        "no verdict - you judge with the rubric."
    ),
    input=_inputs.ReviewPaperScientificInput,
    profiles=("review",),
    output=_out.PeerReviewReport,
    title="Review paper (scientific committee)",
)
def review_paper_scientific(paper_id, domain=None, inspect_fulltext=True):
    return _common._with_integrity(
        _review_tools.review_paper_scientific(paper_id, domain=domain, inspect_fulltext=bool(inspect_fulltext)),
        paper_id,
    )


@_registry.tool(
    "scaffold_implementation",
    description=(
        "Polyglot code & experimental protocol scaffolder: translates a paper's mathematical "
        "formulations, architecture, econometric specification or clinical trial design into executable, "
        "ast-validated code + unit tests (PyTorch `nn.Module` with shape assertions for CS/AI; Statsmodels or "
        "R `fixest` panel + robustness matrix for Economics; CONSORT/STROBE operational checklist + ITT "
        "analytical pipeline for Biomedical/STEM)."
    ),
    input=_inputs.ScaffoldImplementationInput,
    profiles=("review",),
    output=_out.CodeScaffold,
)
def scaffold_implementation(paper_id, target=None, domain=None):
    return _scaffold_tools.scaffold_implementation(paper_id, target=target, domain=domain)


@_registry.tool(
    "scaffold_paper_code",
    description=(
        "Alias for scaffold_implementation: generates PyTorch, Statsmodels/R, or Clinical Protocol code + unit tests."
    ),
    input=_inputs.ScaffoldPaperCodeInput,
    profiles=("review",),
)
def scaffold_paper_code(paper_id, target_language=None, domain=None):
    return _scaffold_tools.scaffold_paper_code(paper_id, target_language=target_language, domain=domain)
