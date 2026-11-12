"""Interactive scope refinement (#01): detect broad queries, ask 3 domain-specific multiple-choice questions
and turn the answers into an enriched query plus search filters. Pure stdlib, no network."""

import datetime
import re
import unicodedata

from noul.providers.domain_classifier import resolve_domain
from noul.schemas.domain import ScientificDomain as D

MIN_SIGNIFICANT_WORDS = 5
MIN_CRITERIA = 2

STOPWORDS = set(
    """
a an the of in on at to for from by with without about into over under between among and or not no nor vs versus
is are was were be been being do does did has have had can could should would will may might it its this that these
those what which who whom how why when where whether than then there their them they we our you your i my me
as if so such via per using use based effect effects impact role study studies paper papers research
o os as um uma uns umas de do da dos das em no na nos nas ao aos à às pelo pela pelos pelas por para com sem sobre
entre e ou nem não que qual quais quem como porque quando onde se seu sua seus suas é são foi foram ser estar há
efeito efeitos impacto papel estudo estudos artigo artigos pesquisa
""".split()
)

_BOOLEAN_RX = re.compile(r"\b(?:AND|OR|NOT)\b|\"[^\"]+\"|“[^”]+”")
_TEMPORAL_RX = re.compile(
    r"\b(?:19|20)\d{2}\s*(?:\.\.|–|-|to|a|até)\s*(?:19|20)\d{2}\b"
    r"|\b(?:since|after|before|from|until|desde|após|apos|antes\s+de|depois\s+de|até)\s+(?:19|20)\d{2}\b"
    r"|\b(?:last|past|recent|últimos|ultimos|últimas|ultimas)\s+\d{1,2}\s+(?:years?|anos?|months?|meses)\b"
    r"|\b(?:19|20)\d{2}\b",
    re.I,
)
_METHOD_TERMS = (
    "supplementation",
    "suplementacao",
    "intervention",
    "intervencao",
    "therapy",
    "terapia",
    "treatment",
    "tratamento",
    "training",
    "treinamento",
    "exercise",
    "exercicio",
    "diet",
    "dieta",
    "drug",
    "farmaco",
    "vaccine",
    "vacina",
    "rct",
    "randomized",
    "randomised",
    "trial",
    "ensaio",
    "cohort",
    "coorte",
    "meta-analysis",
    "quantization",
    "quantizacao",
    "qlora",
    "lora",
    "fine-tuning",
    "finetuning",
    "distillation",
    "pruning",
    "transformer",
    "attention",
    "diffusion",
    "reinforcement",
    "retrieval",
    "rag",
    "algorithm",
    "algoritmo",
    "difference-in-differences",
    "diff-in-diff",
    "instrumental",
    "regression discontinuity",
    "rdd",
    "var",
    "dsge",
    "policy",
    "politica",
    "tax",
    "imposto",
    "subsidy",
    "subsidio",
    "simulation",
    "simulacao",
)
_OUTCOME_TERMS = (
    "memory",
    "memoria",
    "cognition",
    "cognicao",
    "mortality",
    "mortalidade",
    "survival",
    "sobrevida",
    "efficacy",
    "eficacia",
    "safety",
    "seguranca",
    "incidence",
    "incidencia",
    "risk",
    "risco",
    "adults",
    "adultos",
    "children",
    "criancas",
    "elderly",
    "idosos",
    "patients",
    "pacientes",
    "women",
    "mulheres",
    "athletes",
    "atletas",
    "healthy",
    "saudaveis",
    "accuracy",
    "acuracia",
    "latency",
    "latencia",
    "throughput",
    "perplexity",
    "mmlu",
    "gsm8k",
    "humaneval",
    "imagenet",
    "glue",
    "squad",
    "coco",
    "benchmark",
    "dataset",
    "f1",
    "bleu",
    "vram",
    "energy",
    "gdp",
    "pib",
    "inflation",
    "inflacao",
    "employment",
    "emprego",
    "unemployment",
    "desemprego",
    "wages",
    "salarios",
    "productivity",
    "produtividade",
    "growth",
    "crescimento",
    "poverty",
    "pobreza",
    "yield",
    "efficiency",
    "eficiencia",
    "performance",
    "desempenho",
    "outcome",
    "outcomes",
    "desfecho",
)


def _fold(text):
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)).lower()


def _significant_words(query):
    words = re.findall(r"[\w][\w\-\.]*", query or "", flags=re.UNICODE)
    return [
        w
        for w in words
        if w.lower().strip(".-") not in STOPWORDS and len(w.strip(".-")) > 1 and w.upper() not in ("AND", "OR", "NOT")
    ]


def _has_term(folded, terms):
    return any(re.search(r"(?<![\w-])" + re.escape(t) + r"s?(?![\w-])", folded) for t in terms)


