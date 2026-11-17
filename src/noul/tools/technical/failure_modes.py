"""Failure modes: limitations, critical assumptions and baselines that beat the proposed method (`detect_failure_modes`)."""

import re

from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.providers import pdf_parser
from noul.search import engine as _engine
from noul.tools.technical import ablation as _ablation
from noul.tools.technical import common as _common
from noul.tools.technical import grounding as _grounding
from noul.tools.technical import tables as _tables

_PRIORITY_SECTIONS = re.compile(
    r"limitation|error\s+analysis|failure|discussion|threat|validity|broader\s+impact|appendix|weakness|caveat", re.I
)


_ABBR_DOT = re.compile(r"\b(?:i\.i\.d|e\.g|i\.e|et\s+al|vs|cf|fig|tab|eq|sec|dr|mr|prof|no|vol|pp|approx)\.", re.I)


_FAILURE_CATEGORIES = (
    (
        "long_context",
        re.compile(
            r"\b(?:long[-\s]context|sequence\s+length|context\s+window|context\s+length|lost\s+in\s+the\s+middle|"
            r"quadratic\s+complexity|memory\s+overflow|out[-\s]of[-\s]memory|\bOOM\b|long\s+sequences|long\s+inputs)\b|"
            r"\bO\(\s*[nNlL]\s*(?:\^\s*2|²)\s*\)|>\s*(?:8|16|32|64|128)\s*k\b",
            re.I,
        ),
    ),
    (
        "noise_sensitivity",
        re.compile(
            r"\b(?:noisy|noise|perturbations?|adversarial|corrupted|missing\s+data|prompt\s+sensitivity|"
            r"sensitive\s+to\s+prompts?|brittle|outliers?|malformed\s+prompts?|typos?)\b",
            re.I,
        ),
    ),
    (
        "out_of_distribution",
        re.compile(
            r"\b(?:out[-\s]of[-\s]distribution|\bOOD\b|domain\s+shift|distribution\s+shift|covariate\s+shift|"
            r"generaliz(?:e|es|ing|ation)\s+to|unseen\s+domains?|cross[-\s]lingual|external\s+validity|"
            r"non[-\s]english|low[-\s]resource\s+languages?)\b",
            re.I,
        ),
    ),
    (
        "latency_or_compute",
        re.compile(
            r"\b(?:latency|slow(?:er)?|computational\s+cost|compute\s+cost|overhead|inference\s+time|"
            r"inference\s+cost|memory\s+footprint|bottleneck|multi[-\s]step|multiple\s+inference\s+calls|"
            r"low\s+throughput|high\s+vram|energy\s+consumption)\b",
            re.I,
        ),
    ),
    (
        "sample_or_population_bias",
        re.compile(
            r"\b(?:small\s+sample|single[-\s]center|retrospective|selection\s+bias|attrition|"
            r"short\s+follow[-\s]up|in\s+vitro|animal\s+models?|mice|murine|pre[-\s]?clinical|"
            r"convenience\s+sample|small\s+cohort)\b",
            re.I,
        ),
    ),
)


_GENERAL_LIMITATION = re.compile(
    r"\b(?:limitations?|drawbacks?|weakness(?:es)?|fails?\s+(?:when|to|on|under|if)|"
    r"struggles?\s+(?:with|to|on|when)|does\s+not\s+support|cannot\s+handle|unable\s+to|"
    r"degrades?\s+(?:when|on|under|rapidly|significantly)|shortcomings?|caveats?)\b",
    re.I,
)


_SEVERITY_HIGH = re.compile(
    r"\b(?:fails?|collapses?|severe(?:ly)?|prohibitive(?:ly)?|critical(?:ly)?|cannot|can't|"
    r"only\s+in\s+vitro|catastrophic(?:ally)?|drastic(?:ally)?|unusable)\b",
    re.I,
)


_SEVERITY_LOW = re.compile(r"\b(?:minor|slight(?:ly)?|marginal(?:ly)?|negligible|modest(?:ly)?)\b", re.I)


_ASSUMPTION_RE = re.compile(
    r"\b(?:assum(?:e|es|ed|ing|ption|ptions)|requires?\s+that|under\s+the\s+(?:condition|assumption)|"
    r"provided\s+that|i\.?i\.?d\.?|independent\s+and\s+identically\s+distributed|stationar(?:y|ity)|"
    r"parallel\s+trends?|exogen(?:eity|ous)|no\s+unmeasured\s+confound(?:ing|ers?)|"
    r"oracle(?:\s+[a-z]+)?|clean\s+labels?)\b",
    re.I,
)


