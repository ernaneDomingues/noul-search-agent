"""Economics & Econometrics peer-review rubric (Causal Identification & Empirical Robustness — Issue #20)."""

from typing import Any

RUBRIC_NAME = "Empirical Econometrics & Causal Identification Peer-Review Rubric"

PERSONAS: list[dict[str, Any]] = [
    {
        "role": "Causal Identification Econometrician",
        "focus": (
            "Causal identification strategy (Difference-in-Differences, Instrumental Variables / 2SLS, "
            "Regression Discontinuity / RDD, Natural Experiment), parallel trends plausibility, exclusion "
            "restriction, and endogeneity / omitted variable bias (OVB) threats."
        ),
        "default_questions": [
            "What formal pre-trend / event-study evidence or instrument validity tests (first-stage F-statistic, exclusion restriction) rule out endogeneity?",
            "Could time-varying unobserved shocks or anticipation effects violate the identifying assumption?",
        ],
    },
    {
        "role": "Empirical Robustness & Inference Auditor",
        "focus": (
            "Clustered and heteroskedasticity-robust standard errors, placebo and falsification tests, "
            "sensitivity to alternative time windows and covariate specifications, and panel data attrition/balance."
        ),
        "default_questions": [
            "At what level (e.g. firm, region, cohort, two-way) are standard errors clustered, and do results survive wild bootstrap / randomization inference?",
            "Do placebo treatments (in-time or in-space falsification tests) yield null effects as required by the identification strategy?",
        ],
    },
    {
        "role": "Policy & External Validity Economist",
        "focus": (
            "Economic magnitude and welfare significance (beyond p-value hacking), institutional and geographic "
            "external validity, general equilibrium / spillover effects (SUTVA), and open replication package availability."
        ),
        "default_questions": [
            "What is the economic magnitude of the estimated effect in standard-deviation or welfare/cost-benefit terms?",
            "Do general equilibrium spillovers (SUTVA violations) or institutional particularities limit external validity to other markets?",
        ],
    },
]
