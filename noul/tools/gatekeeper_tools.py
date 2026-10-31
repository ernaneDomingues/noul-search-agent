"""Phase 6 (#27): Pre-Submission & Desk-Reject Gatekeeper (`audit_submission_readiness`).

Executes a 4-stage deterministic audit:
1. Double-Blind Anonymity Audit (GitHub/GitLab/HF links, \\author blocks, PDF metadata, self-citations);
2. Venue-Specific Compliance Checklist (NeurIPS/ICML/ICLR/ACL/IEEE vs. Nature/Lancet/Elsevier);
3. Overclaiming & Uncalibrated Superlative Scanner;
4. Mandatory Section Verification & Readiness Score (0-100)."""

import re

from noul.schemas.authoring import GatekeeperCheckItem, SubmissionGatekeeperReport
from noul.tools import authoring_tools


def _core():
    import noul_core

    return noul_core


_DOUBLE_BLIND_VENUES = frozenset({"neurips", "icml", "iclr", "acl"})
_SUPPORTED_GATEKEEPER_VENUES = frozenset(
    {"neurips", "icml", "iclr", "acl", "ieee", "nature", "lancet", "elsevier", "aer", "general"}
)

_REPO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?P<host>github\.com|gitlab\.com|huggingface\.co|bitbucket\.org)/"
    r"(?P<owner>[A-Za-z0-9_.-]+)(?:/(?P<repo>[A-Za-z0-9_.-]+))?",
    re.I,
)
_ANON_OWNERS = frozenset(
    {"anonymous", "anon", "anonymous-author", "anonymous_authors", "double-blind", "blind-review", "anon-submission"}
)

_LATEX_AUTHOR_RE = re.compile(r"\\author\s*\{(?P<body>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", re.S)
_MD_AUTHOR_RE = re.compile(r"^(?:author|authors)\s*:\s*(?P<body>.+)$", re.I | re.M)
_PDF_META_RE = re.compile(r"\\hypersetup\s*\{[^}]*pdfauthor\s*=\s*\{(?P<author>[^}]+)\}", re.I | re.S)
_THANKS_GRANT_RE = re.compile(
    r"\\thanks\s*\{(?P<body>[^}]*(?:grant|university|institute|Supported\s+by|@)[^}]*)\}", re.I
)
_SELF_CITE_RE = re.compile(
    r"\b(?:"
    r"in\s+our\s+(?:previous|prior|earlier)\s+(?:work|paper|study)s?|"
    r"we\s+previously\s+(?:proposed|introduced|showed|demonstrated|developed)|"
    r"following\s+our\s+(?:earlier|previous|prior)\s+(?:work|paper)|"
    r"as\s+we\s+showed\s+in\s+\\cite|"
    r"builds\s+directly\s+on\s+our\s+prior"
    r")\b",
    re.I,
)

_OVERCLAIM_PATTERNS = (
    (
        "ovc_absolute_proof",
        re.compile(r"\b(?:proves?\s+beyond\s+(?:any\s+)?doubt|clearly\s+proves?|irrefutably\s+demonstrates?)\b", re.I),
        "high",
        "Uncalibrated absolute proof claim ('proves beyond doubt' / 'clearly proves') in an empirical context.",
        "Replace with calibrated empirical phrasing ('provides strong empirical evidence that...').",
    ),
    (
        "ovc_universal_sota",
        re.compile(
            r"\b(?:universally\s+outperforms?\s+all|solves?\s+the\s+problem\s+completely|"
            r"100%\s+accurate\s+in\s+all|eliminates?\s+all\s+(?:errors?|hallucinations?)|"
            r"zero\s+limitations?|first\s+ever\s+in\s+human\s+history|beats?\s+every\s+possible)\b",
            re.I,
        ),
        "high",
        "Universal / unbounded superiority or zero-error claim ('universally outperforms all' / 'eliminates all errors').",
        "Scope claims strictly to the evaluated benchmarks, datasets, and statistical confidence intervals.",
    ),
    (
        "ovc_unanchored_superlative",
        re.compile(r"\b(?:revolutionary|paradigm[-\s]shifting|groundbreaking|flawless\s+performance)\b", re.I),
        "medium",
        "Promotional hyperbole ('revolutionary' / 'flawless') likely to trigger reviewer skepticism.",
        "Use objective, metric-grounded terminology describing the specific technical trade-off.",
    ),
)


