"""Phase 5 Multi-Persona Peer-Review Engine (#17 AI conference reviewer, #20 domain-adaptive review committee).

Deterministic, stdlib-only critical audit across 3 specialized personas per scientific domain
(Computer Science / NeurIPS-ICLR, Biomedical / CONSORT-STROBE-PRISMA, Economics / Causal Econometrics,
and General STEM), consolidated by a Meta-Reviewer into a structured PeerReviewReport."""

import re
from typing import Any

from noul import errors as _errors
from noul.prompts import rubrics_biomedical, rubrics_cs, rubrics_economics
from noul.providers import artifact_radar, domain_classifier, pdf_parser
from noul.schemas.domain import ReviewVerdict, ScientificDomain
from noul.schemas.review import PeerReviewReport, PersonaEvaluation
from noul.search import engine as _engine
from noul.tools import hardware_tools, technical_tools

GENERAL_STEM_RUBRIC_NAME = "General STEM Methodological & Quantitative Peer-Review Rubric"
GENERAL_STEM_PERSONAS: list[dict[str, Any]] = [
    {
        "role": "Experimental Design & Methodology Reviewer",
        "focus": "Control groups, experimental setup, instrumentation calibration, and validity of physical/empirical assumptions.",
        "default_questions": [
            "What control experiments isolate the primary mechanism from environmental or instrumental drift?",
            "Under what boundary conditions do the governing equations or experimental assumptions break down?",
        ],
    },
    {
        "role": "Quantitative & Statistical Auditor",
        "focus": "Error bars, uncertainty propagation, sample size, statistical significance, and sensitivity analysis.",
        "default_questions": [
            "Are error bars and uncertainty intervals propagated across all intermediate measurements?",
            "What is the sensitivity of the main quantitative conclusion to parameter perturbations?",
        ],
    },
    {
        "role": "Reproducibility & Open Data Reviewer",
        "focus": "Open availability of raw datasets, simulation scripts, measurement protocols, and independent replication readiness.",
        "default_questions": [
            "Are raw measurement logs and analysis scripts deposited in a persistent open repository (e.g. Zenodo/OSF)?",
            "Is the experimental protocol specified with sufficient detail for independent replication?",
        ],
    },
]


def _clamp_score(val: float) -> float:
    return round(max(1.0, min(5.0, float(val))), 2)


def _clip(text: str, limit: int = 220) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _has_section(sections: list, pattern: str) -> bool:
    rx = re.compile(pattern, re.I)
    return any(rx.search(s.get("title") or "") for s in (sections or []))


def _collect_evidence(paper_id: str, domain_Override: str | None, inspect_fulltext: bool) -> dict:
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise _errors.HTTPError(400, "paper_id must be a non-empty string")
    pid = paper_id.strip()
    paper = _engine.resolve_paper(pid)
    try:
        dom_enum = domain_classifier.resolve_domain(paper=paper, override_domain=domain_Override)
    except ValueError as e:
        raise _errors.HTTPError(400, str(e)) from e

    if inspect_fulltext:
        corpus = pdf_parser.get_paper_corpus(paper["id"])
    else:
        text = "\n\n".join(x for x in (paper.get("title") or "", paper.get("abstract") or "") if x)
        corpus = pdf_parser.build_corpus(text, paper=paper, source="abstract")

    text = corpus["text"] or ""
    artifacts = artifact_radar.scan_artifacts_from_paper(
        dict(paper), fulltext=text if inspect_fulltext else "", verify_http=False
    )
    failures = technical_tools.detect_failure_modes(paper["id"], inspect_fulltext=bool(inspect_fulltext))
    math_info = technical_tools.extract_paper_math(paper["id"], mode="both")
    benchmarks = technical_tools.extract_benchmark_tables(paper["id"])
    ablations = technical_tools.analyze_ablation_tables(paper["id"])
    prompts = technical_tools.extract_prompts_and_decoding(paper["id"])
    claims = technical_tools.verify_paper_claims(paper["id"])
    hw = hardware_tools.estimate_hardware_specs(paper["id"])

    return {
        "paper": paper,
        "domain": dom_enum,
        "corpus": corpus,
        "text": text,
        "artifacts": artifacts,
        "failures": failures,
        "math": math_info,
        "benchmarks": benchmarks,
        "ablations": ablations,
        "prompts": prompts,
        "claims": claims,
        "hardware": hw,
    }


