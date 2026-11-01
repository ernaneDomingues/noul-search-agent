"""Phase 5 Multi-Persona Peer-Review Engine (#17 AI conference reviewer, #20 domain-adaptive review committee).

Deterministic, stdlib-only critical audit across 3 specialized personas per scientific domain
(Computer Science / NeurIPS-ICLR, Biomedical / CONSORT-STROBE-PRISMA, Economics / Causal Econometrics,
and General STEM), consolidated by a Meta-Reviewer into a structured PeerReviewReport."""

import re
from dataclasses import dataclass, field
from typing import Any

from noul import errors as _errors
from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.prompts import rubrics_biomedical, rubrics_cs, rubrics_economics
from noul.providers import artifact_radar, domain_classifier, pdf_parser
from noul.schemas.domain import ScientificDomain
from noul.schemas.evidence import AnalysisSubject, Excerpt, PriorityHint, Signal, SignalKind
from noul.schemas.review import PeerReviewReport, ReviewPerspective
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


@dataclass
class _Persona:
    """One reviewer persona's heuristic findings. `strengths` / `critical_flaws` items are a description, or a
    `(description, excerpt, ...)` tuple when the finding rests on text quoted from the paper."""

    persona_role: str
    rubric_focus: str = ""
    strengths: list = field(default_factory=list)
    critical_flaws: list = field(default_factory=list)
    unanswered_questions: list = field(default_factory=list)


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


# --------------------------------------------------------------------------- Computer Science (NeurIPS / ICLR)


def _evaluate_cs(ev: dict) -> list[_Persona]:
    text = ev["text"]
    sections = ev["corpus"]["sections"]
    math_info = ev["math"]
    bench = ev["benchmarks"]
    abl = ev["ablations"]
    art = ev["artifacts"]
    failures = ev["failures"]
    hw = ev["hardware"]
    prompts = ev["prompts"]

    # Persona 1: Theoretical Reviewer (NeurIPS/ICLR)
    p1 = rubrics_cs.PERSONAS[0]
    p1_strengths: list = []
    p1_flaws: list = []
    p1_questions: list = list(p1["default_questions"])
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
        p1_flaws.append(("Theoretical/modeling assumption stated without validation in the text", assump))
    if failures.get("critical_assumptions"):
        p1_questions.append("How sensitive is the method when the stated modeling assumptions are violated?")
    if not p1_strengths:
        p1_strengths.append("Problem formulation stated in the abstract/introduction.")

    # Persona 2: Skeptical Empirical Reviewer (NeurIPS/ICLR)
    p2 = rubrics_cs.PERSONAS[1]
    p2_strengths: list = []
    p2_flaws: list = []
    p2_questions: list = list(p2["default_questions"])
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
        p2_flaws.append(("A baseline may match or beat the proposed method here", bsup))
    for fm in (failures.get("signals") or [])[:2]:
        p2_flaws.append((f"Candidate empirical failure mode ({fm['category']})", *fm["excerpts"][:1]))
    if not _has_section(sections, r"limitation|error\s+analysis|failure|threat"):
        p2_flaws.append(
            "Section omitted: dedicated 'Limitations' or 'Error Analysis' section not found in document structure."
        )
    if not p2_strengths:
        p2_strengths.append("Empirical claims and target task are outlined in the paper summary.")

    # Persona 3: Systems & Reproducibility Engineer (NeurIPS/ICLR)
    p3 = rubrics_cs.PERSONAS[2]
    p3_strengths: list = []
    p3_flaws: list = []
    p3_questions: list = list(p3["default_questions"])
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

    return [
        _Persona(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unanswered_questions=p1_questions[:3],
        ),
        _Persona(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unanswered_questions=p2_questions[:3],
        ),
        _Persona(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unanswered_questions=p3_questions[:3],
        ),
    ]


# --------------------------------------------------------------------------- Biomedical (CONSORT / STROBE / PRISMA)