def _extract_pdf_fallback_bytes(raw_bytes: bytes) -> str:
    core = _core()
    txt = core._extract_pdf_text(raw_bytes)
    if txt and len(txt.strip()) >= 20:
        return txt
    # Fallback stdlib extraction for literal PDF text streams `( ... )` and readable ASCII lines
    decoded = raw_bytes.decode("latin-1", errors="ignore")
    chunks = re.findall(r"\(([^()]{3,300})\)", decoded)
    if chunks:
        joined = " ".join(chunks)
        if len(joined.strip()) >= 20:
            return joined
    ascii_runs = re.findall(r"[A-Za-z0-9\s.,;:/\\{}_^$%#@&+\-\[\]()'\"=<>!?]{12,}", decoded)
    return "\n".join(ascii_runs)


def _load_manuscript_text(draft_path: str) -> tuple[str, str]:
    core = _core()
    if not isinstance(draft_path, str) or not draft_path.strip():
        raise core.HTTPError(400, "draft_path must be a non-empty file path or manuscript text.")
    s = draft_path.strip()
    if authoring_tools._looks_like_filepath(s):
        path = authoring_tools._validate_workspace_path(s, must_exist=True)
        if path.suffix.lower() == ".pdf":
            raw = path.read_bytes()
            return _extract_pdf_fallback_bytes(raw), path.name
        return path.read_text(encoding="utf-8", errors="replace"), path.name
    return draft_path, "inline_manuscript"


def _audit_anonymity(text: str, is_double_blind: bool) -> list[GatekeeperCheckItem]:
    if not is_double_blind:
        return []
    violations: list[GatekeeperCheckItem] = []

    # 1. Non-anonymized repository URLs
    for m in _REPO_URL_RE.finditer(text or ""):
        owner = m.group("owner").lower()
        full_url = m.group(0)
        if owner not in _ANON_OWNERS:
            violations.append(
                GatekeeperCheckItem(
                    check_id="anon_repo_url",
                    category="anonymity",
                    passed=False,
                    severity="critical",
                    description=f"Identity-revealing repository URL detected ('{owner}' on {m.group('host')}).",
                    evidence_or_location=full_url,
                    remediation="Replace with an anonymized repository (e.g., https://anonymous.4open.science/r/...) or redact owner handle.",
                )
            )

    # 2. Explicit \author{...} or YAML author block with real names/emails/affiliations
    for m in _LATEX_AUTHOR_RE.finditer(text or ""):
        body = re.sub(r"\s+", " ", m.group("body")).strip()
        if body and not re.search(r"\banonymous\b", body, re.I):
            violations.append(
                GatekeeperCheckItem(
                    check_id="anon_author_block",
                    category="anonymity",
                    passed=False,
                    severity="critical",
                    description="LaTeX \\author{...} block contains non-anonymous author names, affiliations, or emails.",
                    evidence_or_location=f"\\author{{{body[:120]}}}",
                    remediation="Use \\author{Anonymous Author(s)} and enable the venue style's [submission] / [review] option.",
                )
            )

    for m in _MD_AUTHOR_RE.finditer(text or ""):
        body = m.group("body").strip()
        if body and not re.search(r"\banonymous\b", body, re.I):
            violations.append(
                GatekeeperCheckItem(
                    check_id="anon_header_author",
                    category="anonymity",
                    passed=False,
                    severity="critical",
                    description="Manuscript header lists non-anonymous author name(s) or affiliation(s).",
                    evidence_or_location=m.group(0).strip()[:120],
                    remediation="Replace author header with 'Authors: Anonymous Author(s)' for double-blind review.",
                )
            )

    # 3. PDF metadata / grant acknowledgments
    for m in _PDF_META_RE.finditer(text or ""):
        author_meta = m.group("author").strip()
        if author_meta and not re.search(r"\banonymous\b", author_meta, re.I):
            violations.append(
                GatekeeperCheckItem(
                    check_id="anon_pdf_metadata",
                    category="anonymity",
                    passed=False,
                    severity="critical",
                    description="LaTeX \\hypersetup{pdfauthor=...} embeds author identity into the compiled PDF metadata.",
                    evidence_or_location=m.group(0)[:120],
                    remediation="Remove pdfauthor from \\hypersetup or set pdfauthor={Anonymous}.",
                )
            )

    for m in _THANKS_GRANT_RE.finditer(text or ""):
        violations.append(
            GatekeeperCheckItem(
                check_id="anon_grant_acknowledgment",
                category="anonymity",
                passed=False,
                severity="critical",
                description="Identifying grant number, institution, or email found in \\thanks{...} footnote.",
                evidence_or_location=m.group(0)[:120],
                remediation="Suppress acknowledgments and grant numbers until the camera-ready version.",
            )
        )

    # 4. Self-revealing first-person citations
    for m in _SELF_CITE_RE.finditer(text or ""):
        start = max(0, m.start() - 20)
        end = min(len(text), m.end() + 60)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        violations.append(
            GatekeeperCheckItem(
                check_id="anon_self_citation",
                category="anonymity",
                passed=False,
                severity="critical",
                description="First-person self-citation ('In our previous work...' / 'We previously proposed...') breaks double-blind anonymity.",
                evidence_or_location=snippet,
                remediation="Refer to your prior work strictly in the third person ('Prior work by Smith et al. [1] proposed...').",
            )
        )

    return violations