def _unverified_claim_strings(claims_res: dict) -> list[str]:
    out = []
    for cl in claims_res.get("claims") or []:
        if cl.get("status") in ("extrapolated", "partially_supported"):
            out.append(f"[{cl['status']}] {_clip(cl.get('claim'), 180)} — {cl.get('citation_tag', '')}")
    return out[:4]


# --------------------------------------------------------------------------- Computer Science (NeurIPS / ICLR)


def _evaluate_cs(ev: dict) -> list[PersonaEvaluation]:
    text = ev["text"]
    sections = ev["corpus"]["sections"]
    math_info = ev["math"]
    bench = ev["benchmarks"]
    abl = ev["ablations"]
    art = ev["artifacts"]
    failures = ev["failures"]
    hw = ev["hardware"]
    prompts = ev["prompts"]
    unverified = _unverified_claim_strings(ev["claims"])

    # Persona 1: Theoretical Reviewer (NeurIPS/ICLR)
    p1 = rubrics_cs.PERSONAS[0]
    p1_strengths, p1_flaws, p1_questions = [], [], list(p1["default_questions"])
    eq_n = math_info.get("equation_count", 0)
    algo_n = math_info.get("algorithm_count", 0)
    if eq_n > 0 or algo_n > 0:
        labels = [it["label"] for it in (math_info.get("items") or [])[:3]]
        p1_strengths.append(
            f"Formal mathematical specification present: extracted {eq_n} equation(s) and {algo_n} algorithm block(s) "
            f"({', '.join(labels)})."
        )
    if re.search(r"\b(?:theorem|lemma|proposition|proof|convergence|regret\s+bound|lipschitz|convex)\b", text, re.I):
        p1_strengths.append("Manuscript discusses formal theoretical properties (theorem/proof/convergence bounds).")
    else:
        p1_flaws.append(
            "Section omitted / not reported: no formal theorem, proof, or convergence guarantee provided for the proposed method."
        )
    if eq_n == 0 and algo_n == 0:
        p1_flaws.append(
            "Section omitted / not reported: no explicit mathematical equations or algorithmic pseudocode found in the analyzed text."
        )
    if not re.search(r"\bO\([^)]+\)|\bcomplexity\b|\bscaling\b|\bFLOPs?\b", text, re.I):
        p1_flaws.append(
            "Omitted analysis: asymptotic computational/memory complexity (e.g. Big-O scaling or FLOPs) is not formally derived."
        )
    for assump in (failures.get("critical_assumptions") or [])[:2]:
        p1_flaws.append(f'Unvalidated theoretical/modeling assumption in text: "{_clip(assump, 160)}"')
        p1_questions.append(f"How sensitive is the method when the assumption '{_clip(assump, 100)}' is violated?")
    if not p1_strengths:
        p1_strengths.append(
            f'Problem formulation stated in manuscript abstract/introduction ("{_clip(ev["paper"].get("title"), 120)}").'
        )

    p1_score = (
        3.2 + (0.6 if eq_n + algo_n > 0 else -0.6) + (0.4 if len(p1_flaws) <= 1 else -0.3 * min(len(p1_flaws), 3))
    )

    # Persona 2: Skeptical Empirical Reviewer (NeurIPS/ICLR)
    p2 = rubrics_cs.PERSONAS[1]
    p2_strengths, p2_flaws, p2_questions = [], [], list(p2["default_questions"])
    tables = bench.get("tables") or []
    abl_entries = abl.get("ablations") or []
    if tables:
        metrics_list = (bench.get("normalized_summary") or {}).get("metrics") or []
        p2_strengths.append(
            f"Quantitative evaluation reported across {len(tables)} benchmark table(s) covering metrics: "
            f"{', '.join(metrics_list[:6]) or 'reported metrics'}."
        )
    else:
        p2_flaws.append("Section omitted / not reported: no structured benchmark comparison table found in manuscript.")
    if abl_entries:
        top_abl = abl_entries[0]
        p2_strengths.append(
            f"Component ablation study present ({len(abl_entries)} ablated entries; top impact: "
            f"'{top_abl['component']}' on {top_abl['metric']} with Δ% = {top_abl['delta_percent']:+.2f}%)."
        )
        for alert in (abl.get("warnings_and_alerts") or [])[:2]:
            p2_flaws.append(f"Ablation audit alert: {alert}")
    else:
        p2_flaws.append(
            "Section omitted / not reported: no ablation study ('w/o component' or component contribution table) found in manuscript."
        )
    if not re.search(
        r"(?:±|\+/-|\bstd\b|\bstandard\s+deviation\b|\bconfidence\s+interval\b|\bmultiple\s+seeds?\b|\b\d+\s+seeds?\b)",
        text,
        re.I,
    ):
        p2_flaws.append(
            "Omitted statistical rigor: error bars, multi-seed variance (± std), or significance tests across runs are not reported."
        )
    for bsup in (failures.get("baseline_superiority_scenarios") or [])[:2]:
        p2_flaws.append(f"Baseline superiority / competitive weakness: {_clip(bsup, 180)}")
    for fm in (failures.get("failure_modes") or [])[:2]:
        p2_flaws.append(
            f"Empirical failure mode [{fm['category']}, severity={fm['severity']}]: {_clip(fm['description'], 170)}"
        )
    if not _has_section(sections, r"limitation|error\s+analysis|failure|threat"):
        p2_flaws.append(
            "Section omitted: dedicated 'Limitations' or 'Error Analysis' section not found in document structure."
        )
    if not p2_strengths:
        p2_strengths.append("Empirical claims and target task are outlined in the paper summary.")

    p2_score = 3.0 + (0.6 if tables else -0.5) + (0.6 if abl_entries else -0.5) - 0.2 * min(len(p2_flaws), 3)

    # Persona 3: Systems & Reproducibility Engineer (NeurIPS/ICLR)
    p3 = rubrics_cs.PERSONAS[2]
    p3_strengths, p3_flaws, p3_questions = [], [], list(p3["default_questions"])
    if art.get("has_code"):
        p3_strengths.append(f"Open-source code repository linked in manuscript ({art.get('code_url')}).")
    else:
        p3_flaws.append("Reproducibility omission: no public code repository (GitHub/GitLab) linked in the paper.")
    if art.get("has_weights"):
        p3_strengths.append(f"Pretrained model weights / checkpoints linked ({art.get('weights_url')}).")
    if art.get("evasive_availability_statement"):
        p3_flaws.append(
            "Evasive availability statement detected: code or data declared 'available upon reasonable request' rather than openly hosted."
        )
    fp16_vram = (hw.get("fp16") or {}).get("recommended_min_vram_gb")
    int4_vram = (hw.get("int4") or {}).get("recommended_min_vram_gb")
    tier = hw.get("operational_tier")
    p3_strengths.append(
        f"Hardware footprint profile ({hw.get('total_parameters_billion')}B params, {hw.get('architecture_type')}): "
        f"requires ~{fp16_vram} GB VRAM in FP16 (~{int4_vram} GB in INT4), tier='{tier}'."
    )
    if not hw.get("reported_training_hardware"):
        p3_flaws.append(
            "Section omitted / not reported: training/inference GPU hardware specification and wall-clock compute budget not explicitly stated."
        )
    if not (prompts.get("hyperparameters") or {}):
        p3_flaws.append(
            "Omitted reproducibility details: decoding/training hyperparameters (temperature, top_p, seed, learning rate) not specified in text."
        )
    else:
        hp_keys = ", ".join(sorted((prompts.get("hyperparameters") or {}).keys()))
        p3_strengths.append(f"Hyperparameters explicitly documented ({hp_keys}).")

    p3_score = (
        3.1
        + (0.7 if art.get("has_code") else -0.6)
        + (0.3 if prompts.get("hyperparameters") else -0.2)
        - (0.5 if art.get("evasive_availability_statement") else 0.0)
    )

    return [
        PersonaEvaluation(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unverified_claims=unverified,
            unanswered_questions=p1_questions[:3],
            score=_clamp_score(p1_score),
            confidence_score=4.2 if ev["corpus"]["source"] == "fulltext" else 3.5,
        ),
        PersonaEvaluation(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unverified_claims=unverified,
            unanswered_questions=p2_questions[:3],
            score=_clamp_score(p2_score),
            confidence_score=4.3 if tables else 3.6,
        ),
        PersonaEvaluation(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unverified_claims=unverified,
            unanswered_questions=p3_questions[:3],
            score=_clamp_score(p3_score),
            confidence_score=4.0,
        ),
    ]