def _evaluate_biomedical(ev: dict) -> list[_Persona]:
    text = ev["text"]
    paper = ev["paper"]
    evidence_lvl = paper.get("evidence") or "other"
    failures = ev["failures"]
    art = ev["artifacts"]

    # Persona 1: Clinical Trial & Study Design Methodologist (CONSORT/STROBE)
    p1 = rubrics_biomedical.PERSONAS[0]
    p1_strengths: list = []
    p1_flaws: list = []
    p1_questions: list = list(p1["default_questions"])
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

    # Persona 2: Biostatistician & Statistical Power Auditor
    p2 = rubrics_biomedical.PERSONAS[1]
    p2_strengths: list = []
    p2_flaws: list = []
    p2_questions: list = list(p2["default_questions"])
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

    # Persona 3: Translational Medicine & Clinical Safety Reviewer
    p3 = rubrics_biomedical.PERSONAS[2]
    p3_strengths: list = []
    p3_flaws: list = []
    p3_questions: list = list(p3["default_questions"])
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
    for fm in (failures.get("signals") or [])[:2]:
        p3_flaws.append((f"Candidate clinical/population limitation ({fm['category']})", *fm["excerpts"][:1]))
    if art.get("has_dataset") or art.get("has_code"):
        p3_strengths.append("Open clinical data or analysis scripts linked for independent verification.")
    elif art.get("evasive_availability_statement"):
        p3_flaws.append("Data availability concern: de-identified patient data only 'available upon request'.")

    return [
        _Persona(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unanswered_questions=p1_questions[:3],
        ),
        _Persona(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unanswered_questions=p2_questions[:3],
        ),
        _Persona(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unanswered_questions=p3_questions[:3],
        ),
    ]


# --------------------------------------------------------------------------- Economics (Econometrics & Causal ID)


def _evaluate_economics(ev: dict) -> list[_Persona]:
    text = ev["text"]
    failures = ev["failures"]
    art = ev["artifacts"]
    math_info = ev["math"]
    bench = ev["benchmarks"]

    # Persona 1: Causal Identification Econometrician
    p1 = rubrics_economics.PERSONAS[0]
    p1_strengths: list = []
    p1_flaws: list = []
    p1_questions: list = list(p1["default_questions"])
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
        p1_flaws.append(("Econometric assumption that needs verification", assump))

    # Persona 2: Empirical Robustness & Inference Auditor
    p2 = rubrics_economics.PERSONAS[1]
    p2_strengths: list = []
    p2_flaws: list = []
    p2_questions: list = list(p2["default_questions"])
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

    # Persona 3: Policy & External Validity Economist
    p3 = rubrics_economics.PERSONAS[2]
    p3_strengths: list = []
    p3_flaws: list = []
    p3_questions: list = list(p3["default_questions"])
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

    return [
        _Persona(
            persona_role=p1["role"],
            rubric_focus=p1["focus"],
            strengths=p1_strengths,
            critical_flaws=p1_flaws,
            unanswered_questions=p1_questions[:3],
        ),
        _Persona(
            persona_role=p2["role"],
            rubric_focus=p2["focus"],
            strengths=p2_strengths,
            critical_flaws=p2_flaws,
            unanswered_questions=p2_questions[:3],
        ),
        _Persona(
            persona_role=p3["role"],
            rubric_focus=p3["focus"],
            strengths=p3_strengths,
            critical_flaws=p3_flaws,
            unanswered_questions=p3_questions[:3],
        ),
    ]


# --------------------------------------------------------------------------- General STEM fallback


def _evaluate_general_stem(ev: dict) -> list[_Persona]:
    text = ev["text"]
    math_info = ev["math"]
    bench = ev["benchmarks"]
    art = ev["artifacts"]
    failures = ev["failures"]

    evals = []
    for idx, p in enumerate(GENERAL_STEM_PERSONAS):
        strengths: list = []
        flaws: list = []
        if idx == 0:
            if math_info.get("equation_count", 0) > 0:
                strengths.append(
                    f"Formal quantitative equations extracted ({math_info['equation_count']} equation(s))."
                )
            else:
                flaws.append("Section omitted: no formal mathematical model or governing equations reported.")
            for fm in (failures.get("signals") or [])[:2]:
                flaws.append((f"Candidate methodological limitation ({fm['category']})", *fm["excerpts"][:1]))
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
            strengths.append("Core scientific objective stated in the abstract.")
        evals.append(
            _Persona(
                persona_role=p["role"],
                rubric_focus=p["focus"],
                strengths=strengths,
                critical_flaws=flaws,
                unanswered_questions=list(p["default_questions"])[:2],
            )
        )
    return evals


_GAP = re.compile(
    r"omitted|omission|not\s+reported|not\s+found|missing|\bno\s+(?:public|formal|explicit|structured|ablation|open)",
    re.I,
)
_HIGH = re.compile(r"omitted|missing|no\s+public|evasive|preclinical", re.I)
_FINAL_CRITERION = (
    "recommendation",
    "Weighing all perspectives, what is your recommendation (e.g. accept / revise / reject) and its main reasons?",
    "Make the call yourself from the excerpts; heuristic gaps may be false negatives when only the abstract was "
    "available.",
)


