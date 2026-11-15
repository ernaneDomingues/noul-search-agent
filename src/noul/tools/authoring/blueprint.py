"""Manuscript blueprint: a venue-adapted IMRaD outline for a topic (`generate_manuscript_blueprint`)."""

import re

from noul import errors as _errors
from noul.schemas.authoring import (
    BlueprintSection,
    ManuscriptBlueprint,
)

_SUPPORTED_VENUES = ("neurips", "icml", "iclr", "acl", "ieee", "nature", "lancet", "aer", "elsevier", "general")


def generate_manuscript_blueprint(
    topic: str,
    venue: str = "neurips",
    hypothesis: str = "",
    key_findings: list[str] | None = None,
    output_format: str = "markdown",
) -> dict:
    """Issue #23 (Mode 1): Generate a complete IMRaD structural blueprint tailored to `venue`
    (NeurIPS, ICML, ICLR, ACL, IEEE, Nature, Lancet, AER, general) in Markdown or LaTeX."""
    if not isinstance(topic, str) or not topic.strip():
        raise _errors.HTTPError(400, "Topic must be a non-empty string.")
    v = (venue or "neurips").strip().lower()
    if v not in _SUPPORTED_VENUES:
        raise _errors.HTTPError(400, f"Unsupported venue '{venue}'. Choose from: {', '.join(_SUPPORTED_VENUES)}")
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    if isinstance(key_findings, str):
        findings = [x.strip() for x in re.split(r"[;\n]", key_findings) if x.strip()]
    elif isinstance(key_findings, (list, tuple)):
        findings = [str(x).strip() for x in key_findings if str(x).strip()]
    else:
        findings = []

    hyp_str = (
        hypothesis or ""
    ).strip() or f"Our proposed method for {topic.strip()} improves efficiency and accuracy over SOTA baselines."
    findings_preview = (
        "; ".join(findings) if findings else "Quantified improvements (e.g., +X.X% over baseline with p < 0.01)"
    )

    venue_mandatory_title = {
        "neurips": "7. Broader Impact & NeurIPS Paper Checklist",
        "icml": "7. Impact Statement & Reproducibility Checklist",
        "iclr": "7. Reproducibility & Ethics Statement",
        "acl": "7. Limitations & Ethics Statement (ACL Mandatory)",
        "ieee": "7. Conclusion & Acknowledgment",
        "nature": "7. Data Availability, Code Availability & Author Contributions",
        "lancet": "7. Research in Context, CONSORT/STROBE Declarations & Role of Funding Source",
        "aer": "7. Data & Code Availability Statement (AEA Mandatory)",
        "elsevier": "7. Declaration of Competing Interest & Data Availability",
        "general": "7. Reproducibility & Broader Impact",
    }[v]

    sections = [
        BlueprintSection(
            section_title="Abstract",
            imrad_role="abstract",
            paragraph_prompts=[
                f"Sentences 1-2 (Context & Bottleneck): Define the high-stakes problem in {topic.strip()} and why current methods fail.",
                f"Sentence 3 (Core Hypothesis & Proposal): State the central hypothesis ({hyp_str}).",
                f"Sentences 4-5 (Quantitative Findings): Summarize empirical gains ({findings_preview}) and release of open artifacts.",
            ],
            recommended_elements=[
                "150-250 words",
                "Zero undefined acronyms",
                "Headline metric delta with confidence interval",
            ],
        ),
        BlueprintSection(
            section_title="1. Introduction",
            imrad_role="introduction",
            paragraph_prompts=[
                f"Paragraph 1 (Macro Motivation): Establish the scientific and practical importance of {topic.strip()}.",
                "Paragraph 2 (Limitations of Prior Work): Explain the structural trade-offs in existing literature.",
                f"Paragraph 3 (Central Hypothesis & Approach): Formulate the testable hypothesis: '{hyp_str}'.",
                "Paragraph 4 (Bulleted Contributions): List 3-4 concrete, falsifiable contributions anchored to sections and tables.",
            ],
            recommended_elements=[
                "Figure 1 (Overview / Teaser diagram)",
                "Bulleted list of 3-4 contributions",
                "Explicit forward references to Sections 3-5",
            ],
        ),
        BlueprintSection(
            section_title="2. Related Work",
            imrad_role="related_work",
            paragraph_prompts=[
                "Paragraph 1 (Foundational Architectures / Theory): Contrast foundational paradigms rather than listing papers in isolation.",
                "Paragraph 2 (Optimization, Identification & Empirical Benchmarks): Compare methodological assumptions and evaluation regimes.",
                "Paragraph 3 (Positioning & Gap): Explicitly state how your method resolves the collective limitation of prior work.",
            ],
            recommended_elements=[
                "3 thematic clusters",
                "Comparative connectors (While X..., Y..., whereas Z...)",
                "Explicit differentiation paragraph",
            ],
        ),
        BlueprintSection(
            section_title="3. Methodology / Problem Formulation",
            imrad_role="methodology",
            paragraph_prompts=[
                "Paragraph 1 (Formal Problem Setup & Notation): Define input/output spaces, random variables, and notation table.",
                "Paragraph 2 (Core Mechanism / Identification Strategy): Present numbered equations and algorithmic steps.",
                "Paragraph 3 (Complexity / Theoretical Guarantees): Analyze computational complexity, memory footprint, or identification assumptions.",
            ],
            recommended_elements=[
                "Numbered Equations (Eq. 1-3)",
                "Algorithm 1 pseudocode box",
                "Explicit assumptions and boundary conditions",
            ],
        ),
        BlueprintSection(
            section_title="4. Experimental Setup & Empirical Results",
            imrad_role="experiments",
            paragraph_prompts=[
                "Paragraph 1 (Datasets, Baselines & Metrics): Justify benchmark selection, sample sizes (N), splits, and strong SOTA baselines.",
                "Paragraph 2 (Implementation & Reproducibility Details): Report exact hyperparameters, random seeds, optimizer, and compute hardware.",
                f"Paragraph 3 (Main Results): Present primary quantitative evidence supporting: {findings_preview}.",
                "Paragraph 4 (Ablation & Robustness Studies): Isolate the contribution of each component (w/o X) with error bars.",
            ],
            recommended_elements=[
                "Table 1 (Main benchmark comparison with ± std)",
                "Table 2 (Ablation study)",
                "Hyperparameter & hardware specification",
            ],
        ),
        BlueprintSection(
            section_title="5. Discussion & Limitations",
            imrad_role="discussion",
            paragraph_prompts=[
                "Paragraph 1 (Interpretation of Findings): Explain why the proposed mechanism succeeds and where marginal gains saturate.",
                "Paragraph 2 (Failure Modes & Threats to Validity): Transparently analyze boundary conditions, OOD degradation, and compute/clinical constraints.",
            ],
            recommended_elements=[
                "Dedicated Limitations subsection",
                "Failure case analysis",
                "Calibration of claims to empirical evidence",
            ],
        ),
        BlueprintSection(
            section_title="6. Conclusion",
            imrad_role="conclusion",
            paragraph_prompts=[
                f"Paragraph 1 (Synthesis & Future Directions): Summarize how the study advances {topic.strip()} and outline concrete open problems.",
            ],
            recommended_elements=["Concise summary without introducing unverified claims"],
        ),
        BlueprintSection(
            section_title=venue_mandatory_title,
            imrad_role="venue_mandatory",
            paragraph_prompts=[
                f"Mandatory {v.upper()} declarations: address ethical implications, societal impact, data/code licensing, and reproducibility checklist items.",
            ],
            recommended_elements=[
                f"Venue-specific ({v.upper()}) compliance block",
                "Anonymized artifact URLs for double-blind review",
            ],
        ),
    ]

    if fmt == "latex":
        lines = [
            f"% Manuscript Blueprint ({v.upper()}) — Topic: {topic.strip()}",
            f"\\title{{{topic.strip()}}}",
            "\\author{Anonymous Author(s)}",
            "\\maketitle",
            "",
        ]
        for sec in sections:
            if sec.imrad_role == "abstract":
                lines.append("\\begin{abstract}")
                for p in sec.paragraph_prompts:
                    lines.append(f"% {p}")
                lines.append("\\end{abstract}\n")
            else:
                clean_title = re.sub(r"^\d+\.\s*", "", sec.section_title)
                lines.append(f"\\section{{{clean_title}}}")
                for p in sec.paragraph_prompts:
                    lines.append(f"% Paragraph guidance: {p}")
                for el in sec.recommended_elements:
                    lines.append(f"% Required element: {el}")
                lines.append("")
        rendered = "\n".join(lines)
    else:
        lines = [
            f"# {topic.strip()}",
            f"> **Target Venue:** `{v.upper()}` | **Central Hypothesis:** {hyp_str}",
            "",
        ]
        for sec in sections:
            lines.append(f"## {sec.section_title}")
            lines.append("**Paragraph-by-Paragraph Writing Plan:**")
            for idx, p in enumerate(sec.paragraph_prompts, 1):
                lines.append(f"{idx}. {p}")
            lines.append("\n**Recommended Elements:**")
            for el in sec.recommended_elements:
                lines.append(f"- [ ] {el}")
            lines.append("")
        rendered = "\n".join(lines)

    blueprint = ManuscriptBlueprint(
        topic=topic.strip(),
        venue=v,
        hypothesis=hyp_str,
        key_findings=findings,
        output_format=fmt,
        sections=sections,
        rendered_template=rendered,
    )
    return blueprint.model_dump()