# --------------------------------------------------------------------------- Biomedical (CONSORT / STROBE / PRISMA)


def _evaluate_biomedical(ev: dict) -> list[PersonaEvaluation]:
    text = ev["text"]
    paper = ev["paper"]
    evidence_lvl = paper.get("evidence") or "other"
    failures = ev["failures"]
    art = ev["artifacts"]
    unverified = _unverified_claim_strings(ev["claims"])

    # Persona 1: Clinical Trial & Study Design Methodologist (CONSORT/STROBE)
    p1 = rubrics_biomedical.PERSONAS[0]
    p1_strengths, p1_flaws, p1_questions = [], [], list(p1["default_questions"])
    if evidence_lvl in ("rct", "meta-analysis", "systematic-review"):
        p1_strengths.append(
            f"High-evidence study design detected: '{evidence_lvl}' (top tier of the clinical evidence pyramid)."
        )
    elif evidence_lvl == "cohort":
        p1_strengths.append("Observational cohort study design identified; evaluates longitudinal outcomes.")
    if re.search(r"(?<!without\s)(?<!no\s)\brandomi[sz](?:ed|ation)\b", text, re.I):
        p1_strengths.append("Randomization procedure explicitly mentioned in the manuscript.")
    else:
        p1_flaws.append(
            "Control / design omission: randomization procedure and allocation concealment are not reported in the manuscript."
        )
    if re.search(
        r"\b(?:double[-\s]blind|single[-\s]blind|placebo[-\s]controlled|masked|blinding)\b", text, re.I
    ) and not re.search(
        r"\b(?:without|no|unblinded|lack\s+of)\s+(?:[a-z-]+\s+){0,3}?(?:blinding|masking)\b", text, re.I
    ):
        p1_strengths.append("Blinding / masking or placebo-control protocol reported.")
    else:
        p1_flaws.append(
            "Section / control omitted: double-blinding / outcome-assessor masking and placebo control are not reported in text."
        )
    if not re.search(
        r"\b(?:NCT\d{6,}|ISRCTN\d+|PROSPERO|CRD\d+|pre[-\s]?registered|trial\s+registration|ethics\s+committee|IRB)\b",
        text,
        re.I,
    ):
        p1_flaws.append(
            "CONSORT/PRISMA omission: no clinical trial registration identifier (e.g. NCT/PROSPERO) or protocol pre-registration reported."
        )
    if not re.search(r"\b(?:inclusion\s+criteria|exclusion\s+criteria|eligibility)\b", text, re.I):
        p1_flaws.append(
            "CONSORT/STROBE omission: explicit participant inclusion and exclusion eligibility criteria are not detailed in the analyzed text."
        )
    if not p1_strengths:
        p1_strengths.append(f"Biomedical research question and study scope defined ({evidence_lvl} design).")

    p1_score = (
        3.0
        + (0.7 if evidence_lvl in ("rct", "meta-analysis", "systematic-review") else -0.3)
        - 0.25 * min(len(p1_flaws), 3)
    )

    # Persona 2: Biostatistician & Statistical Power Auditor
    p2 = rubrics_biomedical.PERSONAS[1]
    p2_strengths, p2_flaws, p2_questions = [], [], list(p2["default_questions"])
    m_n = re.search(
        r"\b[nN]\s*=\s*(\d[\d,]*)|\b(\d[\d,]*)\s+(?:patients|participants|subjects|adults|cases)\b", text, re.I
    )
    if m_n:
        n_str = (m_n.group(1) or m_n.group(2)).replace(",", "")
        p2_strengths.append(f"Sample size explicitly reported in text (N = {n_str}).")
        try:
            if int(n_str) < 50:
                p2_flaws.append(
                    f"Small sample size warning (N = {n_str}): high risk of Type II error and inflated effect sizes."
                )
        except ValueError:
            pass
    else:
        p2_flaws.append(
            "Statistical omission: exact sample size (N) per study arm is not reported in the analyzed text."
        )

    if re.search(
        r"\b(?:power\s+calculation|statistical\s+power|1\s*-\s*\\?beta|80%\s+power|90%\s+power|sample\s+size\s+calculation)\b",
        text,
        re.I,
    ) and not re.search(
        r"\b(?:without|no|lack\s+of)\s+(?:[a-z-]+\s+){0,5}?(?:power\s+calculation|sample\s+size\s+calculation)\b",
        text,
        re.I,
    ):
        p2_strengths.append("A priori statistical power (1-beta) / sample-size justification reported.")
    else:
        p2_flaws.append(
            "Section omitted / not reported: sample-size power calculation (1-beta) and minimum detectable effect size are missing."
        )
    if re.search(
        r"\b(?:95%\s*CI|confidence\s+interval|hazard\s+ratio|odds\s+ratio|relative\s+risk|\bp\s*[<=>])\b", text, re.I
    ):
        p2_strengths.append("Effect estimates with confidence intervals (95% CI) or p-values are reported.")
    else:
        p2_flaws.append(
            "Biostatistical omission: 95% confidence intervals (CI) and exact p-values for primary endpoints are not reported."
        )
    if not re.search(
        r"\b(?:intention[-\s]to[-\s]treat|\bITT\b|per[-\s]protocol|attrition|loss\s+to\s+follow[-\s]up|missing\s+data)\b",
        text,
        re.I,
    ):
        p2_flaws.append(
            "CONSORT omission: intention-to-treat (ITT) analysis and attrition / loss-to-follow-up accounting are not reported."
        )
    if not p2_strengths:
        p2_strengths.append("Quantitative biomedical outcome measures are referenced in the study.")

    p2_score = 3.1 + (0.4 if m_n else -0.5) - 0.25 * min(len(p2_flaws), 3)

    # Persona 3: Translational Medicine & Clinical Safety Reviewer
    p3 = rubrics_biomedical.PERSONAS[2]
    p3_strengths, p3_flaws, p3_questions = [], [], list(p3["default_questions"])
    if evidence_lvl == "preclinical" or re.search(
        r"\b(?:in\s+vitro|mice|murine|rat\s+model|animal\s+model|cell\s+line|xenograft)\b", text, re.I
    ):
        p3_flaws.append(
            "Translational gap warning: preclinical study (in vitro / animal model) — efficacy and dosing cannot be directly extrapolated to human patients."
        )
    else:
        p3_strengths.append("Human clinical or population-level investigation with translational relevance.")
    if re.search(
        r"\b(?:adverse\s+events?|side\s+effects?|toxicity|safety\s+profile|tolerability|serious\s+adverse)\b",
        text,
        re.I,
    ):
        p3_strengths.append("Clinical safety, tolerability, or adverse event (AE) profile is addressed.")
    else:
        p3_flaws.append(
            "Safety omission: adverse events (AEs), toxicity grade, and safety monitoring are not reported in the analyzed text."
        )
    for fm in (failures.get("failure_modes") or [])[:2]:
        p3_flaws.append(f"Clinical/population limitation [{fm['category']}]: {_clip(fm['description'], 170)}")
    if art.get("has_dataset") or art.get("has_code"):
        p3_strengths.append("Open clinical data or analysis scripts linked for independent verification.")
    elif art.get("evasive_availability_statement"):
        p3_flaws.append("Data availability concern: de-identified patient data only 'available upon request'.")

    p3_score = 3.0 + (0.4 if len(p3_strengths) >= 2 else 0.0) - 0.3 * min(len(p3_flaws), 3)

    return [
        PersonaEvaluation(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unverified_claims=unverified,
            unanswered_questions=p1_questions[:3],
            score=_clamp_score(p1_score),
            confidence_score=4.1,
        ),
        PersonaEvaluation(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unverified_claims=unverified,
            unanswered_questions=p2_questions[:3],
            score=_clamp_score(p2_score),
            confidence_score=4.2,
        ),
        PersonaEvaluation(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unverified_claims=unverified,
            unanswered_questions=p3_questions[:3],
            score=_clamp_score(p3_score),
            confidence_score=4.0,
        ),
    ]


