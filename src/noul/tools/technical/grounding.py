"""Claim grounding: each claim is matched to the passages of the paper that state it, with extrapolation alerts (`verify_paper_claims`)."""

import re

from noul import errors as _errors
from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.providers import pdf_parser
from noul.schemas.technical import GroundedClaim
from noul.tools.technical import common as _common
from noul.tools.technical import tables as _tables

MAX_CLAIMS = 30


AUTO_CLAIMS = 10


_CLAIM_NUM = re.compile(
    r"(?<![A-Za-z][-_])(?<![\w.])[-+−]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%|\s?(?:x|×|pp|ms|GB|B|M|K)\b)?"
)


_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\-]{2,}")


_STOP = frozenset(
    "the and for with that this our from are was were has have had than into its their these those which while "
    "using used use based also both can more most such between over under per all any each not but when where "
    "model models method approach paper work results result show shows showed achieve achieves achieved "
    "que com para uma umas uns dos das nos nas pelo pela como mais por modelo modelos resultado resultados "
    "artigo trabalho sobre entre ser são foi tem".split()
)


_EXTRAPOLATION_CUES = re.compile(
    r"\b(?:will|always|never|guarantees?|proves?|any|every|all\s+(?:tasks|domains|hardware|settings)|"
    r"universally|therefore|thus|implies|sempre|nunca|garante|prova|qualquer|todos?\s+os)\b",
    re.I,
)


_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=[\w%)\]]))+[.!?]?")


def _claim_numbers(text):
    return [re.sub(r"\s+", "", m.group(0)) for m in _CLAIM_NUM.finditer(text or "")]


def _num_value(token):
    m = re.match(r"[-+−]?\d+(?:,\d{3})*(?:\.\d+)?", token)
    return float(m.group(0).replace(",", "").replace("−", "-")) if m else None


def _keywords(text):
    out = set()
    for w in _WORD.findall(text or ""):
        canon = _tables.normalize_metric_name(w)
        if canon in _tables.METRIC_SYNONYMS.values():
            out.add(canon.lower())
        elif w.lower() not in _STOP:
            out.add(w.lower())
    return out


def _passages(corpus):
    """Index the document as (page, label, quote, scoring text) passages: one per table row, one per sentence."""
    text, pages, sections = corpus["text"], corpus["pages"], corpus["sections"]
    tables = _tables.parse_benchmark_tables(text, pages, corpus["captions"])
    out, spans = [], []
    for tb in tables:
        if tb["format"] == "inline":
            continue
        s, e = tb["_span"]
        spans.append((s, e))
        number = tb["label"].split()[-1]
        context = " ".join([tb["caption"], " ".join(tb["headers"])])
        pos = s
        for line in text[s:e].split("\n"):
            quote = re.sub(r"\s+", " ", line).strip()
            if quote and not _tables._MD_SEP.match(quote) and _tables._LATEX_RULES.sub("", quote).strip():
                out.append(
                    {
                        "page": pdf_parser.page_at(pages, pos),
                        "label": f"Table {number}",
                        "quote": quote,
                        "scoring": f"{context} {_tables.clean_cell(quote)}",
                    }
                )
            pos += len(line) + 1
    # Sentences wrap across PDF lines: match on the same-length "flowed" view of the text.
    for m in _SENTENCE.finditer(pdf_parser.flow_text(text, sections, corpus["captions"], spans)):
        if any(s <= m.start() < e for s, e in spans):
            continue
        quote = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(quote) < 8 or not re.search(r"[A-Za-z]", quote):
            continue
        section = pdf_parser.section_at(sections, m.start())
        if section:
            label = f"Section {_untrusted.label(section)}"
        else:
            label = "Abstract" if corpus["source"] == "abstract" else "Body text"
        out.append({"page": pdf_parser.page_at(pages, m.start()), "label": label, "quote": quote, "scoring": quote})
    for p in out:
        p["values"] = {_num_value(x) for x in _claim_numbers(p["scoring"])} - {None}
        p["keywords"] = _keywords(p["scoring"])
    return out


