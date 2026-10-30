"""Deterministic paper-corpus utilities (Phase 3): resolve a paper, pick its best available text (cached
open-access full text, else title + abstract) and segment it into pages, table captions and sections.

Stdlib only; `noul_core` is imported lazily so importing this module has no side effects."""
import re

PAGE_CHARS = 3000

_PAGE_MARKER = re.compile(r"^[ \t]*(?:\[\s*Page\s+(\d+)\s*\]|-{2,}\s*Page\s+(\d+)\s*-{2,})[ \t]*$", re.I | re.M)
_CAPTION = re.compile(
    r"^[ \t>*_#]*(Table|Tabela|Tab\.)\s+(\d+|[IVX]+)\s*[:.\-—–]?\s*(.*)$", re.I | re.M)
_LATEX_CAPTION = re.compile(r"\\caption\s*\{")
_SECTION = re.compile(
    r"^[ \t]*(?:#{1,4}[ \t]+(?P<md>[^\n]{2,80})|\\(?:sub)*section\*?\{(?P<tex>[^}\n]{2,80})\}|"
    r"(?P<num>\d{1,2}(?:\.\d{1,2}){0,2}\.?[ \t]+[A-Z][A-Za-z][^\n]{0,60})|"
    r"(?P<named>Abstract|Introduction|Related Work|Background|Methods?|Methodology|Experiments?|"
    r"Experimental Setup|Results?|Evaluation|Ablation(?: Study| Studies)?|Discussion|Limitations|"
    r"Error Analysis|Failure Cases|Threats to Validity|Broader Impacts?|Conclusions?|"
    r"References|Appendix(?: [A-Z])?)[ \t]*:?)[ \t]*$", re.M)


def _core():
    import noul_core

    return noul_core


def balanced(text, start, open_ch="{", close_ch="}"):
    """Return (content, end_index) of the brace group whose opening brace is at `start` (or None)."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "\\":
            continue
        if ch == open_ch and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif ch == close_ch and text[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return None


def split_pages(text, page_chars=PAGE_CHARS):
    """Split text into pages: form feeds, `[Page N]` / `--- Page N ---` markers, or ~`page_chars` blocks.

    Returns [{"page": n, "start": offset, "end": offset, "text": str}] with offsets into `text`."""
    text = text or ""
    pages = []
    if "\f" in text:
        pos = 0
        for n, chunk in enumerate(text.split("\f"), 1):
            pages.append({"page": n, "start": pos, "end": pos + len(chunk), "text": chunk})
            pos += len(chunk) + 1
    elif _PAGE_MARKER.search(text):
        markers = list(_PAGE_MARKER.finditer(text))
        first = markers[0]
        if text[:first.start()].strip():
            num = int(first.group(1) or first.group(2))
            pages.append({"page": max(1, num - 1), "start": 0, "end": first.start(), "text": text[:first.start()]})
        for i, m in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            pages.append({"page": int(m.group(1) or m.group(2)), "start": m.end(), "end": end,
                          "text": text[m.end():end]})
    else:
        pos, n = 0, 1
        while pos < len(text):
            end = min(len(text), pos + page_chars)
            if end < len(text):
                cut = text.rfind("\n", pos + page_chars // 2, end)
                end = cut + 1 if cut > pos else end
            pages.append({"page": n, "start": pos, "end": end, "text": text[pos:end]})
            pos, n = end, n + 1
    return pages or [{"page": 1, "start": 0, "end": 0, "text": ""}]


def page_at(pages, offset):
    """Page number containing character `offset`."""
    for pg in pages:
        if pg["start"] <= offset < max(pg["end"], pg["start"] + 1):
            return pg["page"]
    return pages[-1]["page"] if pages else 1


def find_table_captions(text):
    """Table captions (`Table 1: ...`, `Tabela 2. ...`, `\\caption{...}`) with offsets, in document order."""
    out = []
    for m in _CAPTION.finditer(text or ""):
        out.append({"label": f"Table {m.group(2)}", "number": m.group(2), "caption": m.group(3).strip(" *_"),
                    "offset": m.start(), "end": m.end(), "kind": "text"})
    for m in _LATEX_CAPTION.finditer(text or ""):
        grp = balanced(text, m.end() - 1)
        if grp:
            out.append({"label": None, "number": None, "caption": re.sub(r"\s+", " ", grp[0]).strip(),
                        "offset": m.start(), "end": grp[1], "kind": "latex"})
    return sorted(out, key=lambda c: c["offset"])


def find_sections(text):
    """Section headings (Markdown, LaTeX, numbered `3.2 Results` or well-known names) with offsets."""
    out = []
    for m in _SECTION.finditer(text or ""):
        if m.group("num") and len(re.findall(r"\d+(?:\.\d+)?", m.group("num"))) > 2:
            continue  # a numeric table row, not a numbered heading
        title = next(g for g in (m.group("md"), m.group("tex"), m.group("num"), m.group("named")) if g)
        out.append({"title": re.sub(r"\s+", " ", title).strip(" #:"), "offset": m.start()})
    return out


def section_at(sections, offset):
    title = None
    for s in sections:
        if s["offset"] <= offset:
            title = s["title"]
        else:
            break
    return title


def build_corpus(text, paper=None, source="text"):
    """Segment raw `text` into the corpus structure used by the technical tools (no I/O)."""
    paper = paper or {}
    return {
        "paper": paper,
        "paper_id": paper.get("id"),
        "title": paper.get("title"),
        "abstract": paper.get("abstract") or "",
        "text": text,
        "source": source,
        "pages": split_pages(text),
        "captions": find_table_captions(text),
        "sections": find_sections(text),
        "warnings": [],
    }


def get_paper_corpus(paper_id):
    """Resolve `paper_id` and return its segmented corpus.

    Uses the cached full text (`ft:<id>`), fetching it via `noul_core.fulltext` for open-access papers; falls back
    to title + abstract (with a warning) when no full text can be obtained."""
    core = _core()
    p = core.resolve_paper(paper_id)
    pid = p["id"]
    warnings = []
    text = (core.cache_get("ft:" + pid) or {}).get("text") or ""
    if not text and p.get("open_access"):
        try:
            if core.fulltext(pid).get("available"):
                text = (core.cache_get("ft:" + pid) or {}).get("text") or ""
        except Exception as e:
            warnings.append(f"Full text fetch failed ({type(e).__name__}).")
    source = "fulltext"
    if not text:
        source = "abstract"
        text = "\n\n".join(x for x in (p.get("title") or "", p.get("abstract") or "") if x)
        warnings.append("Full text not available; analysed title/abstract only.")
    corpus = build_corpus(text, paper=p, source=source)
    corpus["warnings"] = warnings
    return corpus
