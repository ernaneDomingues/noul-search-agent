"""Scientific-domain detection (#19): arXiv taxonomy > fields of study / topics > PubMed signals > text.

Pure stdlib and side-effect free, so it is safe to run on every parsed search result."""
import re

from noul.schemas.domain import ScientificDomain

CS, BIO, ECON, STEM = (ScientificDomain.COMPUTER_SCIENCE, ScientificDomain.BIOMEDICAL,
                       ScientificDomain.ECONOMICS, ScientificDomain.GENERAL_STEM)

# Exact arXiv categories checked before prefixes (math.OC is CS although math.* is general STEM).
_ARXIV_EXACT = {"stat.ml": CS, "math.oc": CS, "eess.as": CS, "eess.iv": CS, "eess.sp": CS}
_ARXIV_PREFIXES = (
    ("cs.", CS),
    ("q-bio.", BIO), ("med.", BIO), ("bio.", BIO),
    ("econ.", ECON), ("q-fin.", ECON),
    ("physics.", STEM), ("math.", STEM), ("cond-mat.", STEM), ("astro-ph.", STEM), ("quant-ph", STEM),
    ("hep-", STEM), ("nucl-", STEM), ("nlin.", STEM), ("gr-qc", STEM), ("math-ph", STEM),
)

# Substrings of Semantic Scholar fieldsOfStudy, OpenAlex fields/domains/concepts, Crossref subjects.
_FIELD_HINTS = (
    (CS, ("computer science", "artificial intelligence", "machine learning", "computer vision",
          "information systems", "software", "computational linguistics")),
    (ECON, ("economics", "econometric", "finance", "financial", "business", "accounting")),
    (BIO, ("medicine", "medical", "biology", "biochemistry", "genetics", "pharmacology", "pharmac", "psychology",
           "psychiatry", "neuroscience", "health", "nursing", "dentistry", "immunology", "microbiology",
           "oncology", "cardiology", "epidemiology", "veterinary", "physiology", "toxicology")),
)

KEYWORDS = {
    CS: (
        "transformer", "llm", "large language model", "neural network", "deep learning", "reinforcement learning",
        "attention mechanism", "pytorch", "benchmark", "token", "gpu", "vram", "quantization", "fine-tuning", "rag",
        "diffusion model", "convolutional", "algorithm", "compiler", "distributed systems",
        # Portuguese equivalents so clarification works on PT queries.
        "quantização", "rede neural", "redes neurais", "aprendizado profundo", "modelo de linguagem",
    ),
    BIO: (
        "clinical trial", "randomized", "placebo", "patient", "patients", "cohort", "mortality", "therapy",
        "treatment", "disease", "diagnosis", "syndrome", "dosage", "mg/day", "in vivo", "in vitro", "biomarker",
        "meta-analysis", "pathogenesis", "efficacy", "adverse events", "medical", "rct", "supplementation",
        "healthy adults", "creatine", "vaccine", "cancer", "clinical",
        "ensaio clínico", "pacientes", "tratamento", "doença", "suplementação", "creatina", "terapia",
    ),
    ECON: (
        "econometric", "difference-in-differences", "instrumental variable", "regression discontinuity",
        "panel data", "gdp", "inflation", "monetary policy", "fiscal", "market equilibrium", "macroeconomic",
        "microeconomic", "wage", "elasticity", "central bank", "portfolio", "asset pricing", "endogeneity",
        "inflação", "política monetária", "juros", "pib", "econometria", "salário mínimo",
    ),
}
DOMAIN_ORDER = (CS, BIO, ECON)


def _kw_pattern(kw):
    body = re.escape(kw).replace(r"\-", r"[-\s]?").replace(r"\ ", r"[-\s]+")
    return re.compile(r"(?<![\w])" + body + r"(?:s|es)?(?![\w])", re.I)


def _singulars(kws):
    # Patterns already accept plural suffixes, so "patients" would double-count "patient".
    return [kw for kw in kws if not (kw.endswith("s") and (kw[:-1] in kws or kw[:-2] in kws))]


_KW_RX = {dom: [(kw, _kw_pattern(kw)) for kw in _singulars(kws)] for dom, kws in KEYWORDS.items()}

_OVERRIDE_ALIASES = {
    CS: ("computer_science", "computer science", "computer-science", "cs", "comp_sci", "ai", "ml", "computing"),
    BIO: ("biomedical", "biomedicine", "biomed", "bio", "med", "medicine", "medical", "health", "life_sciences"),
    ECON: ("economics", "econ", "economy", "finance", "fin", "q-fin"),
    STEM: ("general_stem", "general stem", "general-stem", "stem", "general"),
}
_ALIAS_TO_DOMAIN = {alias: dom for dom, aliases in _OVERRIDE_ALIASES.items() for alias in aliases}
VALID_DOMAIN_NAMES = tuple(d.value for d in ScientificDomain)