def _clip_quote(quote, numbers, limit=220):
    if len(quote) <= limit:
        return quote
    anchor = 0
    for n in numbers:
        i = quote.find(re.sub(r"[^\d.,]", "", n))
        if i >= 0:
            anchor = i
            break
    start = max(0, min(anchor - limit // 3, len(quote) - limit))
    clip = quote[start : start + limit].strip()
    return ("..." if start > 0 else "") + clip + ("..." if start + limit < len(quote) else "")


# Page furniture that contains numbers but is not a claim: venue headers, arXiv stamps, author footnotes.
_BOILERPLATE = re.compile(
    r"(?i)\barxiv:\s*\d{4}\.\d{4,5}|\b(?:conference\s+on|proceedings\s+of|workshop\s+on|preprint|copyright|©|"
    r"all\s+rights\s+reserved|equal\s+contribution|work\s+(?:performed|done)\s+while|corresponding\s+author|"
    r"licen[cs]ed?\s+under|permission\s+to\s+reproduce|codebase)\b"
)


_NUMBERED_HEADING = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}\s+[A-Z][^.!?]{0,80}$")


_CITATION_REF = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")


def _claim_key(sentence):
    """Loose identity of a sentence (the metadata abstract and the PDF text spell the same one differently)."""
    return re.sub(r"[^a-z0-9]", "", sentence.lower())[:80]


def _auto_claims(corpus, passages):
    """Numeric / comparative sentences: from the abstract first, then from the non-table body text."""
    comparative = re.compile(
        r"\b(?:outperform\w*|improv\w*|better than|surpass\w*|reduc\w*|faster|"
        r"state[-\s]of[-\s]the[-\s]art)\b",
        re.I,
    )
    abstract = [re.sub(r"\s+", " ", m.group(0)).strip() for m in _SENTENCE.finditer(corpus["abstract"] or "")]
    body = [p["quote"] for p in passages if not p["label"].startswith("Table ")]
    claims = []
    for group in (abstract, body):
        in_body = group is body
        for s in group:
            # "[13]" is a reference, not a result; outside the abstract a claim must carry numbers.
            if (
                len(s.split()) >= 5
                and not _BOILERPLATE.search(s)
                and _claim_key(s) not in {_claim_key(c) for c in claims}
                and not _NUMBERED_HEADING.match(s)
                and not re.match(r"(?:Table|Tabela|Fig(?:ure|\.)|Figura)\s*\d", s, re.I)
                and (_claim_numbers(_CITATION_REF.sub(" ", s)) or (not in_body and comparative.search(s)))
                and s not in claims
            ):
                claims.append(s)
                if len(claims) >= AUTO_CLAIMS:
                    return claims
        if len(claims) >= 2:
            break  # the abstract states the paper's headline claims; body sentences only fill in when it has none
    return claims


def _validate_claims(claims):
    if claims is None:
        return []
    if isinstance(claims, str):
        claims = claims.split(";")
    if not isinstance(claims, (list, tuple)) or any(not isinstance(x, str) for x in claims):
        raise _errors.HTTPError(400, "claims must be a list of strings")
    claims = list(dict.fromkeys(x.strip() for x in claims if x.strip()))
    if len(claims) > MAX_CLAIMS:
        raise _errors.HTTPError(400, f"claims accepts at most {MAX_CLAIMS} items (got {len(claims)})")
    return claims


def _rank_passages(claim, passages):
    numbers = _claim_numbers(claim)
    values = [v for v in (_num_value(n) for n in numbers) if v is not None]
    kws = _keywords(claim)
    scored = []
    for i, p in enumerate(passages):
        matched = sum(1 for v in values if v in p["values"])
        lex = len(kws & p["keywords"]) / len(kws) if kws else 0.0
        key = (matched / len(values) if values else 0.0) * 2 + lex
        scored.append((key, -i, p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return numbers, kws, [p for key, _, p in scored if key > 0] or [p for _, _, p in scored[:1]]


def _passage_excerpt(p, paper_id, numbers=()):
    return _untrusted.excerpt(
        _clip_quote(p["quote"], numbers), paper_id=paper_id, section=p["label"], page=p["page"], limit=None
    )


def ground_claim(claim, passages, paper_id=None, claim_untrusted=False):
    """Locate one claim in the indexed passages -> GroundedClaim dict (lexical facts + candidate excerpts; the
    client's model judges whether they support the claim)."""
    numbers, kws, ranked = _rank_passages(claim, passages)
    if not ranked:
        return GroundedClaim(
            claim=_untrusted.neutralize(claim),
            claim_untrusted=claim_untrusted,
            numbers_checked=numbers,
            numbers_missing=numbers,
            citation_tag="[no analysable text]",
        ).model_dump(mode="json")
    best = ranked[0]
    found = [n for n in numbers if _num_value(n) is not None and _num_value(n) in best["values"]]
    shared = kws & best["keywords"]
    cues = sorted(
        {c.lower() for c in _EXTRAPOLATION_CUES.findall(claim)}
        - {c.lower() for c in _EXTRAPOLATION_CUES.findall(best["quote"])}
    )
    return GroundedClaim(
        claim=_untrusted.neutralize(claim),
        claim_untrusted=claim_untrusted,
        numbers_checked=numbers,
        numbers_found=found,
        numbers_missing=[n for n in numbers if n not in found],
        keyword_overlap=round(len(shared) / len(kws), 2) if kws else 0.0,
        hedging_cues_missing_in_text=cues,
        citation_tag=f"[p. {best['page']}, {best['label']}]" if best["page"] else f"[{best['label']}]",
        best_match=_passage_excerpt(best, paper_id, numbers),
        other_matches=[_passage_excerpt(p, paper_id, numbers) for p in ranked[1:3]],
    ).model_dump(mode="json")


_CLAIMS_RUBRIC = (
    ("literal_support", "Does the best-matching excerpt state the claim literally (same numbers, same comparison)?"),
    (
        "scope",
        "Does the claim generalise beyond what the excerpt supports (other datasets, settings, populations)?",
        "Look at hedging_cues_missing_in_text: words like 'always' or 'any' in the claim but not in the paper.",
    ),
    ("numbers", "Are the claim's numbers present and attached to the same metric/condition in the excerpt?"),
)


def verify_paper_claims(paper_id, claims=None):
    """Locate claims (given, or auto-extracted numeric/comparative sentences) in the paper's literal text and
    return the candidate passages for the client's model to judge."""
    from noul.schemas.evidence import AnalysisSubject
    from noul.schemas.technical import ClaimsGroundingReport

    claims = _validate_claims(claims)
    corpus = pdf_parser.get_paper_corpus(_common._paper_id(paper_id))
    auto = not claims
    passages = _passages(corpus)
    if auto:
        claims = _auto_claims(corpus, passages)
    pid = corpus["paper_id"]
    results = [ground_claim(cl, passages, paper_id=pid, claim_untrusted=auto) for cl in claims]
    # Alternative evidence: the paragraphs (BM25 over section/page chunks) most related to each claim, besides
    # the passage that contains `best_match`.
    from noul.library import retrieval as _retrieval

    chunks = _retrieval.chunk_corpus(corpus)
    for r in results:
        best = (r.get("best_match") or {}).get("text") or ""
        others = [
            _retrieval.to_excerpt(ch, pid, rel, limit=600).model_dump(mode="json")
            for rel, ch in _retrieval.rank(chunks, r["claim"])
            if not (best and best[:60] in ch["text"])
        ][:2]
        if others:
            r["other_matches"] = others
    limitations = _contract.corpus_limitations(corpus)
    if auto and not claims:
        limitations.insert(0, "No numeric or comparative claims found to check.")
    report = ClaimsGroundingReport(
        tool="verify_paper_claims",
        subject=AnalysisSubject(paper_id=pid, title=corpus["title"] or "", source=corpus["source"]),
        rubric=_contract.rubric("Claim grounding", _CLAIMS_RUBRIC),
        extraction_confidence=_contract.extraction_confidence(corpus["source"], corpus["sections"]),
        limitations=limitations,
        untrusted_fields=["subject.title", "claims[].citation_tag"],
        auto_extracted_claims=auto,
        claims=results,
    )
    extra = ["", "## Claims and where they appear"]
    for i, c in enumerate(report.claims, 1):
        extra += [
            "",
            f"### Claim {i}",
            _untrusted.wrap(c.claim, pid or "paper") if auto else f"> {_untrusted.neutralize(c.claim)}",
            f"- Location: {_untrusted.neutralize(c.citation_tag)}; numbers found: {', '.join(c.numbers_found) or '—'}; "
            f"missing: {', '.join(c.numbers_missing) or '—'}; keyword overlap: {c.keyword_overlap:.2f}"
            + (
                f"; claim words absent from the text: {', '.join(c.hedging_cues_missing_in_text)}"
                if c.hedging_cues_missing_in_text
                else ""
            ),
        ]
        if c.best_match:
            extra += ["", _untrusted.render(c.best_match)]
    report.rendered_markdown = _contract.render_markdown(report, "Claim grounding", extra)
    return report.model_dump(mode="json")