_BASELINE_SUPERIORITY_RE = re.compile(
    r"\b(?:outperformed\s+by|baseline\s+outperforms?|outperforms?\s+(?:our|the\s+proposed)|"
    r"underperforms?|(?:our|proposed)\s+(?:\w+\s+){0,2}(?:is|are|was|were|performs?|performed)\s+worse\s+than|classical\s+(?:methods?|models?|baselines?)|simple\s+baselines?|"
    r"linear\s+models?|BM25|logistic\s+regression|in\s+low[-\s]data|on\s+small\s+datasets?|"
    r"when\s+N\s+is\s+small)\b",
    re.I,
)


_OURS_ROW_RE = re.compile(
    r"\b(?:ours|our\s+(?:model|method|approach|system|framework)|proposed(?:\s+method|\s+model)?|full\s+model)\b", re.I
)


def _prose_sentences(text, sections, table_spans=()):
    """Yield `(is_priority, section_title, sentence_text)` preserving document order inside each priority tier."""
    # Same-length transforms only, so match offsets stay valid for section/page lookups: wrapped lines are joined
    # ("flowed") and abbreviation dots become a one-character placeholder.
    flowed = pdf_parser.flow_text(text or "", sections, spans=table_spans)
    protected = _ABBR_DOT.sub(lambda m: m.group(0).replace(".", "\u2024"), flowed)
    items = []
    for m in _grounding._SENTENCE.finditer(protected):
        if any(s <= m.start() < e for s, e in table_spans):
            continue
        raw = m.group(0).replace("\u2024", ".")
        quote = re.sub(r"\s+", " ", raw).strip()
        if len(quote) < 12 or not re.search(r"[A-Za-z]{3,}", quote):
            continue
        if re.match(r"^(?:#{1,4}\s+|(?:Table|Tabela|Fig(?:ure|\.)|Section)\s+\d+\s*[:.]?$)", quote, re.I):
            continue
        sec = pdf_parser.section_at(sections, m.start()) or ""
        is_prio = bool(_PRIORITY_SECTIONS.search(sec))
        items.append((0 if is_prio else 1, m.start(), sec, quote))
    items.sort(key=lambda x: (x[0], x[1]))
    return items


_FAILURE_LABELS = {
    "long_context": "Possible degradation with long inputs or context",
    "noise_sensitivity": "Possible sensitivity to noise or perturbations",
    "out_of_distribution": "Possible out-of-distribution / generalisation limit",
    "latency_or_compute": "Possible latency or compute-cost limitation",
    "sample_or_population_bias": "Possible sample or population bias",
    "general_limitation": "Limitation stated by the authors",
}


_FAILURE_RUBRIC = (
    (
        "about_method",
        "Does each excerpt describe a limitation of the proposed method (not of a baseline or prior work)?",
    ),
    ("conditions", "Under which conditions (input size, data shift, hardware, population) does it apply?"),
    ("mitigated", "Is it mitigated or quantified elsewhere in the paper?"),
    ("impact", "How much does it limit the paper's main claims or your use case?"),
)


def _severity_for(sentence):
    if _SEVERITY_HIGH.search(sentence):
        return "high"
    if _SEVERITY_LOW.search(sentence):
        return "low"
    return "medium"


