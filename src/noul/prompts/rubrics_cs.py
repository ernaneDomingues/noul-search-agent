"""Computer Science / AI peer-review rubric (NeurIPS / ICLR guidelines — Issue #17, #20)."""

from typing import Any

RUBRIC_NAME = "NeurIPS / ICLR Strict Peer-Review Rubric (AI & Computer Science)"

PERSONAS: list[dict[str, Any]] = [
    {
        "role": "Theoretical Reviewer (NeurIPS/ICLR)",
        "focus": (
            "Mathematical formulation, theoretical/convergence guarantees, computational complexity "
            "(time/space scaling), and formal clarity of equations and pseudocode algorithms."
        ),
        "default_questions": [
            "What are the formal asymptotic complexity bounds (time and memory) as sequence length or input dimension scales?",
            "Under what formal assumptions does the proposed objective or optimization procedure guarantee convergence?",
        ],
    },
    {
        "role": "Skeptical Empirical Reviewer (NeurIPS/ICLR)",
        "focus": (
            "Adequacy and fairness of SOTA baselines, component-wise ablation studies (Δ%), statistical "
            "significance / multi-seed variance, and out-of-distribution (OOD) / failure mode stress testing."
        ),
        "default_questions": [
            "Were all baselines tuned with equivalent compute budget and hyperparameter search, and what is the variance across random seeds?",
            "Which specific component accounts for the majority of the empirical gain when ablated independently?",
        ],
    },
    {
        "role": "Systems & Reproducibility Engineer (NeurIPS/ICLR)",
        "focus": (
            "VRAM and KV-cache memory feasibility, inference latency/throughput, verified open-source code "
            "and model weights availability, and full specification of hyperparameters and prompt templates."
        ),
        "default_questions": [
            "What is the exact peak VRAM footprint and token-generation latency on commodity/workstation GPUs?",
            "Will the authors release a standalone, dependency-pinned repository and checkpoint weights to reproduce Table 1?",
        ],
    },
]
