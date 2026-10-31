"""Study-design classification (evidence level) and its weights."""

import re

EV_W = {
    "meta-analysis": 1.0,
    "systematic-review": 0.9,
    "rct": 0.8,
    "review": 0.55,
    "cohort": 0.5,
    "empirical": 0.45,
    "preclinical": 0.2,
    "other": 0.3,
}


NEG_PATTERNS = re.compile(
    r"\b(?:"
    r"(?:non|not|un)[-\s]?randomi[sz]ed(?:\s+(?:controlled\s+)?(?:clinical\s+)?(?:trials?|stud(?:y|ies)))?|"
    r"(?:future|further|more|larger|additional)\s+(?:well[-\s]designed\s+|large[-\s]scale\s+|high[-\s]quality\s+)?"
    r"(?:meta[-\s]?analys\w+|systematic\s+reviews?|rcts?|randomi[sz]ed\s+(?:controlled\s+)?(?:clinical\s+)?trials?)|"
    r"need\s+for\s+(?:further\s+|more\s+)?(?:meta[-\s]?analys\w+|systematic\s+reviews?|rcts?|"
    r"randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"no\s+(?:meta[-\s]?analys\w+|rcts?|randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"lack\s+of\s+(?:meta[-\s]?analys\w+|rcts?|randomi[sz]ed\s+(?:controlled\s+)?trials?)|"
    r"protocol\s+for\s+a\s+(?:systematic\s+review|meta[-\s]?analysis|randomi[sz]ed(?:\s+controlled)?\s+trial)"
    r")\b",
    re.I,
)


_META_RE = re.compile(r"\bmeta[-\s]?analys(is|es)\b")


_SR_RE = re.compile(r"\bsystematic\s+review\b|\bprisma\b|\bcochrane\s+review\b|\bscoping\s+review\b")


_RCT_RE = re.compile(
    r"\brandomi[sz]ed\s+controlled\s+trial\b|\brandomi[sz]ed\s+trial\b|\brct\b|"
    r"\bdouble[-\s]?blind\b|\bplacebo[-\s]?controlled\b|\bclinical\s+trial\b"
)


_PRECLINICAL_RE = re.compile(
    r"\bin\s+vitro\b|\bin\s+vivo\b|\bmice\b|\bmurine\b|\brat\s+model\b|\banimal\s+model\b|"
    r"\bcell\s+line\b|\bwistar\b|\bc57bl\b|\bzebrafish\b"
)


_OBSERVATIONAL_RE = re.compile(
    r"\bcohort\b|\blongitudinal\b|\bprospective\s+study\b|\bretrospective\s+study\b|"
    r"\bcase[-\s]?control\b|\bcross[-\s]?sectional\b|\bepidemiolog|\bpanel\s+data\b|"
    r"\bdifference[-\s]?in[-\s]?differences\b|\bquasi[-\s]?experiment|\b(?:non|not|un)[-\s]?randomi[sz]ed\b"
)


_REVIEW_RE = re.compile(r"\breview\b|\bsurvey\s+of\b|\boverview\s+of\b|\bstate[-\s]of[-\s]the[-\s]art\b")


_EMPIRICAL_RE = re.compile(
    r"\bbenchmark\b|\bablation\b|\bempirical\b|\bexperiment(al|s)?\b|\bdataset\b|"
    r"\bevaluation\s+of\b|\bcomparative\s+study\b"
)


_STRONG_DESIGNS = (("meta-analysis", _META_RE), ("systematic-review", _SR_RE), ("rct", _RCT_RE))


def classify_evidence(title="", abstract="", pub_types=None):
    """Classify study design across biomedical and general scientific disciplines.
    Order: structured publication types > title > title+abstract; negated mentions are ignored."""
    pts_norm = [re.sub(r"[^a-z0-9]", "", str(x).lower()) for x in (pub_types or []) if x]
    if any(pt == "metaanalysis" for pt in pts_norm):
        return "meta-analysis"
    if any(pt == "systematicreview" for pt in pts_norm):
        return "systematic-review"
    if any(pt.startswith(("randomizedcontrolledtrial", "randomisedcontrolledtrial")) for pt in pts_norm):
        return "rct"

    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()
    pts_text = " ".join(str(x).lower() for x in (pub_types or []) if x)
    title_clean = NEG_PATTERNS.sub(" ", title_l)

    for label, rx in _STRONG_DESIGNS:
        if rx.search(title_clean):
            return label
    # A clearly observational/preclinical title wins over secondary mentions in the abstract.
    if _PRECLINICAL_RE.search(title_l):
        return "preclinical"
    if _OBSERVATIONAL_RE.search(title_l):
        return "cohort"

    raw = f"{title_l} {abstract_l} {pts_text}"
    clean = NEG_PATTERNS.sub(" ", raw)
    for label, rx in _STRONG_DESIGNS:
        if rx.search(clean):
            return label
    if _PRECLINICAL_RE.search(raw):
        return "preclinical"
    if _OBSERVATIONAL_RE.search(raw):
        return "cohort"
    if _REVIEW_RE.search(clean):
        return "review"
    if _EMPIRICAL_RE.search(raw):
        return "empirical"
    return "other"
