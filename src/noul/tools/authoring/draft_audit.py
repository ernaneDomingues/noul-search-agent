"""Draft auditor: weak claims, inconsistent notation and missing reproducibility details in a draft (`audit_manuscript_draft`)."""

import re

from noul import errors as _errors
from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.schemas.authoring import (
    DraftAuditFinding,
    DraftAuditReport,
)
from noul.schemas.evidence import AnalysisSubject, ChecklistItem, Signal
from noul.tools.authoring import blueprint as _blueprint
from noul.tools.authoring import citations as _citations
from noul.tools.authoring import common as _common

_WEAK_CLAIM_PATTERNS = (
    (
        re.compile(r"\b(?:clearly\s+proves?|proves?\s+beyond\s+doubt|undoubtedly|obviously|trivially)\b", re.I),
        "high",
        "Overstated or dogmatic phrasing ('clearly proves' / 'obviously' / 'undoubtedly') without formal proof or citation.",
        "Replace absolute language with calibrated empirical hedging (e.g., 'Our experiments on Table 1 indicate that... \\cite{...}').",
    ),
    (
        re.compile(
            r"\b(?:massive|huge|dramatic|unprecedented|vastly\s+superior)\s+(?:improvement|gain|speedup|performance)s?\b",
            re.I,
        ),
        "high",
        "Vague qualitative superlative ('massive/dramatic improvement') lacking exact metric delta and baseline anchor.",
        "Specify the exact quantitative delta, metric, and baseline (e.g., 'improves accuracy by +4.2% (±0.3%) over Baseline X on Dataset Y').",
    ),
    (
        re.compile(r"\b(?:state[-\s]of[-\s]the[-\s]art|\bsota\b|outperforms?\s+all)\b", re.I),
        "medium",
        "State-of-the-art / universal superiority claim without adjacent Table/Figure reference or citation.",
        "Anchor the claim to specific evaluated benchmarks and citations (e.g., 'achieves competitive performance among evaluated baselines on Benchmark Z (Table 1)').",
    ),
)


def _detect_weak_claims(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text or ""):
        sent = m.group(0).strip()
        if len(sent) < 12 or sent.startswith(("%", "#")):
            continue
        has_anchor = bool(
            _citations._EXISTING_CITATION_RE.search(sent)
            or re.search(r"\b(?:Table|Figure|Fig\.|Tab\.|Eq\.|Section)\s*\d+|\b\\ref\{", sent, re.I)
        )
        for rx, sev, desc, rewrite in _WEAK_CLAIM_PATTERNS:
            if rx.search(sent):
                if sev == "medium" and has_anchor:
                    continue
                findings.append(
                    DraftAuditFinding(
                        category="weak_claim",
                        severity=sev,
                        excerpt=sent[:240],
                        issue_description=desc,
                        suggested_rewrite=rewrite,
                    )
                )
                break
    return findings


def _detect_notation_inconsistencies(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    # 1. Mixed math symbol styling: e.g. L_total vs \mathcal{L}_{total} or L vs \mathcal{L}
    has_plain_loss = bool(re.search(r"\$L(?:_[a-zA-Z0-9{}]+)?\$|\bL_(?:total|loss|train)\b", text or ""))
    has_cal_loss = bool(re.search(r"\\mathcal\{L\}", text or ""))
    if has_plain_loss and has_cal_loss:
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="high",
                excerpt="Mixed usage of $L$ / $L_{total}$ and $\\mathcal{L}$ across equations and prose.",
                issue_description="Inconsistent mathematical notation for the loss/objective function ($L$ vs. $\\mathcal{L}$).",
                suggested_rewrite="Standardize on $\\mathcal{L}_{\\text{total}}$ throughout all equations and inline prose.",
            )
        )

    # 2. Mixed indexing subscript vs superscript: x_i and x^{(i)}
    if re.search(r"\bx_\{?i\}?\b", text or "") and re.search(r"\bx\^\{\(i\)\}", text or ""):
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="medium",
                excerpt="Mixed sample indexing $x_i$ and $x^{(i)}$.",
                issue_description="Sample index notation alternates between subscript $x_i$ and parenthesized superscript $x^{(i)}$.",
                suggested_rewrite="Use $x_i$ consistently for token/feature indices and $x^{(n)}$ exclusively for dataset sample indices.",
            )
        )

    # 3. Terminology inconsistency (e.g., fine-tuning vs finetuning vs Fine Tuning)
    ft_variants = set(
        re.findall(r"\b(?:fine-tun(?:ing|ed)|finetun(?:ing|ed)|fine\s+tun(?:ing|ed))\b", (text or "").lower())
    )
    normalized_ft = {re.sub(r"\s+", " ", v) for v in ft_variants}
    if len(normalized_ft) > 1:
        findings.append(
            DraftAuditFinding(
                category="notation_inconsistency",
                severity="low",
                excerpt=", ".join(sorted(normalized_ft)),
                issue_description="Inconsistent hyphenation of 'fine-tuning' vs 'finetuning' / 'fine tuning'.",
                suggested_rewrite="Standardize on hyphenated 'fine-tuning' / 'fine-tuned' across the entire manuscript.",
            )
        )

    # 4. Equation with unintroduced variables
    eq_matches = list(re.finditer(r"(?:\\begin\{equation\}|\$\$)(.*?)(?:\\end\{equation\}|\$\$)", text or "", re.S))
    for eq_m in eq_matches:
        eq_body = eq_m.group(1).strip()
        after_window = (text or "")[eq_m.end() : eq_m.end() + 260]
        if not re.search(r"\b(?:where|denotes|represents|is\s+the|with)\b", after_window, re.I):
            findings.append(
                DraftAuditFinding(
                    category="notation_inconsistency",
                    severity="medium",
                    excerpt=re.sub(r"\s+", " ", eq_body)[:160],
                    issue_description="Displayed equation is not followed by a 'where ... denotes ...' symbol definition clause.",
                    suggested_rewrite="Immediately follow the equation with: 'where $\\theta \\in \\mathbb{R}^d$ denotes the model parameters and ...'",
                )
            )
            break

    return findings