def _extract_operational_limits(text):
    limits = {}
    ctx_patterns = (
        re.compile(
            r"(?:context\s+(?:window|length)|sequence\s+length|max(?:imum)?\s+(?:context|sequence|input)"
            r"(?:\s+(?:length|window|tokens?))?|beyond|exceeds?|>|>=)\s*(?:of\s*|[:=]\s*)?"
            r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>[kKmM])?\s*(?:tokens?\b|context\b)?",
            re.I,
        ),
        re.compile(r"\b(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>[kK])\s*tokens?\b", re.I),
        re.compile(r">\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[kK])\b", re.I),
    )
    for rx in ctx_patterns:
        for m in rx.finditer(text or ""):
            raw_n = float(m.group("num").replace(",", ""))
            unit = (m.group("unit") or "").lower()
            val = int(raw_n * (1000 if unit == "k" else 1_000_000 if unit == "m" else 1))
            if 128 <= val <= 10_000_000:
                limits["max_context_tokens"] = val
                break
        if "max_context_tokens" in limits:
            break

    m_samp = re.search(
        r"\b(?:min(?:imum)?\s+sample(?:\s+size)?(?:\s+of)?|sample\s+size(?:\s+of)?|\b[nN]\s*=\s*|cohort\s+of\s+)"
        r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:patients?|participants?|subjects?|samples?|examples?|instances?|individuals?)?\b",
        text or "",
        re.I,
    )
    if m_samp:
        n_val = int(m_samp.group(1).replace(",", ""))
        if 1 <= n_val <= 10_000_000:
            limits["min_sample_size"] = n_val

    m_lat = re.search(
        r"(?:latency(?:\s+of|\s*[:=])?\s*(?P<v1>\d+(?:\.\d+)?)\s*(?P<u1>ms|milliseconds?|s|seconds?)\b|"
        r"\b(?P<v2>\d+(?:\.\d+)?)\s*(?P<u2>ms|milliseconds?)\s+(?:latency|per\s+(?:token|query|step|request)))",
        text or "",
        re.I,
    )
    if m_lat:
        v = float(m_lat.group("v1") or m_lat.group("v2"))
        u = (m_lat.group("u1") or m_lat.group("u2") or "ms").lower()
        ms = v * 1000.0 if u.startswith("s") else v
        limits["max_latency_ms"] = int(ms) if ms.is_integer() else round(ms, 2)

    langs = []
    for lang in ("English", "Chinese", "Spanish", "Portuguese", "French", "German", "Japanese", "Arabic"):
        if re.search(rf"\b{lang}(?:[-\s]only)?\b", text or "", re.I) and lang not in langs:
            langs.append(lang)
    if langs:
        limits["supported_languages"] = langs
    return limits


def _table_baseline_superiority(paper_id, corpus, inspect_fulltext=True):
    """Inspect benchmark tables for metrics where a non-ablation baseline outperforms 'Ours'/proposed row."""
    scenarios = []
    tables = _tables.parse_benchmark_tables(corpus["text"], corpus["pages"], corpus["captions"])
    if inspect_fulltext:
        try:
            ext = _tables.extract_benchmark_tables(paper_id)
            if ext and ext.get("tables"):
                tables = ext["tables"]
        except Exception:
            pass
    title_words = {
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", corpus.get("title") or "")
        if w.lower() not in _grounding._STOP
    }
    for tb in tables:
        if tb.get("table_id") == "inline_metrics" or tb.get("format") == "inline":
            continue
        metrics_map = tb.get("normalized_metrics") or {}
        if len(metrics_map) < 2 or _ablation._is_ablation(tb):
            continue  # ablation / "model variations" tables compare the method with itself, not with baselines
        labels = list(metrics_map.keys())

        def own(lbl):
            """A row of the proposed model's own family ("Model (base)", "Model (big)"), not a baseline."""
            return bool(title_words) and any(w in lbl.lower() for w in title_words)

        ours_label = next(
            (lbl for lbl in labels if _OURS_ROW_RE.search(lbl) and not _ablation._ABL_VARIANT.search(lbl)), None
        )
        if ours_label is None and title_words:
            family = [
                lbl
                for lbl in labels
                if own(lbl) and not _ablation._ABL_VARIANT.search(lbl) and not _ablation._ABL_BASE.search(lbl)
            ]
            ours_label = family[-1] if family else None  # the last (usually largest/final) configuration
        if not ours_label:
            continue
        ours_vals = metrics_map.get(ours_label) or {}
        tb_label = tb.get("label") or tb.get("table_id") or "Table"
        for other_label, other_vals in metrics_map.items():
            if other_label == ours_label or _ablation._ABL_VARIANT.search(other_label) or own(other_label):
                continue
            for metric, ours_v in ours_vals.items():
                other_v = other_vals.get(metric)
                if not (_tables._is_number(ours_v) and _tables._is_number(other_v)):
                    continue
                lower_better = metric in _tables.LOWER_IS_BETTER
                if (not lower_better and other_v > ours_v) or (lower_better and other_v < ours_v):
                    msg = (
                        f"{tb_label} ({metric}): baseline '{other_label}' ({other_v}) outperforms "
                        f"'{ours_label}' ({ours_v})."
                    )
                    if msg not in scenarios:
                        scenarios.append(msg)
    return scenarios