# --------------------------------------------------------------------------- Economics (Econometrics & Causal ID)


def _evaluate_economics(ev: dict) -> list[PersonaEvaluation]:
    text = ev["text"]
    failures = ev["failures"]
    art = ev["artifacts"]
    math_info = ev["math"]
    bench = ev["benchmarks"]
    unverified = _unverified_claim_strings(ev["claims"])

    # Persona 1: Causal Identification Econometrician
    p1 = rubrics_economics.PERSONAS[0]
    p1_strengths, p1_flaws, p1_questions = [], [], list(p1["default_questions"])
    m_id = re.search(
        r"\b(difference[-\s]in[-\s]differences|diff[-\s]in[-\s]diff|\bDiD\b|instrumental\s+variables?|\b2SLS\b|"
        r"regression\s+discontinuity|\bRDD\b|natural\s+experiment|event\s+study|synthetic\s+control|"
        r"fixed\s+effects?|panel\s+data)\b",
        text,
        re.I,
    )
    if m_id:
        p1_strengths.append(f"Explicit empirical identification strategy detected: '{m_id.group(1)}'.")
    else:
        p1_flaws.append(
            "Identification omission: no quasi-experimental causal identification strategy (DiD, IV/2SLS, RDD, or panel fixed effects) explicitly stated."
        )
    if math_info.get("equation_count", 0) > 0:
        p1_strengths.append(
            f"Structural or reduced-form econometric specification formalized ({math_info['equation_count']} equation(s))."
        )
    if not re.search(
        r"\b(?:parallel\s+trends?|exclusion\s+restriction|exogen(?:ous|eity)|omitted\s+variable|endogeneity|pre[-\s]?trends?)\b",
        text,
        re.I,
    ):
        p1_flaws.append(
            "Section omitted / not reported: formal discussion of endogeneity, omitted variable bias (OVB), or parallel-trends / exclusion-restriction validity is missing."
        )
    else:
        p1_strengths.append("Addresses identifying assumptions (parallel trends / exogeneity / endogeneity threats).")

    for assump in (failures.get("critical_assumptions") or [])[:2]:
        p1_flaws.append(f'Econometric assumption requiring verification: "{_clip(assump, 160)}"')

    p1_score = 3.2 + (0.6 if m_id else -0.6) - 0.25 * min(len(p1_flaws), 3)

    # Persona 2: Empirical Robustness & Inference Auditor
    p2 = rubrics_economics.PERSONAS[1]
    p2_strengths, p2_flaws, p2_questions = [], [], list(p2["default_questions"])
    if re.search(
        r"\b(?:cluster(?:ed)?\s+standard\s+errors?|robust\s+standard\s+errors?|heteroskedasticity|wild\s+bootstrap|two[-\s]way\s+cluster)\b",
        text,
        re.I,
    ):
        p2_strengths.append(
            "Inference accounts for error correlation via clustered or heteroskedasticity-robust standard errors."
        )
    else:
        p2_flaws.append(
            "Inference omission: no clustered standard errors or heteroskedasticity-robust inference procedure reported in manuscript."
        )
    if re.search(
        r"\b(?:placebo\s+test|falsification\s+test|robustness\s+check|alternative\s+specification|sub[-\s]?sample)\b",
        text,
        re.I,
    ):
        p2_strengths.append("Includes empirical robustness checks or placebo/falsification tests.")
    else:
        p2_flaws.append(
            "Section omitted / not reported: no placebo falsification test, pre-trend event study, or covariate sensitivity matrix reported."
        )
    if bench.get("tables"):
        p2_strengths.append(
            f"Empirical estimation results structured across {len(bench['tables'])} regression/summary table(s)."
        )
    if not p2_strengths:
        p2_strengths.append("Empirical dataset and quantitative estimation scope described.")

    p2_score = 3.0 + (0.5 if len(p2_strengths) >= 2 else -0.3) - 0.25 * min(len(p2_flaws), 3)

    # Persona 3: Policy & External Validity Economist
    p3 = rubrics_economics.PERSONAS[2]
    p3_strengths, p3_flaws, p3_questions = [], [], list(p3["default_questions"])
    if re.search(
        r"\b(?:policy|welfare|elasticity|percentage\s+points?|basis\s+points?|economic\s+significance|cost[-\s]benefit)\b",
        text,
        re.I,
    ):
        p3_strengths.append("Discusses economic magnitude, policy implications, or welfare/elasticity interpretation.")
    else:
        p3_flaws.append(
            "Omitted policy analysis: economic magnitude and real-world welfare significance beyond statistical p-values are not quantified."
        )
    if not re.search(r"\b(?:external\s+validity|general\s+equilibrium|spillover|SUTVA)\b", text, re.I):
        p3_flaws.append(
            "External validity omission: general equilibrium spillover effects (SUTVA) and institutional generalizability are not evaluated."
        )
    if art.get("has_code") or art.get("has_dataset"):
        p3_strengths.append("Open replication code or panel dataset linked in manuscript.")
    else:
        p3_flaws.append(
            "Replication omission: no public replication package (Stata/R/Python scripts and dataset) linked."
        )

    p3_score = 3.1 + (0.4 if art.get("has_code") or art.get("has_dataset") else -0.4) - 0.2 * min(len(p3_flaws), 3)

    return [
        PersonaEvaluation(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unverified_claims=unverified,
            unanswered_questions=p1_questions[:3],
            score=_clamp_score(p1_score),
            confidence_score=4.1,
        ),
        PersonaEvaluation(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unverified_claims=unverified,
            unanswered_questions=p2_questions[:3],
            score=_clamp_score(p2_score),
            confidence_score=4.1,
        ),
        PersonaEvaluation(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unverified_claims=unverified,
            unanswered_questions=p3_questions[:3],
            score=_clamp_score(p3_score),
            confidence_score=3.9,
        ),
    ]