def _detect_reproducibility_omissions(text: str) -> list[DraftAuditFinding]:
    findings: list[DraftAuditFinding] = []
    checks = [
        (
            re.compile(r"\b(?:random\s+seeds?|seeds?\s*=|\bseed\b|multi[-\s]seed)\b", re.I),
            "high",
            "Missing Random Seeds specification",
            "No mention of random seeds or multi-seed evaluation runs was found in the draft.",
            "Add to Experimental Setup: 'All experiments are repeated across 5 random seeds (seeds {42, 100, 2024, 7, 99}) and we report mean ± standard deviation.'",
        ),
        (
            re.compile(
                r"\b(?:learning\s+rate|optimizer|adamw?|sgd|batch\s+size|hyperparameter|dosage|mg/day|protocol)\b", re.I
            ),
            "high",
            "Missing Hyperparameters / Protocol specification",
            "Draft omits optimizer, learning rate, batch size, or intervention protocol parameters.",
            "Add to Experimental Setup: 'We optimize using AdamW (lr = 2e-4, weight decay = 0.01, batch size = 64, cosine warmup over 500 steps).'",
        ),
        (
            re.compile(
                r"\b(?:train(?:ing)?\s*/\s*(?:val|dev|test)|sample\s+size|\b[nN]\s*=\s*\d+|split|cohort\s+of\s+\d+)\b",
                re.I,
            ),
            "high",
            "Missing Sample Size (N) or Train/Val/Test splits",
            "Draft does not specify dataset split sizes or cohort sample size (N).",
            "Add to Experimental Setup: 'The dataset comprises N = 10,000 examples partitioned into 80%/10%/10% train/validation/test splits without data overlap.'",
        ),
        (
            re.compile(
                r"(?:\\pm|±|\b95%\s*CI\b|\bconfidence\s+interval|\bstd\b|\bstandard\s+deviation|\bp\s*[<=>]\s*0\.\d+|\berror\s+bars?\b)",
                re.I,
            ),
            "medium",
            "Missing Error Bars / Confidence Intervals / Statistical Significance",
            "Quantitative claims lack standard deviations (±), 95% confidence intervals, or p-values.",
            "Report dispersion alongside point estimates (e.g., '88.4% ± 0.4% (95% CI: [87.6, 89.2], paired t-test p < 0.01)').",
        ),
        (
            re.compile(r"\b(?:NVIDIA|A100|H100|V100|RTX|TPU|GPU|VRAM|compute\s+hours|wall[-\s]clock)\b", re.I),
            "medium",
            "Missing Compute Infrastructure & Runtime specification",
            "Draft does not report GPU/TPU hardware, VRAM footprint, or training wall-clock time.",
            "Add to Experimental Setup: 'Experiments were executed on 4x NVIDIA A100 (80GB VRAM) GPUs requiring 6.5 GPU-hours per training run.'",
        ),
    ]
    for rx, sev, excerpt_lbl, desc, rewrite in checks:
        if not rx.search(text or ""):
            findings.append(
                DraftAuditFinding(
                    category="reproducibility_omission",
                    severity=sev,
                    excerpt=excerpt_lbl,
                    issue_description=desc,
                    suggested_rewrite=rewrite,
                )
            )
    return findings