def detect_failure_modes(paper_id: str, inspect_fulltext: bool = True) -> dict:
    """Detect categorized failure modes, critical assumptions, baseline superiority scenarios and operational limits."""
    from noul.schemas.evidence import AnalysisSubject, Signal
    from noul.schemas.technical import FailureModesReport

    pid = _common._paper_id(paper_id)
    if inspect_fulltext:
        corpus = pdf_parser.get_paper_corpus(pid)
        p = corpus.get("paper") or _engine.resolve_paper(pid)
        full_t = corpus.get("text") or ""
        abs_t = p.get("abstract") or ""
        if abs_t and abs_t not in full_t:
            combined = f"{p.get('title') or ''}\n\n{abs_t}\n\n{full_t}".strip()
            corpus = pdf_parser.build_corpus(combined, paper=p, source=corpus.get("source") or "fulltext")
    else:
        p = _engine.resolve_paper(pid)
        text = "\n\n".join(x for x in (p.get("title") or "", p.get("abstract") or "") if x)
        corpus = pdf_parser.build_corpus(text, paper=p, source="abstract")

    domain = p.get("domain")
    if not domain:
        try:
            from noul.providers import domain_classifier

            domain = domain_classifier.resolve_domain(paper=p).value
        except Exception:
            domain = "general_stem"

    text = corpus["text"]
    pages = corpus["pages"]
    out_id = corpus["paper_id"] or p.get("id") or pid
    raw_tables = _tables.parse_benchmark_tables(text, pages, corpus["captions"])
    table_spans = [tb["_span"] for tb in raw_tables if tb.get("_span") and tb.get("format") != "inline"]
    # Captions describe tables; they are not statements about the method's failures.
    table_spans += [(c["offset"], c["end"]) for c in corpus["captions"]]
    sentences = _prose_sentences(text, corpus["sections"], table_spans=table_spans)

    def quote(sent, start, sec):
        return _untrusted.excerpt(sent, paper_id=out_id, section=sec, page=pdf_parser.page_at(pages, start))

    signals: list[Signal] = []
    seen_cat_quote = set()
    assumptions: list = []
    baseline_scenarios: list = []
    seen_assumptions, seen_baselines = set(), set()

    def add(category, sent, start, sec):
        key = (category, sent)
        if key in seen_cat_quote:
            return
        seen_cat_quote.add(key)
        signals.append(
            Signal(
                id=f"fm{len(signals) + 1}",
                category=category,
                kind="concern",
                description=_FAILURE_LABELS.get(category, "Limitation stated in the text"),
                priority_hint=_severity_for(sent),
                excerpts=[quote(sent, start, sec)],
            )
        )

    for _, start, sec, sent in sentences:
        matched_specific = False
        for category, rx in _FAILURE_CATEGORIES:
            if rx.search(sent):
                matched_specific = True
                add(category, sent, start, sec)
        if not matched_specific and _GENERAL_LIMITATION.search(sent):
            add("general_limitation", sent, start, sec)
        if _ASSUMPTION_RE.search(sent) and sent not in seen_assumptions:
            seen_assumptions.add(sent)
            assumptions.append(quote(sent, start, sec))
        if _BASELINE_SUPERIORITY_RE.search(sent) and sent not in seen_baselines:
            seen_baselines.add(sent)
            baseline_scenarios.append(quote(sent, start, sec))

    for tbl_scenario in _table_baseline_superiority(pid, corpus, inspect_fulltext=inspect_fulltext):
        if tbl_scenario not in seen_baselines:
            seen_baselines.add(tbl_scenario)
            baseline_scenarios.append(_untrusted.excerpt(tbl_scenario, paper_id=out_id, section="benchmark table"))

    report = FailureModesReport(
        tool="detect_failure_modes",
        subject=AnalysisSubject(
            paper_id=out_id,
            title=corpus["title"] or p.get("title") or "",
            domain=domain or "general_stem",
            source=corpus["source"],
        ),
        signals=signals,
        rubric=_contract.rubric("Failure-mode triage", _FAILURE_RUBRIC),
        extraction_confidence=_contract.extraction_confidence(corpus["source"], corpus["sections"]),
        limitations=_contract.corpus_limitations(corpus),
        untrusted_fields=["subject.title"],
        critical_assumptions=assumptions,
        baseline_superiority_scenarios=baseline_scenarios,
        operational_limits=_extract_operational_limits(text),
    )
    extra = []
    if assumptions:
        extra += ["", "## Assumptions stated in the text"] + [_untrusted.render(x) for x in assumptions]
    if baseline_scenarios:
        extra += ["", "## Where a baseline may do better"] + [_untrusted.render(x) for x in baseline_scenarios]
    if report.operational_limits:
        extra += ["", "## Operational limits found"] + [
            f"- `{k}`: {_untrusted.neutralize(v)}" for k, v in report.operational_limits.items()
        ]
    report.rendered_markdown = _contract.render_markdown(report, "Candidate failure modes", extra)
    return report.model_dump(mode="json")