# --------------------------------------------------------------------------- General STEM fallback


def _evaluate_general_stem(ev: dict) -> list[PersonaEvaluation]:
    text = ev["text"]
    math_info = ev["math"]
    bench = ev["benchmarks"]
    art = ev["artifacts"]
    failures = ev["failures"]
    unverified = _unverified_claim_strings(ev["claims"])

    evals = []
    for idx, p in enumerate(GENERAL_STEM_PERSONAS):
        strengths, flaws = [], []
        if idx == 0:
            if math_info.get("equation_count", 0) > 0:
                strengths.append(
                    f"Formal quantitative equations extracted ({math_info['equation_count']} equation(s))."
                )
            else:
                flaws.append("Section omitted: no formal mathematical model or governing equations reported.")
            for fm in (failures.get("failure_modes") or [])[:2]:
                flaws.append(f"Methodological limitation [{fm['category']}]: {_clip(fm['description'], 160)}")
            if not flaws:
                flaws.append(
                    "Control omission: explicit negative control or baseline calibration not detailed in text."
                )
        elif idx == 1:
            if bench.get("tables"):
                strengths.append(f"Structured quantitative tables present ({len(bench['tables'])} table(s)).")
            else:
                flaws.append("Section omitted: no structured quantitative comparison table found.")
            if not re.search(r"(?:±|\+/-|\bconfidence\s+interval|\berror\s+bar|\bstd\b)", text, re.I):
                flaws.append("Quantitative omission: uncertainty intervals / error bars are not reported.")
        else:
            if art.get("has_code") or art.get("has_dataset"):
                strengths.append("Open code or dataset artifacts linked.")
            else:
                flaws.append("Reproducibility omission: no open data or code repository linked in the manuscript.")
        if not strengths:
            strengths.append(f"Core scientific objective stated for '{_clip(ev['paper'].get('title'), 100)}'.")
        score = _clamp_score(3.1 + 0.3 * len(strengths) - 0.3 * len(flaws))
        evals.append(
            PersonaEvaluation(
                persona_role=p["role"],
                rubric_focus=p["focus"],
                strengths=strengths,
                critical_flaws=flaws,
                unverified_claims=unverified,
                unanswered_questions=list(p["default_questions"])[:2],
                score=score,
                confidence_score=3.8,
            )
        )
    return evals