def _finding(item) -> tuple[str, list[Excerpt]]:
    if isinstance(item, tuple):
        desc, *quotes = item
        return desc, [q if isinstance(q, Excerpt) else Excerpt.model_validate(q) for q in quotes if q]
    return item, []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def _claim_signals(claims_res: dict, start: int) -> list[Signal]:
    """Claims whose numbers or terms are not all found in the closest passage: for the client to check."""
    out: list[Signal] = []
    for cl in claims_res.get("claims") or []:
        if not (
            cl.get("numbers_missing") or cl.get("hedging_cues_missing_in_text") or cl.get("keyword_overlap", 1) < 0.5
        ):
            continue
        quotes = [Excerpt.model_validate(cl["best_match"])] if cl.get("best_match") else []
        if cl.get("claim_untrusted"):
            quotes.insert(0, _untrusted.excerpt(cl["claim"], paper_id=(quotes[0].paper_id if quotes else None)))
        out.append(
            Signal(
                id=f"c{start + len(out)}",
                category="claim_grounding",
                kind="concern",
                description="Claim whose numbers or wording are not all found in the closest passage",
                priority_hint="medium",
                detail=f"numbers missing: {', '.join(cl.get('numbers_missing') or []) or '—'}; "
                f"keyword overlap: {cl.get('keyword_overlap', 0):.2f}",
                excerpts=quotes,
            )
        )
        if len(out) >= 4:
            break
    return out


def review_paper(paper_id: str, domain: str | None = None, inspect_fulltext: bool = True) -> dict:
    """Collect the evidence for a domain-adaptive 3-persona peer review (#17, #20): signals per persona, questions
    for the authors and a rubric. The client's model writes the review and the recommendation."""
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

    paper = ev["paper"]
    pid = str(paper.get("id") or paper_id)
    signals: list[Signal] = []
    perspectives: list[ReviewPerspective] = []
    questions: list[str] = []
    for pi, persona in enumerate(evaluations, 1):
        ids: list[str] = []
        for source_kind, items in (("strength", persona.strengths), ("concern", persona.critical_flaws)):
            for item in items:
                desc, quotes = _finding(item)
                kind: SignalKind
                prio: PriorityHint
                if source_kind == "strength":
                    kind, prio = "strength", "low"
                else:
                    kind = "gap" if _GAP.search(desc) else "concern"
                    prio = "high" if _HIGH.search(desc) else "medium"
                sid = f"r{pi}.{len(ids) + 1}"
                signals.append(
                    Signal(
                        id=sid,
                        category=_slug(persona.persona_role),
                        kind=kind,
                        description=_untrusted.neutralize(desc),
                        priority_hint=prio,
                        excerpts=quotes,
                    )
                )
                ids.append(sid)
        qs = [q for q in persona.unanswered_questions if q][:3]
        questions += [q for q in qs if q not in questions]
        perspectives.append(
            ReviewPerspective(
                role=persona.persona_role, focus=persona.rubric_focus, signal_ids=ids, questions_for_authors=qs
            )
        )
    signals += _claim_signals(ev["claims"], 1)

    criteria = [
        (f"p{i}", f"{p.role}: {p.focus}", f"Signals: {', '.join(p.signal_ids) or 'none'}.")
        for i, p in enumerate(perspectives, 1)
    ]
    corpus = ev["corpus"]
    report = PeerReviewReport(
        tool="review_paper",
        subject=AnalysisSubject(
            paper_id=pid, title=paper.get("title") or "", domain=dom.value, source=corpus["source"]
        ),
        signals=signals,
        rubric=_contract.rubric(rubric_name, [*criteria, _FINAL_CRITERION]),
        extraction_confidence=_contract.extraction_confidence(corpus["source"], corpus.get("sections")),
        limitations=_contract.corpus_limitations(corpus),
        rubric_applied=rubric_name,
        perspectives=perspectives,
        missing_experiments_or_controls=[s.description for s in signals if s.kind == "gap"][:8],
        questions_for_authors=questions[:8],
    )
    extra = ["", "## Reviewer perspectives"]
    for p in perspectives:
        extra += [f"- **{p.role}** — {p.focus} (signals: {', '.join(p.signal_ids) or 'none'})"]
        extra += [f"  - Question: {q}" for q in p.questions_for_authors]
    report.rendered_markdown = _contract.render_markdown(report, "Peer-review evidence", extra)
    return report.model_dump(mode="json")


def review_paper_scientific(paper_id: str, domain: str | None = None, inspect_fulltext: bool = True) -> dict:
    """Alias for review_paper (#20 specification compatibility)."""
    return review_paper(paper_id=paper_id, domain=domain, inspect_fulltext=inspect_fulltext)