def needs_clarification(query):
    """Heuristic broad-vs-specific check. A query is specific when it meets >= 2 of the criteria."""
    folded = _fold(query)
    words = _significant_words(query)
    criteria = {
        "significant_words": len(words) >= MIN_SIGNIFICANT_WORDS,
        "boolean_or_phrase": bool(_BOOLEAN_RX.search(query or "")),
        "temporal_constraint": bool(_TEMPORAL_RX.search(query or "")),
        "method_and_outcome": _has_term(folded, _METHOD_TERMS) and _has_term(folded, _OUTCOME_TERMS),
    }
    met = [k for k, v in criteria.items() if v]
    broad = len(met) < MIN_CRITERIA
    return {
        "query": query,
        "needs_clarification": broad,
        "criteria": criteria,
        "criteria_met": len(met),
        "significant_word_count": len(words),
        "reason": (
            f"Broad query: only {len(met)} of 4 specificity criteria met ({', '.join(met) or 'none'})."
            if broad
            else f"Specific query: {len(met)} of 4 specificity criteria met ({', '.join(met)})."
        ),
    }


def _year(offset):
    return datetime.date.today().year - offset


# Per domain: [(question_id, topic, question, [(label, effects)])]. Effects: terms / year_min / study_types /
# sources / sort. Callables are evaluated lazily so that relative years follow the current date.
QUESTION_BANK = {
    D.COMPUTER_SCIENCE: [
        (
            "q1",
            "focus",
            "What is the technical focus / goal of the search?",
            [
                ("Architecture and theoretical method", {"terms": ["architecture", "method"]}),
                ("Benchmarks and empirical evaluation", {"terms": ["benchmark", "evaluation"]}),
                (
                    "Hardware efficiency / latency / memory (Edge/GPU)",
                    {"terms": ["efficiency", "latency", "memory"]},
                ),
                ("Survey / General review", {"terms": ["survey"], "study_types": ["review", "systematic-review"]}),
            ],
        ),
        (
            "q2",
            "resources",
            "What is the resource constraint / runtime environment?",
            [
                ("Consumer GPUs / Edge (<= 16GB VRAM)", {"terms": ["consumer GPU", "edge"]}),
                ("Workstation / Server (24-80GB VRAM)", {"terms": ["single GPU"]}),
                ("Datacenter scale / Training", {"terms": ["large-scale", "distributed training"]}),
                ("No hardware constraint", {}),
            ],
        ),
        (
            "q3",
            "time_window",
            "Which time window?",
            [
                ("Last 2 years (recent state of the art)", {"year_min": lambda: _year(2), "sort": "date"}),
                ("Last 5 years", {"year_min": lambda: _year(5)}),
                ("Seminal / most cited papers (any year)", {"sort": "citations"}),
            ],
        ),
    ],
    D.BIOMEDICAL: [
        (
            "q1",
            "population",
            "What is the target population?",
            [
                ("Healthy adults", {"terms": ["healthy adults"]}),
                ("Patients with a specific clinical condition", {"terms": ["patients"]}),
                ("Older adults / Pediatric population", {"terms": ["older adults", "children"]}),
                ("Any human population (exclude animal/in vitro)", {"terms": ["humans"]}),
            ],
        ),
        (
            "q2",
            "evidence",
            "Which type of evidence comes first?",
            [
                (
                    "Meta-analyses and systematic reviews only",
                    {"study_types": ["meta-analysis", "systematic-review"]},
                ),
                (
                    "Randomized controlled trials (RCTs)",
                    {"terms": ["randomized controlled trial"], "study_types": ["rct"]},
                ),
                ("Observational / cohort studies", {"terms": ["cohort"], "study_types": ["cohort"]}),
                ("All study designs", {}),
            ],
        ),
        (
            "q3",
            "time_window",
            "Which time window?",
            [
                ("Last 5 years", {"year_min": lambda: _year(5)}),
                ("Last 10 years", {"year_min": lambda: _year(10)}),
                ("Any period", {}),
            ],
        ),
    ],
    D.ECONOMICS: [
        (
            "q1",
            "method",
            "Which methodological approach?",
            [
                (
                    "Causal identification (Diff-in-Diff, IV, RDD)",
                    {"terms": ["difference-in-differences", "instrumental variables", "regression discontinuity"]},
                ),
                ("Time series / Macroeconometrics", {"terms": ["time series", "macroeconometric"]}),
                ("Economic theory / Structural models", {"terms": ["structural model", "theory"]}),
                (
                    "Literature review / Meta-analysis",
                    {"terms": ["literature review"], "study_types": ["meta-analysis", "systematic-review", "review"]},
                ),
            ],
        ),
        (
            "q2",
            "geography",
            "Which geographic / institutional context?",
            [
                ("Emerging economies / Latin America", {"terms": ["emerging economies", "Latin America"]}),
                ("USA / Europe / OECD", {"terms": ["United States", "Europe", "OECD"]}),
                ("Global comparison / Cross-country panel", {"terms": ["cross-country panel"]}),
            ],
        ),
        (
            "q3",
            "time_window",
            "Which time window?",
            [
                ("After 2020", {"year_min": lambda: 2020}),
                ("Last 10 years", {"year_min": lambda: _year(10)}),
                ("Classics and recent work", {"sort": "citations"}),
            ],
        ),
    ],
    D.GENERAL_STEM: [
        (
            "q1",
            "focus",
            "What is the focus of the study?",
            [
                ("Experimental / empirical results", {"terms": ["experimental"]}),
                ("Theoretical modeling / simulation", {"terms": ["theoretical model", "simulation"]}),
                ("Systematic review / Survey", {"terms": ["review"], "study_types": ["systematic-review", "review"]}),
            ],
        ),
        (
            "q2",
            "maturity",
            "How mature should the sources be?",
            [
                ("Peer-reviewed journals only", {"sources": ["openalex", "s2", "crossref"]}),
                ("Include recent preprints (arXiv)", {"sources": ["openalex", "s2", "crossref", "arxiv"]}),
            ],
        ),
        (
            "q3",
            "time_window",
            "Which time window?",
            [
                ("Last 3 years", {"year_min": lambda: _year(3)}),
                ("Last 10 years", {"year_min": lambda: _year(10)}),
                ("Any year", {}),
            ],
        ),
    ],
}