def _format_markdown_report(report: PeerReviewReport) -> str:
    lines = [
        f"# Peer-Review Committee Report: {report.title or report.paper_id}",
        "",
        f"- **Paper ID:** `{report.paper_id}`",
        f"- **Domain Evaluated:** `{report.domain_evaluated}`",
        f"- **Rubric Applied:** {report.rubric_applied}",
        f"- **Verdict:** **{report.verdict.value.upper()}**",
        f"- **Overall Score:** **{report.overall_score:.2f} / 5.00**",
        "",
        "## Executive Summary",
        report.executive_summary,
        "",
        "## Meta-Reviewer Notes",
        report.meta_reviewer_notes,
        "",
        "## Multi-Persona Evaluations",
    ]
    for i, ev in enumerate(report.evaluations, 1):
        lines += [
            "",
            f"### Reviewer {i}: {ev.persona_role} (Score: {ev.score:.2f}/5.0, Confidence: {ev.confidence_score:.1f}/5.0)",
            f"_Focus: {ev.rubric_focus}_",
            "",
            "**Methodological Strengths:**",
        ]
        lines += [f"- {s}" for s in ev.strengths] or ["- None highlighted."]
        lines.append("")
        lines.append("**Critical Flaws & Omitted Controls:**")
        lines += [f"- {f}" for f in ev.critical_flaws] or ["- No critical flaws flagged."]
        if ev.unverified_claims:
            lines.append("")
            lines.append("**Unverified / Extrapolated Claims:**")
            lines += [f"- {c}" for c in ev.unverified_claims]
        lines.append("")
        lines.append("**Questions for the Authors:**")
        lines += [f"- {q}" for q in ev.unanswered_questions]

    lines += ["", "## Missing Experiments or Controls"]
    lines += [f"- {m}" for m in report.missing_experiments_or_controls] or ["- None identified."]
    lines += ["", "## Consolidated Unanswered Questions"]
    lines += [f"- {q}" for q in report.unanswered_questions] or ["- None."]
    return "\n".join(lines)