def normalize_domain(value):
    """Map an override such as 'cs', 'bio', 'econ', 'stem' or an enum to `ScientificDomain`.
    Returns None for empty values; raises ValueError for unknown domains."""
    if value is None:
        return None
    if isinstance(value, ScientificDomain):
        return value
    key = str(value).strip().lower()
    if not key:
        return None
    dom = _ALIAS_TO_DOMAIN.get(key) or _ALIAS_TO_DOMAIN.get(key.replace("-", "_"))
    if dom is None:
        raise ValueError(f"Invalid domain '{value}'. Use one of: {', '.join(VALID_DOMAIN_NAMES)} "
                         "(aliases: cs, bio, med, econ, stem)")
    return dom


def map_arxiv_category(category):
    cat = str(category or "").strip().lower()
    if not cat:
        return None
    if cat in _ARXIV_EXACT:
        return _ARXIV_EXACT[cat]
    for prefix, dom in _ARXIV_PREFIXES:
        if cat.startswith(prefix):
            return dom
    return None


def map_field_of_study(name):
    low = str(name or "").strip().lower()
    if not low:
        return None
    for dom, hints in _FIELD_HINTS:
        if any(h in low for h in hints):
            return dom
    return None


def score_text(title="", abstract="", journal=""):
    """Return {domain: {"score", "title_hits", "matches"}} using domain vocabularies."""
    title = title or ""
    body = f"{title} {abstract or ''} {journal or ''}"
    out = {}
    for dom in DOMAIN_ORDER:
        matches = [kw for kw, rx in _KW_RX[dom] if rx.search(body)]
        title_hits = sum(1 for kw, rx in _KW_RX[dom] if rx.search(title))
        out[dom] = {"score": len(matches), "title_hits": title_hits, "matches": matches}
    return out


def _qualifies(s):
    return s["score"] >= 2 or s["title_hits"] >= 1


def _best_text_domain(scores):
    qualified = [d for d in DOMAIN_ORDER if _qualifies(scores[d])]
    if not qualified:
        return None
    return max(qualified, key=lambda d: (scores[d]["score"] + scores[d]["title_hits"], scores[d]["title_hits"],
                                         -DOMAIN_ORDER.index(d)))


def _as_dict(paper):
    if paper is None:
        return {}
    if isinstance(paper, dict):
        return paper
    if hasattr(paper, "model_dump"):
        return paper.model_dump(mode="json")
    return dict(getattr(paper, "__dict__", {}) or {})


def _as_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def explain_domain(paper=None, categories=None, title="", abstract="", journal="", override_domain=None):
    """Classify a paper and explain the decision: {"domain", "method", "signals", "text_scores"}."""
    forced = normalize_domain(override_domain)
    if forced is not None:
        return {"domain": forced, "method": "override", "signals": [str(override_domain)], "text_scores": {}}

    p = _as_dict(paper)
    title = title or p.get("title") or ""
    abstract = abstract or p.get("abstract") or ""
    journal = journal or p.get("journal") or p.get("venue") or ""

    primary = p.get("primary_category")
    cats = _as_list(categories) + _as_list(p.get("categories"))
    if primary and str(primary).lower() != "general":
        cats = [str(primary)] + cats
    votes = {}
    for i, cat in enumerate(cats):
        dom = map_arxiv_category(cat)
        if dom is not None:
            votes[dom] = votes.get(dom, 0) + (2 if i == 0 else 1)
    if votes:
        best = max(votes, key=lambda d: (votes[d], d != STEM))
        return {"domain": best, "method": "categories", "signals": cats, "text_scores": {}}

    fields = _as_list(p.get("fields_of_study")) + ([str(primary)] if primary and str(primary).lower() != "general" else [])
    field_votes = {}
    for name in fields:
        dom = map_field_of_study(name)
        if dom is not None:
            field_votes[dom] = field_votes.get(dom, 0) + 1
    if field_votes:
        best = max(field_votes, key=lambda d: (field_votes[d], -DOMAIN_ORDER.index(d)))
        return {"domain": best, "method": "fields_of_study", "signals": fields, "text_scores": {}}

    scores = score_text(title, abstract, journal)
    text_scores = {d.value: s for d, s in scores.items()}
    text_best = _best_text_domain(scores)

    sources = set(_as_list(p.get("sources")))
    pubmed_signal = bool(p.get("pmid") or p.get("pmc_id") or p.get("mesh_terms")
                         or (sources and sources <= {"pubmed", "europepmc"}))
    if pubmed_signal:
        if text_best in (CS, ECON) and scores[text_best]["score"] > scores[BIO]["score"]:
            return {"domain": text_best, "method": "text", "signals": scores[text_best]["matches"],
                    "text_scores": text_scores}
        return {"domain": BIO, "method": "pubmed", "signals": ["pmid" if p.get("pmid") else "pubmed-index"],
                "text_scores": text_scores}

    if text_best is not None:
        return {"domain": text_best, "method": "text", "signals": scores[text_best]["matches"],
                "text_scores": text_scores}
    return {"domain": STEM, "method": "default", "signals": [], "text_scores": text_scores}


def resolve_domain(paper=None, categories=None, title="", abstract="", journal="", override_domain=None):
    """Return the `ScientificDomain` of a paper dict / `UnifiedPaper` or of raw metadata.
    `override_domain` (e.g. 'cs', 'bio', 'econ', 'stem') wins; invalid values raise ValueError."""
    return explain_domain(paper, categories, title, abstract, journal, override_domain)["domain"]