def _audit_venue_checklist(text: str, venue: str) -> list[GatekeeperCheckItem]:
    bio_or_journal = venue in ("nature", "lancet", "elsevier", "aer")
    if bio_or_journal:
        specs = [
            (
                "chk_data_availability",
                "high",
                "Data & Code Availability Statement",
                re.compile(
                    r"\b(?:data\s+availability|code\s+availability|replication\s+package|data\s+are\s+available)\b",
                    re.I,
                ),
                "Include a dedicated 'Data Availability Statement' specifying repository accession numbers or access conditions.",
            ),
            (
                "chk_competing_interests",
                "high",
                "Competing Interests / Conflict of Interest Declaration",
                re.compile(r"\b(?:competing\s+interests?|conflicts?\s+of\s+interest|declare\s+no\s+competing)\b", re.I),
                "Add a 'Competing Interests' section declaring financial or non-financial conflicts for all authors.",
            ),
            (
                "chk_ethics_irb",
                "high",
                "Ethics Approval / IRB / Informed Consent Statement",
                re.compile(
                    r"\b(?:ethics\s+(?:approval|committee|statement)|institutional\s+review\s+board|\birb\b|informed\s+consent|declaration\s+of\s+helsinki)\b",
                    re.I,
                ),
                "Document IRB/Ethics Committee approval protocol numbers and participant informed consent (or state exemption).",
            ),
            (
                "chk_funding",
                "medium",
                "Funding & Role of the Funding Source Statement",
                re.compile(
                    r"\b(?:funding\s+(?:statement|source)|role\s+of\s+the\s+funding|supported\s+by\s+grant)\b", re.I
                ),
                "State funding sources and clarify the funder's role in study design, data collection, and publication.",
            ),
            (
                "chk_reporting_guideline",
                "medium",
                "Reporting Guideline / Trial or Registry Identifier (CONSORT/STROBE/PRISMA/AEA)",
                re.compile(
                    r"\b(?:consort|strobe|prisma|clinicaltrials\.gov|nct\d{6,}|aea\s+rct\s+registry|prospero)\b", re.I
                ),
                "Reference compliance with CONSORT/STROBE/PRISMA guidelines and provide trial/registry IDs where applicable.",
            ),
        ]
    else:
        specs = [
            (
                "chk_limitations",
                "high",
                "Explicit Limitations Declaration",
                re.compile(
                    r"(?:\\(?:sub)*section\*?\{[^}]*Limitations|\\paragraph\{Limitations|^#{1,4}\s+[^\n]*Limitations|\bLimitations\s*:)",
                    re.I | re.M,
                ),
                "Add an explicit 'Limitations' section or subsection discussing failure modes, assumptions, and scope constraints.",
            ),
            (
                "chk_compute",
                "medium",
                "Compute Infrastructure & Runtime Disclosure (GPU/TPU/VRAM/Hours)",
                re.compile(r"\b(?:NVIDIA|A100|H100|V100|RTX|TPU|GPU|VRAM|GPU[-\s]hours|compute\s+resources)\b", re.I),
                "Report the type and quantity of GPUs/TPUs, VRAM footprint, and total training/inference compute hours.",
            ),
            (
                "chk_hyperparams_seeds",
                "high",
                "Hyperparameters & Random Seeds Specification",
                re.compile(
                    r"\b(?=.*(?:seed|random\s+seed))(?=.*(?:learning\s+rate|optimizer|adamw?|batch\s+size|hyperparameter)).*",
                    re.I | re.S,
                ),
                "Document both exact training hyperparameters (optimizer, lr, batch size) and the number of random seeds evaluated.",
            ),
            (
                "chk_error_bars",
                "high",
                "Statistical Dispersion (Error Bars / ± Std / Confidence Intervals)",
                re.compile(
                    r"(?:\\pm|±|\b95%\s*CI\b|\bstandard\s+deviation\b|\berror\s+bars?\b|\bp\s*[<=>]\s*0\.\d+)", re.I
                ),
                "Include error bars, ± standard deviation across seeds, or 95% confidence intervals in primary benchmark tables.",
            ),
            (
                "chk_licenses",
                "low",
                "Code & Dataset Asset Licensing (MIT/Apache/CC-BY)",
                re.compile(r"\b(?:MIT\s+License|Apache[-\s]2\.0|CC[-\s]BY|BSD\s+License|GPL|\blicense\b)\b", re.I),
                "Specify the license (e.g., MIT, Apache-2.0, CC-BY-4.0) for all used and released datasets/code artifacts.",
            ),
        ]

    items: list[GatekeeperCheckItem] = []
    for cid, sev, desc, rx, rem in specs:
        m = rx.search(text or "")
        passed = bool(m)
        ev = re.sub(r"\s+", " ", m.group(0))[:100] if m else "Not found in manuscript"
        items.append(
            GatekeeperCheckItem(
                check_id=cid,
                category="checklist",
                passed=passed,
                severity=sev,
                description=desc,
                evidence_or_location=ev,
                remediation="" if passed else rem,
            )
        )
    return items


