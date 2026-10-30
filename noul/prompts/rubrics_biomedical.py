"""Biomedical / Clinical Research peer-review rubric (CONSORT / STROBE / PRISMA guidelines — Issue #20)."""

RUBRIC_NAME = "CONSORT / STROBE / PRISMA Biomedical & Clinical Peer-Review Rubric"

PERSONAS = [
    {
        "role": "Clinical Trial & Study Design Methodologist (CONSORT/STROBE)",
        "focus": (
            "Study design rigor, randomization sequence and allocation concealment, double-blinding / masking, "
            "placebo or active control arm adequacy, eligibility criteria, and pre-registered trial protocol."
        ),
        "default_questions": [
            "Was the study protocol pre-registered (e.g. ClinicalTrials.gov / PROSPERO) with primary endpoints locked prior to unblinding?",
            "How were allocation concealment and blinding of outcome assessors maintained throughout follow-up?",
        ],
    },
    {
        "role": "Biostatistician & Statistical Power Auditor",
        "focus": (
            "Sample size (N) and a priori statistical power calculation (1-beta), 95% confidence intervals, "
            "multivariable confounder adjustment, multiple-testing correction, attrition/loss to follow-up, "
            "and intention-to-treat (ITT) vs per-protocol analysis."
        ),
        "default_questions": [
            "What a priori power calculation (1-beta, alpha, minimum detectable effect size) justified the enrolled sample size N?",
            "How were missing outcome data, participant attrition, and unmeasured confounders handled in sensitivity analyses?",
        ],
    },
    {
        "role": "Translational Medicine & Clinical Safety Reviewer",
        "focus": (
            "Clinical relevance of primary endpoints (patient-centered clinical outcomes vs surrogate biomarkers), "
            "adverse events (AEs) and toxicity reporting, generalizability to real-world patient populations "
            "(flagging preclinical in vitro / animal translation gaps), and conflicts of interest."
        ),
        "default_questions": [
            "What was the incidence and severity grade of treatment-emergent adverse events across study arms?",
            "How directly do these findings translate to heterogeneous real-world clinical populations outside strict trial inclusion criteria?",
        ],
    },
]
