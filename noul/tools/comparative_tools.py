"""Phase 2 analysis tools (#04 comparison matrix, #05 thematic insights, #06 Obsidian export).

Deterministic, stdlib-only extraction over title/abstract (and optionally cached open-access full text).
The host agent's LLM remains responsible for nuanced interpretation; these tools structure the evidence."""

import re
from collections import Counter

from noul.schemas.analysis import ComparativeMatrixResult, ComparisonDimension

MAX_COMPARE = 10
NOT_STATED = "Not explicitly stated in abstract"
NO_LIMITATIONS = "No limitations explicitly declared in abstract"
FULLTEXT_CHARS = 60000
HIGH_EVIDENCE = ("meta-analysis", "systematic-review", "rct")


def _core():
    import noul_core

    return noul_core


# --------------------------------------------------------------------------- text helpers

_SECTION_LABEL = re.compile(
    r"\s*\b(background|objectives?|aims?|purpose|introduction|methods?|methodology|design|setting|participants|"
    r"results?|findings|conclusions?|interpretation|limitations?)\s*:\s*",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])")


def _sentences(text):
    """Split an abstract into sentences; labels of structured abstracts ("Methods: ...") start a new sentence."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    text = _SECTION_LABEL.sub(lambda m: f"\n{m.group(1).capitalize()}: ", text)
    out = []
    for block in text.split("\n"):
        out += [s.strip() for s in _SENT_SPLIT.split(block) if len(s.strip()) > 3]
    return out


def _clip(text, n=280):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _first_match(sentences, pattern, skip=()):
    for s in sentences:
        if s not in skip and pattern.search(s):
            return s
    return None


def _all_matches(sentences, pattern, limit=3, skip=()):
    out = []
    for s in sentences:
        if s not in skip and s not in out and pattern.search(s):
            out.append(s)
            if len(out) >= limit:
                break
    return out


_HYPOTHESIS = re.compile(
    r"\bwe (?:investigate|propose|introduce|present|examine|hypothesi[sz]e|study|explore)\b|"
    r"\bthis (?:study|paper|work|article) (?:examines|investigates|proposes|presents|evaluates|explores)\b|"
    r"^(?:objectives?|aims?|purpose|background)\s*:|\bto evaluate\b|\bwe aim\b",
    re.I,
)
_METHOD = re.compile(
    r"^(?:methods?|methodology|design)\s*:|\bmethodology\b|\busing an?\b|\bwe train(?:ed)?\b|\brandomi[sz]ed\b|"
    r"\bdouble[-\s]blind\b|\bcohort\b|\bdifference[-\s]in[-\s]differences\b|\bpanel data\b|\barchitecture\b|"
    r"\bmeta[-\s]analys[ie]s\b|\bsystematic(?:ally)? review",
    re.I,
)
_RESULT = re.compile(
    r"^(?:results?|findings|conclusions?|interpretation)\s*:|\bachiev(?:e|es|ed|ing)\b|\boutperform(?:s|ed|ing)?\b|"
    r"\bimprov(?:e|es|ed|ing)\b|\breduc(?:e|es|ed|ing)\b|\bdemonstrat(?:e|es|ed)\b|\bshow(?:s|ed)? that\b|"
    r"\bassociated with\b|\bsignificant(?:ly)?\b",
    re.I,
)
_LIMITATION = re.compile(
    r"\blimitations?\b|\bhowever\b|\bfuture (?:work|research|studies)\b|\bsmall sample\b|\bsingle[-\s]cent(?:er|re)\b|"
    r"\bobservational\b|\bpreclinical\b|\bin vitro\b|\bcomputational cost\b|\blatency\b|\black of\b",
    re.I,
)

_BENCHMARKS: list = [
    ("MMLU", r"\bMMLU\b"),
    ("GSM8K", r"\bGSM-?8K\b"),
    ("HumanEval", r"\bHumanEval\b"),
    ("MBPP", r"\bMBPP\b"),
    ("MATH", r"\bMATH\b"),
    ("ImageNet", r"\bImageNet\b"),
    ("CIFAR-100", r"\bCIFAR-?100\b"),
    ("CIFAR-10", r"\bCIFAR-?10\b(?!0)"),
    ("SuperGLUE", r"\bSuperGLUE\b"),
    ("GLUE", r"(?<!Super)\bGLUE\b"),
    ("SQuAD", r"\bSQuAD\b"),
    ("WMT", r"\bWMT(?:\s?'?\d{2,4})?\b"),
    ("MS MARCO", r"\bMS[\s-]?MARCO\b"),
    ("BEIR", r"\bBEIR\b"),
    ("SWE-bench", r"\bSWE-?bench\b"),
    ("MIMIC-III", r"\bMIMIC-?III\b"),
    ("MIMIC-IV", r"\bMIMIC-?IV\b"),
    ("UK Biobank", r"\bUK\s?Biobank\b"),
    ("NHANES", r"\bNHANES\b"),
    ("Penn World Table", r"\bPenn World Tables?\b"),
]
_BENCHMARKS = [(name, re.compile(rx)) for name, rx in _BENCHMARKS]
_POP_WORDS = r"(?:patients|participants|adults|subjects|individuals|children|women|men|firms|countries|households)"
_POP_COUNT = re.compile(r"\b(\d[\d,.]*(?:\s(?:million|thousand))?)\s+(?:[a-z-]+\s){0,2}?" + _POP_WORDS + r"\b", re.I)
_POP_N = re.compile(r"\b[nN]\s*=\s*\d[\d,.]*")
_POP_WORD = re.compile(r"\b" + _POP_WORDS + r"\b", re.I)

_PERCENT = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d+)?\s?%(?!\s*(?:CI|confidence))")
_PVALUE = re.compile(r"\b[pP]\s*(?:<|>|=|≤|≥)\s*0?\.\d+")
_CI = re.compile(r"\b95\s?%\s?CI[:,]?\s*[\[(]?\s*-?\d+(?:\.\d+)?\s*(?:-|–|to|,)\s*-?\d+(?:\.\d+)?\s*[\])]?", re.I)
_NAMED_METRICS: list = [
    ("AUC", r"\bAU(?:RO)?C\b"),
    ("F1", r"\bF1(?:[-\s]score)?\b"),
    ("BLEU", r"\bBLEU\b"),
    ("ROUGE", r"\bROUGE(?:-[L12])?\b"),
    ("HR", r"\b(?:a?HR|hazard ratio)\b"),
    ("OR", r"\b(?:a?OR|odds ratio)\b"),
    ("RR", r"\b(?:RR|relative risk|risk ratio)\b"),
    ("SMD", r"\bSMD\b"),
    ("accuracy", r"\baccuracy\b"),
    ("VRAM", r"\bVRAM\b"),
    ("latency", r"\blatency\b"),
]
_NUM = r"(?:of|was|is|=|:|to|by|reaching|achieving|\()?\s*(-?\d+(?:\.\d+)?\s?(?:%|x|×|ms|s\b|GB|MB)?)"
_NAMED_METRICS = [
    (name, re.compile(rx + r"[^.;\d]{0,20}?" + _NUM, re.I if name in ("accuracy", "latency") else 0))
    for name, rx in _NAMED_METRICS
]
_SPEEDUP = re.compile(
    r"(\d+(?:\.\d+)?\s?[x×])\s*(?:faster|speed-?ups?)|speed-?ups? of (?:up to )?(\d+(?:\.\d+)?\s?[x×]?)", re.I
)


def _benchmarks(text):
    found = [name for name, rx in _BENCHMARKS if rx.search(text)]
    pops = []
    for m in _POP_N.finditer(text):
        pops.append(re.sub(r"\s*=\s*", " = ", m.group(0)).rstrip(".,"))
    for m in _POP_COUNT.finditer(text):
        pops.append(m.group(0).rstrip(".,"))
    if not pops:
        words = sorted({w.lower() for w in _POP_WORD.findall(text)})
        pops = [f"population: {w}" for w in words[:2]]
    out = list(dict.fromkeys(found + pops))
    return out[:8] or [NOT_STATED]


def _metrics(text, sentences, skip=()):
    metrics = {}
    for name, rx in _NAMED_METRICS:
        m = rx.search(text)
        if m:
            metrics[name] = m.group(1).strip()
    m = _SPEEDUP.search(text)
    if m:
        metrics["speedup"] = (m.group(1) or m.group(2)).strip()
    for key, rx in (("percentages", _PERCENT), ("p_values", _PVALUE), ("confidence_intervals", _CI)):
        vals = list(dict.fromkeys(v.group(0).strip() for v in rx.finditer(text)))
        if vals:
            metrics[key] = vals[:6]
    headline = _first_match(sentences, _RESULT, skip=skip) or (sentences[-1] if len(sentences) > 1 else None)
    metrics["headline_finding"] = _clip(headline, 400) if headline else "Not reported in abstract"
    return metrics


def _domain(p):
    if p.get("domain"):
        return str(p["domain"])
    try:
        from noul.providers import domain_classifier

        return domain_classifier.resolve_domain(paper=p).value
    except Exception:
        return "general_stem"


def _artifacts(p):
    """Artifact flags of a paper (offline radar scan on a copy when the record was never annotated)."""
    if "has_code" not in p:
        try:
            from noul.providers import artifact_radar

            p = dict(p)
            artifact_radar.scan_artifacts_from_paper(p, verify_http=False)
        except Exception:
            pass
    art = p.get("artifacts") or {}
    return {
        "has_code": bool(p.get("has_code")),
        "code_url": p.get("code_url") or art.get("code_url"),
        "code_verified": bool(art.get("code_verified")),
        "has_weights": bool(p.get("has_weights")),
        "weights_url": p.get("weights_url") or art.get("weights_url"),
        "weights_verified": bool(art.get("weights_verified")),
        "has_dataset": bool(p.get("has_dataset")),
        "dataset_url": p.get("dataset_url") or art.get("dataset_url"),
        "dataset_verified": bool(art.get("dataset_verified")),
        "evasive": bool(p.get("evasive_availability_statement") or art.get("evasive_availability_statement")),
    }


def _surname(name):
    name = str(name or "").strip()
    if not name:
        return ""
    if "," in name:
        return name.split(",", 1)[0].strip()
    parts = name.split()
    if len(parts) > 1 and parts[-1].isupper() and len(parts[-1].replace(".", "")) <= 3:
        return parts[0]  # PubMed style "Smith AB"
    return parts[-1]


def citation_tag(p):
    """Canonical citation label: [Smith et al., 2023] | [Smith & Jones, 2023] | [Smith, 2023] | [Anon., n.d.]."""
    names = [_surname(a) for a in (p.get("authors") or []) if _surname(a)]
    year = p.get("year") or "n.d."
    if len(names) >= 3:
        who = f"{names[0]} et al."
    elif len(names) == 2:
        who = f"{names[0]} & {names[1]}"
    elif names:
        who = names[0]
    else:
        who = "Anon."
    return f"[{who}, {year}]"


def analyze_paper(p, fulltext=""):
    """Extract the 5 comparison dimensions of a paper record -> (ComparisonDimension, extras)."""
    title = p.get("title") or p.get("id") or "Untitled"
    abstract = p.get("abstract") or ""
    sents = _sentences(abstract)
    ft_sents = _sentences(fulltext[:FULLTEXT_CHARS]) if fulltext else []
    domain = _domain(p)
    evidence = p.get("evidence") or "other"
    arts = _artifacts(p)

    hypothesis = _first_match(sents, _HYPOTHESIS) or (sents[0] if sents else title)
    method_sent = _first_match(sents, _METHOD, skip=(hypothesis,)) or _first_match(ft_sents, _METHOD)
    methodology = f"Design: {evidence} | Domain: {domain}"
    if method_sent:
        methodology += f" | {_clip(method_sent)}"

    text = " ".join([title, abstract, fulltext[:FULLTEXT_CHARS] if fulltext else ""])
    benchmarks = _benchmarks(text)
    metrics = _metrics(text, sents + ft_sents, skip=(hypothesis,))

    limitations = [
        _clip(s) for s in _all_matches(sents + ft_sents, _LIMITATION, limit=3, skip=(hypothesis, method_sent))
    ]
    if evidence == "preclinical":
        limitations.append("Preclinical evidence (animal / in vitro) — direct human translation not established.")
    if arts["evasive"]:
        limitations.append("Evasive availability statement: artifacts only available on request.")
    dim = ComparisonDimension(
        paper_id=str(p.get("id") or title),
        paper_title=title,
        year=p.get("year"),
        domain=domain,
        evidence_level=evidence,
        core_hypothesis=_clip(hypothesis, 400),
        methodology=methodology,
        benchmarks_used=benchmarks,
        primary_metrics=metrics,
        reported_limitations=limitations or [NO_LIMITATIONS],
    )
    return dim, {"artifacts": arts, "tag": citation_tag(p), "sentences": sents + ft_sents, "paper": p}


# --------------------------------------------------------------------------- shared validation / resolution


def _validate_ids(paper_ids, max_n):
    core = _core()
    if isinstance(paper_ids, str):
        paper_ids = paper_ids.split(",")
    error = f"paper_ids must be a non-empty list of strings (1 to {max_n} IDs/DOIs)"
    if not isinstance(paper_ids, (list, tuple)) or any(not isinstance(x, str) for x in paper_ids):
        raise core.HTTPError(400, error)
    ids = list(dict.fromkeys(x.strip() for x in paper_ids if x.strip()))
    if not ids:
        raise core.HTTPError(400, error)
    if len(ids) > max_n:
        raise core.HTTPError(400, f"paper_ids accepts at most {max_n} IDs (got {len(ids)})")
    return ids


def _resolve_all(ids, inspect_fulltext=False):
    core = _core()
    records, warnings = [], []
    for pid in ids:
        try:
            p = core.resolve_paper(pid)
        except Exception as e:
            warnings.append(f"Could not resolve '{pid}': {e}")
            continue
        text = ""
        if inspect_fulltext:
            try:
                if core.fulltext(p["id"]).get("available"):
                    text = (core.cache_get("ft:" + p["id"]) or {}).get("text") or ""
                else:
                    warnings.append(f"Full text not available for '{p['id']}'; used title/abstract only.")
            except Exception as e:
                warnings.append(
                    f"Full text unavailable for '{p['id']}' ({type(e).__name__}); used title/abstract only."
                )
        records.append((p, text))
    if not records:
        raise core.HTTPError(404, "None of the paper IDs could be resolved. " + " ".join(warnings))
    return records, warnings


# --------------------------------------------------------------------------- #04 comparison matrix


def _cell(text, n=320):
    return _clip(str(text or "—").replace("\r", " ").replace("\n", " ").replace("|", "\\|"), n) or "—"


def _results_cell(metrics):
    extras = []
    for k, v in metrics.items():
        if k == "headline_finding":
            continue
        extras.append(f"{k}: {', '.join(v) if isinstance(v, list) else v}")
    head = metrics.get("headline_finding") or ""
    return head + (f" ({'; '.join(extras)})" if extras else "")


def _markdown_table(dims):
    lines = [
        "| Paper | Core Problem | Methodology | Datasets / Population | Key Results | Declared Limitations |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for d in dims:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(f"{d.paper_title} ({d.year or 'n.d.'})", 160),
                    _cell(d.core_hypothesis),
                    _cell(d.methodology),
                    _cell("; ".join(d.benchmarks_used)),
                    _cell(_results_cell(d.primary_metrics), 400),
                    _cell("; ".join(d.reported_limitations)),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _real_benchmarks(dim):
    return [b for b in dim.benchmarks_used if b != NOT_STATED]


def _shared(items_by_tag):
    """{tag: [values]} -> {value: [tags]} for values present in >= 2 papers."""
    owners = {}
    for tag, values in items_by_tag.items():
        for v in dict.fromkeys(values):
            owners.setdefault(v, []).append(tag)
    return {v: tags for v, tags in owners.items() if len(tags) >= 2}


def _ev_weight(level):
    return float(_core().EV_W.get(level, _core().EV_W.get("other", 0.3)))


def _summaries(rows):
    """rows: [(dim, extras)] -> (convergence_summary, divergence_summary)."""
    n = len(rows)
    if n < 2:
        dim, ex = rows[0]
        msg = (
            f"Single paper {ex['tag']} ({dim.evidence_level}, {dim.domain}); "
            "cross-paper {} requires at least two papers."
        )
        return msg.format("convergence"), msg.format("divergence")

    conv = []
    domains = Counter(d.domain for d, _ in rows)
    if len(domains) == 1:
        conv.append(f"All {n} papers belong to the {next(iter(domains))} domain.")
    else:
        common = [f"{k} ({v})" for k, v in domains.most_common() if v >= 2]
        if common:
            conv.append("Shared domain(s): " + ", ".join(common) + ".")
    for label, getter in (
        ("study design", lambda d: [d.evidence_level]),
        ("datasets/benchmarks/populations", _real_benchmarks),
        ("reported metric types", lambda d: [k for k in d.primary_metrics if k != "headline_finding"]),
    ):
        shared = _shared({ex["tag"]: getter(d) for d, ex in rows})
        if shared:
            conv.append(f"Shared {label}: " + "; ".join(f"{v} ({', '.join(t)})" for v, t in shared.items()) + ".")
    if len(conv) <= 1:
        conv.append("No shared benchmarks, populations or metric types were detected in the abstracts.")

    div = []
    levels = sorted({d.evidence_level for d, _ in rows}, key=_ev_weight, reverse=True)
    if len(levels) > 1:
        div.append(
            "Evidence levels differ: "
            + " vs. ".join(
                f"{lvl} (weight {_ev_weight(lvl)}: {', '.join(ex['tag'] for d, ex in rows if d.evidence_level == lvl)})"
                for lvl in levels
            )
            + "."
        )
    if len(domains) > 1:
        div.append(
            "Methodological traditions differ across domains: "
            + "; ".join(f"{ex['tag']} {d.domain}" for d, ex in rows)
            + "."
        )
    uniq = {
        ex["tag"]: [b for b in _real_benchmarks(d) if sum(b in _real_benchmarks(o) for o, _ in rows) == 1]
        for d, ex in rows
    }
    uniq = {k: v for k, v in uniq.items() if v}
    if uniq:
        div.append(
            "Distinct datasets/populations/scale: " + "; ".join(f"{k} {', '.join(v)}" for k, v in uniq.items()) + "."
        )
    openness = {ex["tag"]: [k for k in ("code", "weights", "dataset") if ex["artifacts"][f"has_{k}"]] for _, ex in rows}
    if len({tuple(v) for v in openness.values()}) > 1:
        div.append(
            "Openness differs: "
            + "; ".join(f"{k} {'+'.join(v) if v else 'no open artifacts'}" for k, v in openness.items())
            + "."
        )
    flagged = [(ex["tag"], d.reported_limitations) for d, ex in rows if d.reported_limitations != [NO_LIMITATIONS]]
    if flagged and len(flagged) < n:
        div.append(
            "Only "
            + ", ".join(t for t, _ in flagged)
            + " declare limitations ("
            + "; ".join(_clip(lims[0], 120) for _, lims in flagged)
            + ")."
        )
    elif flagged:
        div.append(
            "Declared limitations differ: " + "; ".join(f"{t} {_clip(lims[0], 120)}" for t, lims in flagged) + "."
        )
    if not div:
        div.append("No technical divergence detected: same design, domain, datasets and openness profile.")
    return " ".join(conv), " ".join(div)


def _disambiguate(rows):
    """Same first author + year -> [Smith et al., 2023a] / [Smith et al., 2023b] (in-place on extras["tag"])."""
    counts = Counter(ex["tag"] for _, ex in rows)
    seen = Counter()
    for _, ex in rows:
        tag = ex["tag"]
        if counts[tag] > 1:
            ex["tag"] = f"{tag[:-1]}{'abcdefghijklmnopqrstuvwxyz'[seen[tag] % 26]}]"
            seen[tag] += 1
    return rows


def compare_papers(paper_ids, inspect_fulltext=False):
    """Parameterized comparison matrix (#04): core problem, methodology, datasets/population, key results and
    declared limitations for 1-10 papers, plus a Markdown table and convergence/divergence summaries."""
    ids = _validate_ids(paper_ids, MAX_COMPARE)
    records, warnings = _resolve_all(ids, inspect_fulltext=bool(inspect_fulltext))
    rows = _disambiguate([analyze_paper(p, text) for p, text in records])
    convergence, divergence = _summaries(rows)
    result = ComparativeMatrixResult(
        papers=[d for d, _ in rows],
        convergence_summary=convergence,
        divergence_summary=divergence,
        markdown_table=_markdown_table([d for d, _ in rows]),
    ).model_dump(mode="json")
    if warnings:
        result["warnings"] = warnings
    return result


# --------------------------------------------------------------------------- #05 thematic insights

MAX_INSIGHTS = 20
STOPWORDS = frozenset(
    """
a about above across after again against all also although among an and any are as at based be because been
before being between both but by can could did do does doing during each either et al few for from further had
has have having here how however i if in into is it its itself may might more most much must no nor not of off
on once only or other our out over own same several should so some such than that the their them then there
these they this those through thus to too under until up upon very via was we were what when where whether which
while who whom why will with within without would yet
abstract aim aims approach approaches analysis article background conclusion conclusions data demonstrate
demonstrated evaluate evaluated evidence finding findings first high higher however introduce introduced
investigate investigated low lower method methods new novel objective objectives outcome outcomes paper
propose proposed present presented purpose result results show showed shown shows significant significantly
studies study suggest suggests use used uses using work works whereas able available upon reasonable request
""".split()
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]|[a-z]{3,}")
_POSITIVE = re.compile(
    r"\b(?:increas\w*|improv\w*|outperform\w*|achiev\w*|reduc\w*|effective|benefit\w*|"
    r"enhanc\w*|associated with|induc\w*|faster|better)\b",
    re.I,
)
_CONDITIONAL = re.compile(
    r"\b(?:contrary|mixed|inconsistent|conflicting|no significant|not significant|did not|"
    r"failed to|only when|depend(?:s|ed|ing)? on)\b",
    re.I,
)
_FUTURE = re.compile(
    r"\bfuture (?:work|research|studies)\b|\bfurther (?:research|studies|work|trials)\b|"
    r"\bshould (?:be )?(?:stud|investigat|explor|examin|address|test)\w*|\bwarrant\w*\b|"
    r"\bremains? (?:unclear|unknown|open)\b|\bare needed\b",
    re.I,
)
_GAPS = [
    ("small or limited samples", re.compile(r"\bsmall sample|\blimited sample|\bsample size", re.I)),
    ("single-center or narrow setting", re.compile(r"\bsingle[-\s](?:cent(?:er|re)|site)", re.I)),
    ("short follow-up / long-term effects unknown", re.compile(r"\bshort(?:[-\s]term)? follow-up|\bshort-term", re.I)),
    ("observational design (causality not established)", re.compile(r"\bobservational\b|\bcross-sectional", re.I)),
    (
        "translation from preclinical models to humans",
        re.compile(r"\bPreclinical evidence\b|\banimal model|\bin vitro", re.I),
    ),
    ("computational cost / efficiency", re.compile(r"\bcomputational cost|\blatency\b|\bmemory footprint", re.I)),
    ("generalization to other settings", re.compile(r"\bgenerali[sz]", re.I)),
    ("open release of data, code or weights", re.compile(r"\bEvasive availability statement", re.I)),
]


def key_terms(text):
    """Content unigrams + bigrams (lowercase, stopword-free) of a text -> Counter."""
    toks = [w for w in _TOKEN.findall(str(text or "").lower()) if not w.isdigit()]
    terms = Counter(w for w in toks if w not in STOPWORDS and len(w) > 2)
    for a, b in zip(toks, toks[1:], strict=False):
        if a not in STOPWORDS and b not in STOPWORDS:
            terms[f"{a} {b}"] += 1
    return terms


def _join_tags(tags):
    return ", ".join(tags)


def _concept_clusters(rows, max_clusters=3):
    """Terms shared by >= 2 papers, one cluster per distinct set of papers -> [(term, [row indexes], [co-terms])],
    ordered by support, then term frequency (bigrams win ties)."""
    owners, freq = {}, Counter()
    for i, (d, ex) in enumerate(rows):
        terms = key_terms(f"{d.paper_title}. {ex['paper'].get('abstract') or ''}")
        freq.update(terms)
        for term in terms:
            owners.setdefault(term, set()).add(i)
    shared = [(t, tuple(sorted(ix))) for t, ix in owners.items() if len(ix) >= 2]
    shared.sort(key=lambda x: (-len(x[1]), -freq[x[0]], -(" " in x[0]), x[0]))
    by_set = {}
    for term, ix in shared:
        by_set.setdefault(ix, []).append(term)
    clusters = []
    for ix, terms in list(by_set.items())[:max_clusters]:
        head = terms[0]
        co = [t for t in terms[1:] if t not in head and not set(t.split()) <= set(head.split())][:3]
        clusters.append((head, list(ix), co))
    return clusters


def _favourable(d):
    head = d.primary_metrics.get("headline_finding") or ""
    return bool(_POSITIVE.search(head)) and not _CONDITIONAL.search(head)


def _headline(d):
    return _clip(d.primary_metrics.get("headline_finding") or d.core_hypothesis, 180)


def _consensuses(rows, clusters):
    out = []
    for term, ix, co in clusters:
        group = [rows[i] for i in ix]
        tags = [ex["tag"] for _, ex in group]
        positive = [ex["tag"] for d, ex in group if _favourable(d)]
        if len(positive) == len(group):
            direction = "all report favourable results"
        elif positive:
            direction = f"favourable results in {_join_tags(positive)}, heterogeneous elsewhere"
        else:
            direction = "outcomes are reported without a common direction"
        related = f" (related: {', '.join(co)})" if co else ""
        out.append(
            f'Convergent focus on "{term}"{related} across {len(group)} papers ({_join_tags(tags)}): '
            f"{direction} — " + "; ".join(f"'{_headline(d)}' {ex['tag']}" for d, ex in group) + "."
        )
    for bench, tags in _shared({ex["tag"]: _real_benchmarks(d) for d, ex in rows}).items():
        out.append(f"Common evaluation ground: {bench} is used by {_join_tags(tags)}, enabling direct comparison.")
    high = [(d, ex) for d, ex in rows if d.evidence_level in HIGH_EVIDENCE]
    for d, ex in high:
        peers = [
            rows[i][1]["tag"]
            for _, ix, _ in clusters
            if rows.index((d, ex)) in ix
            for i in ix
            if rows[i][1]["tag"] != ex["tag"] and _favourable(rows[i][0]) == _favourable(d)
        ]
        line = f"Highest-level evidence ({d.evidence_level}) {ex['tag']}: '{_headline(d)}'"
        if peers:
            line += f", directionally corroborated by {_join_tags(list(dict.fromkeys(peers)))}"
        out.append(line + ".")
    if not out:
        d, ex = max(rows, key=lambda r: _ev_weight(r[0].evidence_level))
        tags = _join_tags(e["tag"] for _, e in rows)
        out.append(
            f"No cross-paper convergence detected among {tags}; strongest individual finding "
            f"({d.evidence_level}): '{_headline(d)}' {ex['tag']}."
        )
    return out


def _tensions(rows, clusters):
    out = []
    for term, ix, _ in clusters:
        group = sorted((rows[i] for i in ix), key=lambda r: _ev_weight(r[0].evidence_level), reverse=True)
        top, bottom = group[0], group[-1]
        if top[0].evidence_level != bottom[0].evidence_level:
            out.append(
                f'On "{term}": {top[1]["tag"]} ({top[0].evidence_level}, weight '
                f"{_ev_weight(top[0].evidence_level)}) vs. {bottom[1]['tag']} ({bottom[0].evidence_level}, weight "
                f"{_ev_weight(bottom[0].evidence_level)}) — the claim rests on different levels of the evidence "
                "pyramid; weigh the higher-level design more heavily."
            )
    if not out:
        levels = sorted(rows, key=lambda r: _ev_weight(r[0].evidence_level), reverse=True)
        if levels[0][0].evidence_level != levels[-1][0].evidence_level:
            out.append(
                f"{levels[0][1]['tag']} ({levels[0][0].evidence_level}) vs. {levels[-1][1]['tag']} "
                f"({levels[-1][0].evidence_level}): evidence comes from different levels of the pyramid."
            )

    by_domain = {}
    for d, ex in rows:
        by_domain.setdefault(d.domain, []).append((d, ex))
    for domain, group in by_domain.items():
        benched = [(d, ex) for d, ex in group if _real_benchmarks(d)]
        if len({tuple(_real_benchmarks(d)) for d, _ in benched}) > 1:
            out.append(
                "Different datasets/populations/scale ("
                + domain
                + "): "
                + " vs. ".join(f"{ex['tag']} ({', '.join(_real_benchmarks(d))})" for d, ex in benched)
                + " — results are only partially comparable."
            )
        open_ = [
            ex["tag"]
            + (" (links verified)" if ex["artifacts"]["code_verified"] or ex["artifacts"]["weights_verified"] else "")
            for _, ex in group
            if ex["artifacts"]["has_code"] or ex["artifacts"]["has_weights"]
        ]
        closed = [
            ex["tag"] + (" (artifacts only on request)" if ex["artifacts"]["evasive"] else "")
            for _, ex in group
            if not (ex["artifacts"]["has_code"] or ex["artifacts"]["has_weights"])
        ]
        if open_ and closed:
            out.append(
                f"Reproducibility asymmetry ({domain}): {_join_tags(open_)} release code/weights vs. "
                f"{_join_tags(closed)} without open artifacts."
            )

    for d, ex in rows:
        sent = _first_match(ex["sentences"], _CONDITIONAL)
        if not sent:
            continue
        mates = [i for _, ix, _ in clusters if rows.index((d, ex)) in ix for i in ix]
        others = [rows[i] for i in dict.fromkeys(mates) if rows[i][1]["tag"] != ex["tag"]] or [
            r for r in rows if r[1]["tag"] != ex["tag"]
        ]
        contrast = next((r for r in others if _favourable(r[0])), others[0] if others else None)
        if contrast:
            out.append(
                f"Conditional/mixed results: {ex['tag']} ('{_clip(sent, 160)}') vs. {contrast[1]['tag']} "
                f"('{_headline(contrast[0])}')."
            )
        else:
            out.append(f"Conditional/mixed results reported by {ex['tag']}: '{_clip(sent, 160)}'.")
    if not out:
        out.append(
            f"No methodological tension detected: {_join_tags(ex['tag'] for _, ex in rows)} share comparable "
            "designs, datasets and openness."
        )
    return out


def _future_work(rows):
    out = []
    for label, rx in _GAPS:
        tags = [ex["tag"] for d, ex in rows if any(rx.search(x) for x in d.reported_limitations)]
        if tags:
            out.append(f"Gap — {label}: flagged by {_join_tags(tags)}.")
    for _d, ex in rows:
        for sent in _all_matches(ex["sentences"], _FUTURE, limit=2):
            out.append(f"Future direction: '{_clip(sent, 200)}' {ex['tag']}.")
    if not out:
        out.append(
            "No explicit limitations or future work declared; extending the evaluation beyond the current "
            f"scope remains open for {_join_tags(ex['tag'] for _, ex in rows)}."
        )
    return out


def _derive_topic(rows, clusters):
    if clusters:
        return " / ".join(t for t, _, _ in clusters)
    terms = Counter()
    for d, _ in rows:
        terms.update(set(key_terms(d.paper_title)))
    top = [t for t, _ in terms.most_common(3)]
    return " / ".join(top) or "Untitled corpus"


def get_thematic_insights(paper_ids, topic=None):
    """Thematic synthesis (#05): cross-cutting consensuses, methodological tensions and future work for 1-20 papers;
    every statement carries explicit [Author et al., Year] citations (see `citations_map`)."""
    from noul.schemas.analysis import ThematicInsightsResult

    ids = _validate_ids(paper_ids, MAX_INSIGHTS)
    records, warnings = _resolve_all(ids)
    rows = _disambiguate([analyze_paper(p) for p, _ in records])
    clusters = _concept_clusters(rows)
    citations = {}
    for d, ex in rows:
        citations.setdefault(ex["tag"], []).append(d.paper_id)
    result = ThematicInsightsResult(
        topic=str(topic).strip() if topic and str(topic).strip() else _derive_topic(rows, clusters),
        consolidated_consensuses=_consensuses(rows, clusters),
        methodological_tensions=_tensions(rows, clusters),
        emerging_future_work=_future_work(rows),
        citations_map=citations,
    ).model_dump(mode="json")
    if warnings:
        result["warnings"] = warnings
    return result


# --------------------------------------------------------------------------- #06 Obsidian / Markdown export

MAX_EXPORT = 50
_FS_UNSAFE = re.compile(r'[\\/:*?"<>|\[\]#^\x00-\x1f]')


def _safe_filename_part(text, max_len):
    text = re.sub(r"\s+", " ", _FS_UNSAFE.sub(" ", str(text or ""))).strip().strip(".")
    return (text[:max_len].rstrip(" .") if len(text) > max_len else text) or "Untitled"


def note_name(p):
    """`<Year> - <FirstAuthor> - <ShortTitle>` (no extension), safe on Windows/Linux/macOS and inside [[wikilinks]]."""
    authors = [a for a in (p.get("authors") or []) if _surname(a)]
    short = " ".join(str(p.get("title") or p.get("id") or "Untitled").split()[:8])
    return " - ".join(
        [
            _safe_filename_part(p["year"], 8) if p.get("year") else "n.d.",
            _safe_filename_part(_surname(authors[0]) if authors else "Anon", 40),
            _safe_filename_part(short, 60),
        ]
    )


def _yaml(value):
    import json

    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)  # JSON strings are valid YAML double-quoted scalars


def _frontmatter(p, dim, arts):
    fields = [
        ("title", dim.paper_title),
        ("paper_id", dim.paper_id),
        ("doi", p.get("doi")),
        ("year", p.get("year")),
    ]
    lines = ["---"] + [f"{k}: {_yaml(v)}" for k, v in fields]
    lines.append("authors:" if p.get("authors") else "authors: []")
    lines += [f"  - {_yaml(a)}" for a in p.get("authors") or []]
    lines += [
        f"{k}: {_yaml(v)}"
        for k, v in (
            ("venue", p.get("journal") or p.get("venue")),
            ("domain", dim.domain),
            ("evidence_level", dim.evidence_level),
            ("citations", int(p.get("citations") or 0)),
            ("open_access", bool(p.get("open_access"))),
            ("has_code", arts["has_code"]),
            ("has_weights", arts["has_weights"]),
        )
    ]
    lines += ["tags:"] + [
        f"  - {_yaml(t)}" for t in ("noul/paper", f"domain/{dim.domain}", f"evidence/{dim.evidence_level}")
    ]
    return "\n".join(lines + ["---"])


def _note_markdown(p, dim, arts, related, bibtex):
    head = dim.primary_metrics.get("headline_finding") or ""
    tldr = dim.core_hypothesis + ("" if not head or head == dim.core_hypothesis else f" **Key finding:** {head}")
    metrics = "; ".join(
        f"{k}: {', '.join(v) if isinstance(v, list) else v}"
        for k, v in dim.primary_metrics.items()
        if k != "headline_finding"
    )
    body = (
        [
            _frontmatter(p, dim, arts),
            "",
            f"# {dim.paper_title}",
            "",
            "## TL;DR",
            tldr,
            "",
            "## Key Findings & Methodology",
            f"- **Core problem:** {dim.core_hypothesis}",
            f"- **Methodology:** {dim.methodology}",
            f"- **Datasets / Population:** {'; '.join(dim.benchmarks_used)}",
            f"- **Key results:** {head}" + (f" ({metrics})" if metrics else ""),
            "- **Declared limitations:**",
        ]
        + [f"  - {x}" for x in dim.reported_limitations]
        + ["", "## Open Artifacts"]
    )
    found = False
    for kind, label in (("code", "Code"), ("weights", "Weights"), ("dataset", "Dataset")):
        url = arts.get(f"{kind}_url")
        if arts[f"has_{kind}"] and url:
            found = True
            status = "verified (HTTP)" if arts[f"{kind}_verified"] else "not verified"
            body.append(f"- **{label}:** <{url}> — {status}")
    if not found:
        body.append("- No open artifacts detected.")
    if arts["evasive"]:
        body.append("- ⚠️ Evasive availability statement (artifacts only on request).")
    if related is not None:
        body += ["", "## Related Notes (Wikilinks)"] + ([f"- [[{r}]]" for r in related] or ["- _None in this export._"])
    body += ["", "## BibTeX", "```bibtex", bibtex.strip(), "```", ""]
    return "\n".join(body)


def _write_note(path, text):
    """Create/overwrite a note without following a symlink planted at its path (O_NOFOLLOW when available)."""
    import os

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def export_obsidian_notes(paper_ids, vault_dir=None, include_related_wikilinks=True):
    """Atomic Markdown notes (#06) with YAML frontmatter, TL;DR, findings/methodology, open artifacts,
    [[wikilinks]] between the exported papers and a BibTeX block, written inside the workspace."""
    import noul_tools

    core = _core()
    ids = _validate_ids(paper_ids, MAX_EXPORT)
    dest = noul_tools._safe_output_dir(vault_dir, default_subdir="obsidian")
    noul_tools._reject_symlink_path(dest, vault_dir)
    records, warnings = _resolve_all(ids)
    rows = [analyze_paper(p) for p, _ in records]

    names, used = [], set()
    for _dim, ex in rows:
        base, n = note_name(ex["paper"]), 2
        name = base
        while name.lower() in used:
            name, n = f"{base} ({n})", n + 1
        used.add(name.lower())
        names.append(name)

    dest.mkdir(parents=True, exist_ok=True)
    noul_tools._reject_symlink_path(dest, vault_dir)
    notes = []
    for i, ((dim, ex), name) in enumerate(zip(rows, names, strict=False)):
        path = dest / f"{name}.md"
        if path.is_symlink():
            raise core.HTTPError(403, f"Refusing to write through symbolic link '{path}'.")
        related = [x for j, x in enumerate(names) if j != i] if include_related_wikilinks else None
        bibtex = core.export([ex["paper"]], "bibtex")[0]
        _write_note(path, _note_markdown(ex["paper"], dim, ex["artifacts"], related, bibtex))
        notes.append(
            {"paper_id": dim.paper_id, "title": dim.paper_title, "filepath": str(path), "wikilink": f"[[{name}]]"}
        )
    out = {"vault_dir": str(dest), "exported_count": len(notes), "notes": notes}
    if warnings:
        out["warnings"] = warnings
    return out


# --------------------------------------------------------------------------- #18 autonomous research gap & hypothesis engine

MAX_HYPOTHESIS_PAPERS = 15

_DEFAULT_METRICS_BY_DOMAIN = {
    "computer_science": ["Accuracy / Pass@1", "Latency (ms/token)", "Peak VRAM (GB)", "OOD Generalization Delta (%)"],
    "biomedical": [
        "Primary Clinical Endpoint (95% CI)",
        "Hazard / Odds Ratio (p-value)",
        "Adverse Event Rate (CTCAE >= 2)",
        "Intention-to-Treat Attrition (%)",
    ],
    "economics": [
        "Average Treatment Effect (ATE / ATT)",
        "Cluster-Robust Standard Error",
        "Pre-Trend Placebo F-Statistic",
        "Welfare / Elasticity Magnitude",
    ],
    "general_stem": [
        "Primary Quantitative Effect Size",
        "95% Confidence / Uncertainty Interval",
        "Replication Concordance Rate",
        "Computational / Experimental Cost",
    ],
}

_DEFAULT_DATASETS_BY_DOMAIN = {
    "computer_science": ["MMLU", "GSM8K", "LongBench / 32k+ Context Stress Suite"],
    "biomedical": ["Multi-center randomized clinical cohort (N >= 200)", "External registry validation cohort"],
    "economics": ["Balanced longitudinal firm/household panel dataset", "Out-of-sample regional holdout panel"],
    "general_stem": ["Standardized multi-site benchmark dataset", "Controlled out-of-distribution testbed"],
}


def _short_method_label(dim):
    title_words = [
        w for w in re.findall(r"[A-Za-z0-9][-A-Za-z0-9]+", dim.paper_title or "") if w.lower() not in STOPWORDS
    ]
    short = " ".join(title_words[:3]) or dim.paper_id
    return f"{short} ({dim.evidence_level})"


def _format_gap_markdown(report, topic_label):
    lines = [
        f"# Autonomous Research Gap & Hypothesis Report: {topic_label}",
        "",
        f"- **Papers Cross-Analyzed ({len(report.paper_ids)}):** {', '.join(f'`{pid}`' for pid in report.paper_ids)}",
        "",
        "## 1. Contradictions & Methodological Tensions Found",
    ]
    for c in report.contradictions_found:
        lines.append(f"- {c}")
    lines += ["", "## 2. Unexplored Complementarities"]
    for comp in report.unexplored_complementarities:
        lines.append(f"- {comp}")
    lines += ["", "## 3. Testable Research Hypotheses"]
    for h in report.hypotheses:
        lines += [
            "",
            f"### Hypothesis {h.index}: {h.title}",
            f"- **Source Papers:** {', '.join(f'`{p}`' for p in h.source_papers)}",
            f"- **Rationale:** {h.rationale}",
            f"- **Proposed Methodology:** {h.proposed_methodology}",
            f"- **Required Baselines:** {'; '.join(h.required_baselines)}",
            f"- **Target Datasets / Population:** {'; '.join(h.target_datasets_or_population)}",
            f"- **Evaluation Metrics:** {'; '.join(h.evaluation_metrics)}",
        ]
    return "\n".join(lines)


def generate_research_hypotheses(paper_ids, topic=None, max_hypotheses=3):
    """Cross-analyze 2-15 papers (#18) to detect methodological contradictions, unexplored complementarities,
    and formulate structured, testable research hypotheses with baselines, datasets/populations, and metrics."""
    from noul.schemas.analysis import ResearchGapReport, ResearchHypothesis
    from noul.tools import technical_tools

    core = _core()
    if isinstance(paper_ids, str):
        paper_ids = [x.strip() for x in paper_ids.split(",") if x.strip()]
    if not isinstance(paper_ids, (list, tuple)) or any(not isinstance(x, str) for x in paper_ids):
        raise core.HTTPError(400, "Provide at least 2 paper_ids to cross-analyze research gaps and generate hypotheses")
    ids = list(dict.fromkeys(x.strip() for x in paper_ids if x.strip()))
    if len(ids) < 2:
        raise core.HTTPError(400, "Provide at least 2 paper_ids to cross-analyze research gaps and generate hypotheses")
    if len(ids) > MAX_HYPOTHESIS_PAPERS:
        raise core.HTTPError(400, f"paper_ids accepts at most {MAX_HYPOTHESIS_PAPERS} IDs (got {len(ids)})")

    try:
        max_h = max(1, min(int(max_hypotheses or 3), 10))
    except (TypeError, ValueError):
        raise core.HTTPError(400, "max_hypotheses must be an integer between 1 and 10") from None

    records, warnings = _resolve_all(ids, inspect_fulltext=True)
    if len(records) < 2:
        raise core.HTTPError(400, "Provide at least 2 paper_ids to cross-analyze research gaps and generate hypotheses")

    rows = _disambiguate([analyze_paper(p, text) for p, text in records])
    clusters = _concept_clusters(rows)
    topic_label = str(topic).strip() if topic and str(topic).strip() else _derive_topic(rows, clusters)

    failures_by_id = {}
    for d, _ in rows:
        try:
            failures_by_id[d.paper_id] = technical_tools.detect_failure_modes(d.paper_id, inspect_fulltext=True)
        except Exception:
            failures_by_id[d.paper_id] = {
                "failure_modes": [],
                "critical_assumptions": [],
                "baseline_superiority_scenarios": [],
                "operational_limits": {},
            }

    # 1. Contradictions & Methodological Tensions across pairs (A, B)
    contradictions = []
    for t_line in _tensions(rows, clusters):
        if not t_line.startswith("No methodological tension detected"):
            contradictions.append(t_line)

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            d_a, ex_a = rows[i]
            d_b, ex_b = rows[j]
            tag_a, tag_b = ex_a["tag"], ex_b["tag"]
            fm_a = failures_by_id.get(d_a.paper_id) or {}
            fm_b = failures_by_id.get(d_b.paper_id) or {}

            assump_a = (fm_a.get("critical_assumptions") or [None])[0]
            assump_b = (fm_b.get("critical_assumptions") or [None])[0]
            if assump_a or assump_b:
                a_txt = _clip(assump_a or d_a.reported_limitations[0], 130)
                b_txt = _clip(assump_b or d_b.reported_limitations[0], 130)
                contradictions.append(
                    f"Divergent modeling assumptions & trade-offs between {tag_a} ('{a_txt}') and {tag_b} ('{b_txt}')."
                )
            else:
                contradictions.append(
                    f"Methodological trade-off between {tag_a} ({_clip(d_a.methodology, 110)}) and "
                    f"{tag_b} ({_clip(d_b.methodology, 110)}) on target scope/evaluation."
                )

    contradictions = list(dict.fromkeys(contradictions))[:8]

    # 2. Unexplored Complementarities across pairs (A, B)
    complementarities = []
    for i in range(len(rows)):
        for j in range(len(rows)):
            if i == j:
                continue
            d_a, ex_a = rows[i]
            d_b, ex_b = rows[j]
            tag_a, tag_b = ex_a["tag"], ex_b["tag"]
            fm_b = failures_by_id.get(d_b.paper_id) or {}
            b_modes = fm_b.get("failure_modes") or []
            b_lim = _clip(b_modes[0]["description"], 140) if b_modes else _clip(d_b.reported_limitations[0], 140)
            a_strength = _headline(d_a)
            complementarities.append(
                f"Unexplored synergy ({tag_a} -> {tag_b}): {tag_a}'s mechanism ('{a_strength}') can mitigate "
                f"the limitation/failure mode identified in {tag_b} ('{b_lim}')."
            )
            if len(complementarities) >= 6:
                break
        if len(complementarities) >= 6:
            break

    # 3. Formulate 1..max_h testable ResearchHypothesis items crossing >= 2 papers
    pair_candidates = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            pair_candidates.append((rows[i], rows[j]))
    if len(rows) >= 3 and len(pair_candidates) < max_h:
        pair_candidates.append((rows[0], rows[-1]))

    hypotheses = []
    for idx in range(1, max_h + 1):
        (d_a, ex_a), (d_b, ex_b) = pair_candidates[(idx - 1) % len(pair_candidates)]
        tag_a, tag_b = ex_a["tag"], ex_b["tag"]
        fm_a = failures_by_id.get(d_a.paper_id) or {}
        fm_b = failures_by_id.get(d_b.paper_id) or {}

        source_ids = [d_a.paper_id, d_b.paper_id]
        if idx == 3 and len(rows) >= 3:
            third_id = rows[2][0].paper_id
            if third_id not in source_ids:
                source_ids.append(third_id)

        lim_b = (
            _clip(fm_b["failure_modes"][0]["description"], 140)
            if fm_b.get("failure_modes")
            else _clip(d_b.reported_limitations[0], 140)
        )
        lim_a = (
            _clip(fm_a["failure_modes"][0]["description"], 140)
            if fm_a.get("failure_modes")
            else _clip(d_a.reported_limitations[0], 140)
        )

        dom = d_a.domain if d_a.domain != "general_stem" else d_b.domain
        shared_benches = list(dict.fromkeys(_real_benchmarks(d_a) + _real_benchmarks(d_b)))
        datasets = shared_benches[:4] or list(
            _DEFAULT_DATASETS_BY_DOMAIN.get(dom, _DEFAULT_DATASETS_BY_DOMAIN["general_stem"])
        )

        extracted_metrics = [
            k
            for d in (d_a, d_b)
            for k in d.primary_metrics.keys()
            if k not in ("headline_finding", "percentages", "p_values", "confidence_intervals")
        ]
        default_metrics = _DEFAULT_METRICS_BY_DOMAIN.get(dom, _DEFAULT_METRICS_BY_DOMAIN["general_stem"])
        metrics = list(dict.fromkeys(extracted_metrics + default_metrics))[:5]

        baselines = [
            f"{_short_method_label(d_a)} {tag_a}",
            f"{_short_method_label(d_b)} {tag_b}",
            f"Ablated single-mechanism baseline (without {tag_a} + {tag_b} coupling)",
        ]

        if idx % 3 == 1:
            h_title = (
                f"Hybrid Integration of {_short_method_label(d_a)} and {_short_method_label(d_b)} for {topic_label}"
            )
            rationale = (
                f"While {tag_a} demonstrates '{_headline(d_a)}', it exhibits vulnerability to '{lim_a}'. "
                f"Conversely, {tag_b} addresses complementary structural constraints but suffers from '{lim_b}'. "
                f"Coupling {tag_a} and {tag_b} directly tests whether their combined inductive biases eliminate both failure modes."
            )
            methodology = (
                f"Construct a unified two-stage pipeline integrating {tag_a}'s core mechanism with {tag_b}'s "
                f"regularization/identification design; evaluate under identical splits and conducts a 2x2 factorial "
                f"ablation across {', '.join(datasets[:2])}."
            )
        elif idx % 3 == 2:
            h_title = f"Stress-Testing & Boundary Reconciliation Between {tag_a} and {tag_b}"
            rationale = (
                f"{tag_a} and {tag_b} operate under divergent assumptions and evaluation scales, leaving an open gap "
                f"when transitioning from {tag_a}'s regime to {tag_b}'s failure boundary ('{lim_b}')."
            )
            methodology = (
                f"Benchmark {tag_a} and {tag_b} on a standardized cross-regime evaluation protocol with controlled "
                f"perturbation sweeps (noise, scale, and out-of-distribution shift) over {', '.join(datasets[:2])}."
            )
        else:
            h_title = f"Adaptive Gating / Meta-Selection Across {tag_a} and {tag_b} Regimes"
            rationale = (
                f"Neither {tag_a} nor {tag_b} dominates across all operational regimes ('{lim_a}' vs '{lim_b}'). "
                f"An adaptive routing or regime-switching protocol can achieve Pareto-superior trade-offs."
            )
            methodology = (
                f"Implement a lightweight regime selector conditioned on input/cohort diagnostics that dynamically "
                f"allocates between {tag_a} and {tag_b}, validating efficiency and robustness gains."
            )

        hypotheses.append(
            ResearchHypothesis(
                index=idx,
                title=h_title,
                rationale=rationale,
                source_papers=source_ids,
                proposed_methodology=methodology,
                required_baselines=baselines,
                target_datasets_or_population=datasets,
                evaluation_metrics=metrics,
            )
        )

    report = ResearchGapReport(
        paper_ids=[d.paper_id for d, _ in rows],
        contradictions_found=contradictions,
        unexplored_complementarities=complementarities,
        hypotheses=hypotheses,
        markdown_report="",
    )
    report.markdown_report = _format_gap_markdown(report, topic_label)
    out = report.model_dump(mode="json")
    if warnings:
        out["warnings"] = warnings
    return out