def _audit_overclaiming(text: str) -> list[GatekeeperCheckItem]:
    flags: list[GatekeeperCheckItem] = []
    for cid, rx, sev, desc, rem in _OVERCLAIM_PATTERNS:
        for m in rx.finditer(text or ""):
            start = max(0, m.start() - 25)
            end = min(len(text), m.end() + 45)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            flags.append(
                GatekeeperCheckItem(
                    check_id=cid,
                    category="overclaiming",
                    passed=False,
                    severity=sev,
                    description=desc,
                    evidence_or_location=snippet,
                    remediation=rem,
                )
            )
    return flags


def _audit_mandatory_sections(text: str, venue: str) -> list[GatekeeperCheckItem]:
    required_sections = [
        (
            "sec_abstract",
            "Abstract",
            re.compile(r"(?:\\begin\{abstract\}|^#{1,3}\s*Abstract\b|\bAbstract\s*[:\n])", re.I | re.M),
        ),
        (
            "sec_intro",
            "Introduction",
            re.compile(r"(?:\\section\*?\{[^}]*Introduction|^#{1,3}\s*\d*\.?\s*Introduction\b)", re.I | re.M),
        ),
        (
            "sec_methods",
            "Methodology / Methods",
            re.compile(
                r"(?:\\section\*?\{[^}]*(?:Method|Approach|Formulation|Model|Identification)|^#{1,3}\s*\d*\.?\s*(?:Method|Approach|Formulation|Model))",
                re.I | re.M,
            ),
        ),
        (
            "sec_experiments",
            "Experiments / Results",
            re.compile(
                r"(?:\\section\*?\{[^}]*(?:Experiment|Result|Evaluation|Empirical)|^#{1,3}\s*\d*\.?\s*(?:Experiment|Result|Evaluation|Empirical))",
                re.I | re.M,
            ),
        ),
        (
            "sec_limitations",
            "Limitations",
            re.compile(
                r"(?:\\(?:sub)*section\*?\{[^}]*Limitations|\\paragraph\{Limitations|^#{1,4}\s*\d*\.?\s*Limitations\b|\bLimitations\s*:)",
                re.I | re.M,
            ),
        ),
        (
            "sec_references",
            "References / Bibliography",
            re.compile(
                r"(?:\\bibliography|\\begin\{thebibliography\}|\\section\*?\{References\}|^#{1,3}\s*References\b)",
                re.I | re.M,
            ),
        ),
    ]
    if venue in ("neurips", "icml", "iclr", "acl"):
        required_sections.append(
            (
                "sec_impact_or_ethics",
                "Broader Impact / Ethics / Reproducibility Statement",
                re.compile(
                    r"(?:\\(?:sub)*section\*?\{[^}]*(?:Broader\s+Impact|Impact\s+Statement|Ethics\s+Statement|Reproducibility|Paper\s+Checklist)|^#{1,4}\s*[^\n]*(?:Broader\s+Impact|Impact\s+Statement|Ethics\s+Statement|Reproducibility|Checklist))",
                    re.I | re.M,
                ),
            )
        )

    out: list[GatekeeperCheckItem] = []
    for cid, title, rx in required_sections:
        m = rx.search(text or "")
        passed = bool(m)
        out.append(
            GatekeeperCheckItem(
                check_id=cid,
                category="mandatory_section",
                passed=passed,
                severity="high" if cid in ("sec_limitations", "sec_impact_or_ethics", "sec_experiments") else "medium",
                description=f"Mandatory section: {title}",
                evidence_or_location=m.group(0).strip()[:80] if m else "Section missing",
                remediation=""
                if passed
                else f"Add an explicit '{title}' section to comply with {venue.upper()} submission guidelines.",
            )
        )
    return out


