"""Passage retrieval over a paper's text: section- and page-aware chunks ranked with BM25 (pure Python).

Chunks never cross a section heading or a page break, so every excerpt can be cited as "p. N, Section X". The
index is built per call (papers are small: a 15-page PDF gives ~60 chunks); nothing is persisted."""

from __future__ import annotations

import math
import re
from collections import Counter

from noul.evidence import untrusted as _untrusted
from noul.net import cache as _cache
from noul.providers import pdf_parser
from noul.schemas.evidence import Excerpt

K1 = 1.2
B = 0.75
SECTION_WEIGHT = 2  # a section title's terms count this many times in each of its chunks
CHUNK_CHARS = 900

_WORD = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being below between
    both but by can could did do does doing down during each few for from further had has have having he her here hers
    him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our ours
    out over own same she should so some such than that the their theirs them then there these they this those through
    to too under until up very was we were what when where which while who whom why will with would you your yours
    o os as um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre entre que se e ou mas como
    mais menos muito muita muitos ser estar foi sao são ter tem há ao aos à às pelo pela pelos pelas este esta isto esse
    essa isso aquele aquela qual quais quando onde porque já não sim seu sua seus suas nosso nossa""".split()
)


def tokens(text: str) -> list[str]:
    """Lowercased word tokens without stopwords, with a light plural fold ("heads" -> "head")."""
    out = []
    for w in _WORD.findall((text or "").lower()):
        if w in STOPWORDS or len(w) < 2 and not w.isdigit():
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def chunk_corpus(corpus: dict, max_chars: int = CHUNK_CHARS) -> list[dict]:
    """Split `corpus["text"]` into passages of ~`max_chars`, closing at a sentence end. A passage never spans a
    section heading or a real page break, and a long paragraph is split at sentence boundaries. Each chunk:
    {id, start, end, text, section, page}; `id` is "<start>-<end>" (character offsets into the cached text,
    stable for a given text layout - see fulltext.PDF_TEXT_FORMAT)."""
    text = corpus.get("text") or ""
    pages, sections = corpus.get("pages") or [], corpus.get("sections") or []
    real_pages = [p for p in pages if not p.get("synthetic")]
    cuts = sorted({s["offset"] for s in sections} | {p["start"] for p in real_pages} | {0})
    chunks: list[dict] = []

    def flush(start, end):
        body = text[start:end].strip()
        if len(body) >= 20:
            lead = len(text[start:end]) - len(text[start:end].lstrip())
            begin = start + lead
            stop = begin + len(body)
            chunks.append(
                {
                    "id": f"{begin}-{stop}",
                    "start": begin,
                    "end": stop,
                    "text": re.sub(r"\s+", " ", body),
                    "section": pdf_parser.section_at(sections, begin) or None,
                    "page": pdf_parser.page_at(pages, begin) if pages else None,
                }
            )

    for i, seg_start in enumerate(cuts):
        seg_end = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        start = seg_start
        for piece in _pieces(text, seg_start, seg_end, max_chars):
            if piece[1] - start >= max_chars and _SENTENCE_END.search(text[piece[0] : piece[1]]):
                flush(start, piece[1])
                start = piece[1]
        flush(start, seg_end)
    return chunks


_SENTENCE_END = re.compile(r"[.!?:]\s*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[0-9])")


def _pieces(text, seg_start, seg_end, max_chars):
    """(start, end) offsets of the lines of a segment; lines longer than `max_chars` (one-line paragraphs, e.g.
    from JATS XML) are split into sentences so a passage can close inside them."""
    pos = seg_start
    for line in text[seg_start:seg_end].split("\n"):
        line_end = pos + len(line)
        if len(line) > max_chars:
            cur = pos
            for m in _SENTENCE_SPLIT.finditer(line):
                yield cur, pos + m.start()
                cur = pos + m.end()
            yield cur, line_end
        else:
            yield pos, line_end
        pos = line_end + 1


def rank(chunks: list[dict], question: str) -> list[tuple[float, dict]]:
    """BM25 scores of `chunks` for `question`, best first (ties keep document order); zero-score chunks dropped."""
    query = Counter(tokens(question))
    if not chunks or not query:
        return []
    docs = []
    for ch in chunks:
        terms = tokens(ch["text"])
        if ch.get("section"):
            terms += tokens(ch["section"]) * SECTION_WEIGHT
        docs.append(Counter(terms))
    lengths = [sum(d.values()) for d in docs]
    avg = sum(lengths) / len(lengths) or 1.0
    n = len(docs)
    df = Counter(t for d in docs for t in d)
    scored = []
    for idx, (doc, length) in enumerate(zip(docs, lengths, strict=True)):
        score = 0.0
        for term in query:
            tf = doc.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg))
        if score > 0:
            scored.append((score, -idx, chunks[idx]))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [(round(s, 4), ch) for s, _, ch in scored]


def to_excerpt(chunk: dict, paper_id: str | None, relevance: float | None = None, limit: int = 1200) -> Excerpt:
    ex = _untrusted.excerpt(
        chunk["text"], paper_id=paper_id, section=chunk.get("section"), page=chunk.get("page"), limit=limit
    )
    ex.chunk_id, ex.char_start, ex.char_end = chunk["id"], chunk["start"], chunk["end"]
    ex.relevance = relevance
    return ex


def search(corpus: dict, question: str, n: int = 6, paper_id: str | None = None) -> list[Excerpt]:
    """The `n` passages that best answer `question` (BM25), as untrusted `Excerpt`s with section and page. Without
    a question (or when no term matches), the first passages in document order."""
    chunks = chunk_corpus(corpus)
    ranked = rank(chunks, question) if question else []
    if ranked:
        return [to_excerpt(ch, paper_id, score) for score, ch in ranked[:n]]
    return [to_excerpt(ch, paper_id) for ch in chunks[:n]]


def corpus_for(text: str, source: str = "fulltext") -> dict:
    """A minimal corpus (pages + sections) for raw `text`."""
    return pdf_parser.build_corpus(text or "", source=source)


def cached_corpus(paper_id: str) -> dict:
    """The cached full text of `paper_id` (after `fulltext.fulltext()` ran) segmented into pages and sections, plus
    the cache layout version (`format`), so stored passage ids can be checked against the text they point into."""
    entry = _cache.cache_get("ft:" + paper_id) or {}
    corpus = corpus_for(entry.get("text") or "")
    corpus["format"] = entry.get("format")
    return corpus