DEFAULT_SOURCES = {
    D.COMPUTER_SCIENCE: ["openalex", "s2", "arxiv"],
    D.BIOMEDICAL: ["pubmed", "europepmc", "openalex", "s2"],
    D.ECONOMICS: ["openalex", "s2", "crossref"],
    D.GENERAL_STEM: None,
}


def _domain_for(query, domain=None):
    """Explicit override (aliases accepted; ValueError if invalid) or the domain detected from the query."""
    if domain is not None and str(domain).strip():
        return resolve_domain(override_domain=domain)
    return resolve_domain(title=query)


def generate_clarification_questions(query, domain=None):
    """-> {"query", "domain", "questions": [{"id", "topic", "question", "options"}]} (3 questions)."""
    dom = _domain_for(query, domain)
    return {
        "query": query,
        "domain": dom.value,
        "questions": [
            {"id": qid, "topic": topic, "question": text, "options": [label for label, _ in opts]}
            for qid, topic, text, opts in QUESTION_BANK[dom]
        ],
    }


def _match_option(answer, options):
    """Resolve an answer given as option label, 1-based index or free text -> (label, effects) or (None, text)."""
    if isinstance(answer, bool):
        return None, None
    if isinstance(answer, int) or (isinstance(answer, str) and answer.strip().isdigit()):
        idx = int(answer) - 1
        if 0 <= idx < len(options):
            return options[idx]
    text = str(answer if answer is not None else "").strip()
    if not text:
        return None, None
    folded = _fold(text)
    for label, effects in options:
        if _fold(label) == folded:
            return label, effects
    rx = re.compile(r"(?<!\w)" + re.escape(folded) + r"s?(?!\w)")
    hits = [(label, effects) for label, effects in options if rx.search(_fold(label))]
    if len(hits) == 1:
        return hits[0]
    return None, (None if text.isdigit() else text)


def build_refined_query(query, answers=None, domain=None):
    """Combine the original query with the chosen options -> refined_query + recommended_filters."""
    dom = _domain_for(query, domain)
    answers = dict(answers or {})
    terms, study_types, applied, ignored = [], [], [], []
    filters = {
        "year_min": None,
        "study_types": None,
        "sources": DEFAULT_SOURCES[dom],
        "domain": dom.value,
        "sort": None,
    }
    for qid, topic, _text, options in QUESTION_BANK[dom]:
        raw = answers.get(qid, answers.get(topic))
        if raw is None:
            continue
        label, effects = _match_option(raw, options)
        if label is None:
            if effects:  # free-text answer: use it as an extra query term
                terms.append(effects)
                applied.append({"id": qid, "answer": effects, "free_text": True})
            else:
                ignored.append(qid)
            continue
        applied.append({"id": qid, "answer": label})
        terms.extend(effects.get("terms") or [])
        study_types.extend(effects.get("study_types") or [])
        if "year_min" in effects:
            filters["year_min"] = effects["year_min"]()
        if "sources" in effects:
            filters["sources"] = list(effects["sources"])
        if "sort" in effects:
            filters["sort"] = effects["sort"]
    if study_types:
        filters["study_types"] = list(dict.fromkeys(study_types))
    base = _fold(query)
    extra = [t for t in dict.fromkeys(terms) if _fold(t) not in base]
    out = {
        "original_query": query,
        "refined_query": " ".join([query.strip()] + extra).strip(),
        "recommended_filters": filters,
        "applied_answers": applied,
    }
    if ignored:
        out["ignored_answers"] = ignored
    return out