def audit_submission_readiness(draft_path: str, venue: str = "neurips") -> dict:
    """Issue #27: Run the 4-stage Pre-Submission & Desk-Reject Gatekeeper on a `.tex`, `.md`, or `.pdf`
    manuscript (or inline draft string) for the target `venue`."""
    core = _core()
    v = (venue or "neurips").strip().lower()
    if v not in _SUPPORTED_GATEKEEPER_VENUES:
        raise core.HTTPError(
            400, f"Unsupported venue '{venue}'. Choose from: {', '.join(sorted(_SUPPORTED_GATEKEEPER_VENUES))}"
        )

    text, source_label = _load_manuscript_text(draft_path)
    is_double_blind = v in _DOUBLE_BLIND_VENUES

    anon_violations = _audit_anonymity(text, is_double_blind)
    checklist_items = _audit_venue_checklist(text, v)
    overclaiming_flags = _audit_overclaiming(text)
    mandatory_secs = _audit_mandatory_sections(text, v)

    # Compute deterministic readiness_score (0-100)
    score = 100
    score -= 30 * len(anon_violations)
    missing_secs = [s for s in mandatory_secs if not s.passed]
    failed_chk = [c for c in checklist_items if not c.passed]
    score -= 12 * len(missing_secs)
    score -= 8 * len(failed_chk)
    score -= 6 * len(overclaiming_flags)
    score = max(0, min(100, score))

    if anon_violations or score < 50:
        risk = "critical"
    elif score < 80 or missing_secs:
        risk = "medium"
    else:
        risk = "low"

    corrective_actions: list[str] = []
    for av in anon_violations:
        corrective_actions.append(f"[CRITICAL ANONYMITY] {av.description} -> {av.remediation}")
    for ms in missing_secs:
        corrective_actions.append(f"[MANDATORY SECTION] {ms.description} -> {ms.remediation}")
    for fc in failed_chk:
        corrective_actions.append(f"[VENUE CHECKLIST] {fc.description} -> {fc.remediation}")
    for ov in overclaiming_flags:
        corrective_actions.append(f"[OVERCLAIMING] {ov.description} ({ov.evidence_or_location}) -> {ov.remediation}")

    md_lines = [
        f"# Pre-Submission Gatekeeper Audit (`{source_label}` — Venue: `{v.upper()}`)",
        "",
        f"- **Readiness Score:** `{score}/100`",
        f"- **Desk-Reject Risk:** `{risk.upper()}`",
        f"- **Double-Blind Anonymity Violations:** `{len(anon_violations)}`",
        f"- **Checklist Items Passed:** `{sum(1 for c in checklist_items if c.passed)}/{len(checklist_items)}`",
        f"- **Mandatory Sections Present:** `{sum(1 for s in mandatory_secs if s.passed)}/{len(mandatory_secs)}`",
        f"- **Overclaiming Flags:** `{len(overclaiming_flags)}`",
        "",
        "## 1. Double-Blind Anonymity Audit",
    ]
    if not is_double_blind:
        md_lines.append(f"- `{v.upper()}` does not enforce strict double-blind code/author anonymization by default.")
    elif anon_violations:
        for av in anon_violations:
            md_lines.append(
                f"- ❌ **[{av.severity.upper()}] {av.description}** (`{av.evidence_or_location}`)\n  - *Fix:* {av.remediation}"
            )
    else:
        md_lines.append("- ✅ Zero double-blind anonymity violations detected.")

    md_lines.extend(["", f"## 2. {v.upper()} Compliance Checklist"])
    for c in checklist_items:
        icon = "✅" if c.passed else "❌"
        md_lines.append(f"- {icon} **{c.description}**" + (f" — *{c.remediation}*" if not c.passed else ""))

    md_lines.extend(["", "## 3. Mandatory Sections Verification"])
    for s in mandatory_secs:
        icon = "✅" if s.passed else "❌"
        md_lines.append(f"- {icon} **{s.description}**" + (f" — *{s.remediation}*" if not s.passed else ""))

    md_lines.extend(["", "## 4. Overclaiming & Calibration Scanner"])
    if overclaiming_flags:
        for ov in overclaiming_flags:
            md_lines.append(
                f"- ⚠️ **[{ov.severity.upper()}] {ov.description}** (`{ov.evidence_or_location}`)\n  - *Fix:* {ov.remediation}"
            )
    else:
        md_lines.append("- ✅ Claims are well-calibrated; no unsubstantiated superlatives detected.")

    if corrective_actions:
        md_lines.extend(["", "## 5. Mandatory Corrective Actions Before Submission"])
        for idx, act in enumerate(corrective_actions, 1):
            md_lines.append(f"{idx}. {act}")

    report = SubmissionGatekeeperReport(
        venue=v,
        readiness_score=score,
        desk_reject_risk=risk,
        anonymity_violations=anon_violations,
        venue_checklist=checklist_items,
        overclaiming_flags=overclaiming_flags,
        mandatory_sections=mandatory_secs,
        mandatory_corrective_actions=corrective_actions,
        summary_markdown="\n".join(md_lines),
    )
    return report.model_dump()