def review_paper(paper_id: str, domain: str | None = None, inspect_fulltext: bool = True) -> dict:
    """Run a domain-adaptive 3-persona peer-review committee (#17, #20) and consolidate via a Meta-Reviewer."""
    ev = _collect_evidence(paper_id, domain_Override=domain, inspect_fulltext=bool(inspect_fulltext))
    dom = ev["domain"]

    if dom == ScientificDomain.COMPUTER_SCIENCE:
        rubric_name = rubrics_cs.RUBRIC_NAME
        evaluations = _evaluate_cs(ev)
    elif dom == ScientificDomain.BIOMEDICAL:
        rubric_name = rubrics_biomedical.RUBRIC_NAME
        evaluations = _evaluate_biomedical(ev)
    elif dom == ScientificDomain.ECONOMICS:
        rubric_name = rubrics_economics.RUBRIC_NAME
        evaluations = _evaluate_economics(ev)
    else:
        rubric_name = GENERAL_STEM_RUBRIC_NAME
        evaluations = _evaluate_general_stem(ev)

    overall_score = round(sum(e.score for e in evaluations) / 3.0, 2)
    all_flaws = [f for e in evaluations for f in e.critical_flaws]
    severe_flaw_count = sum(
        1 for f in all_flaws if re.search(r"omitted|missing|no\s+public|evasive|preclinical", f, re.I)
    )

    if overall_score >= 4.0 and severe_flaw_count == 0:
        verdict = ReviewVerdict.ACCEPT
    elif overall_score >= 3.2:
        verdict = ReviewVerdict.ACCEPT_WITH_RESERVATIONS
    elif overall_score >= 2.3:
        verdict = ReviewVerdict.BORDERLINE
    else:
        verdict = ReviewVerdict.REJECT

    missing_controls = []
    for f in all_flaws:
        if f not in missing_controls and re.search(r"omitted|omission|not\s+reported|missing|no\s+", f, re.I):
            missing_controls.append(f)
    if not missing_controls and all_flaws:
        missing_controls = list(dict.fromkeys(all_flaws))[:4]

    questions = []
    for e in evaluations:
        for q in e.unanswered_questions:
            if q not in questions:
                questions.append(q)

    title = ev["paper"].get("title") or ev["paper"].get("id") or ""
    exec_summary = (
        f"Multi-persona peer review of '{title}' under the {rubric_name} ({dom.value}). "
        f"Consolidated score: {overall_score:.2f}/5.00 ({verdict.value}). "
        f"Audited {ev['corpus']['source']} corpus across {len(evaluations)} specialized reviewers, "
        f"identifying {sum(len(e.strengths) for e in evaluations)} methodological strengths and "
        f"{len(all_flaws)} critical flaws/omissions."
    )
    meta_notes = (
        f"Meta-Reviewer synthesis ({dom.value}): Roles engaged — "
        + ", ".join(e.persona_role for e in evaluations)
        + f". Primary decision driver: {missing_controls[0] if missing_controls else 'overall methodological balance'}."
    )

    report = PeerReviewReport(
        paper_id=str(ev["paper"].get("id") or paper_id),
        title=title,
        domain_evaluated=dom.value,
        rubric_applied=rubric_name,
        verdict=verdict,
        overall_score=overall_score,
        executive_summary=exec_summary,
        evaluations=evaluations,
        meta_reviewer_notes=meta_notes,
        missing_experiments_or_controls=missing_controls[:8],
        unanswered_questions=questions[:8],
        markdown_report="",
    )
    report.markdown_report = _format_markdown_report(report)
    out = report.model_dump(mode="json")
    if ev["corpus"].get("warnings"):
        out["warnings"] = list(ev["corpus"]["warnings"])
    return out


def review_paper_scientific(paper_id: str, domain: str | None = None, inspect_fulltext: bool = True) -> dict:
    """Alias for review_paper (#20 specification compatibility)."""
    return review_paper(paper_id=paper_id, domain=domain, inspect_fulltext=inspect_fulltext)