def audit_manuscript_draft(
    draft_input: str,
    venue: str = "neurips",
    output_format: str = "markdown",
) -> dict:
    """Issue #23 (Mode 2): Audit a manuscript draft (.md / .tex file in workspace or inline text) for
    weak/unsubstantiated claims, mathematical/terminological inconsistencies, and reproducibility omissions."""
    text, source_label, _ = _common._load_text_or_workspace_file(draft_input)
    v = (venue or "neurips").strip().lower()
    if v not in _blueprint._SUPPORTED_VENUES:
        raise _errors.HTTPError(
            400, f"Unsupported venue '{venue}'. Choose from: {', '.join(_blueprint._SUPPORTED_VENUES)}"
        )
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    weak = _detect_weak_claims(text)
    notation = _detect_notation_inconsistencies(text)
    repro = _detect_reproducibility_omissions(text)

    signals: list[Signal] = []
    for f in [*weak, *notation, *repro]:
        signals.append(
            Signal(
                id=f"d{len(signals) + 1}",
                category=f.category,
                kind="gap" if f.category == "reproducibility_omission" else "concern",
                description=f.issue_description,
                priority_hint=_contract.priority(f.severity),
                detail=f"Suggested rewrite: {f.suggested_rewrite}" if f.suggested_rewrite else "",
                # Omissions carry a label, not a passage of the draft: no excerpt for them.
                excerpts=[_untrusted.excerpt(f.excerpt, origin="draft")]
                if f.excerpt and f.category != "reproducibility_omission"
                else [],
            )
        )

    def state(findings):
        return "no" if findings else "unclear"

    checklist = [
        ChecklistItem(
            id="claims_anchored",
            question="Are all empirical and SOTA claims anchored to specific tables, figures or citations?",
            detected=state(weak),
        ),
        ChecklistItem(
            id="notation_consistent",
            question="Are notation and terminology consistent across prose and equations?",
            detected=state(notation),
        ),
        ChecklistItem(
            id="reproducibility_complete",
            question="Are reproducibility details (seeds, hyperparameters, sample sizes, error bars, hardware) complete?",
            detected=state(repro),
        ),
        ChecklistItem(
            id="venue_sections",
            question=f"Are the {v.upper()} mandatory sections (Limitations, Ethics/Broader Impact, checklist) included?",
            detected="unclear",
            remediation="Run audit_submission_readiness for the venue-specific checks.",
        ),
    ]
    report = DraftAuditReport(
        tool="audit_manuscript_draft",
        subject=AnalysisSubject(title=source_label, source="draft"),
        signals=signals,
        checklist=checklist,
        rubric=_contract.rubric(
            f"Manuscript draft audit ({v.upper()})",
            (
                ("claims", "Which claims are over-stated or lack a table, figure or citation?"),
                ("notation", "Which notation or terminology changes would confuse a reviewer?"),
                ("reproducibility", "What would a reader need to reproduce the main result that is missing?"),
                ("priorities", "Which three fixes matter most before submission?"),
            ),
            "These are pattern-based findings on the user's draft, not an assessment. Confirm each against the "
            "excerpt; 'unclear' means the heuristics found no problem, not that the item is satisfied.",
        ),
        extraction_confidence=_contract.extraction_confidence("draft", length=len(text)),
        limitations=["Pattern-based detection: paraphrased problems are missed and some flags are false positives."],
        venue=v,
        output_format=fmt,
    )
    report.rendered_markdown = _contract.render_markdown(report, f"Manuscript draft audit ({source_label})")
    if fmt == "latex":
        lines = [f"\\section*{{Manuscript Audit ({v.upper()}) --- {source_label}}}", "\\begin{itemize}"]
        for sig in signals:
            quote = sig.excerpts[0].text if sig.excerpts else ""
            lines.append(
                f"\\item \\textbf{{[{sig.category}, {sig.priority_hint}]}} \\texttt{{{quote}}} --- "
                f"{sig.description} \\emph{{{sig.detail}}}"
            )
        if not signals:
            lines.append("\\item No pattern-based findings.")
        lines.append("\\end{itemize}")
        report.rendered_report = "\n".join(lines)
    else:
        report.rendered_report = report.rendered_markdown
    return report.model_dump(mode="json")
