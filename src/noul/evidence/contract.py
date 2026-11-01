"""Helpers for building `AnalysisResult`s (the evidence-vs-judgement contract) and rendering them as Markdown."""

from __future__ import annotations

from collections.abc import Iterable

from noul.evidence import untrusted as _untrusted
from noul.schemas.evidence import (
    AnalysisResult,
    ChecklistItem,
    Confidence,
    PriorityHint,
    Rubric,
    RubricCriterion,
    Signal,
)

JUDGE_WITH_RUBRIC = (
    "These are heuristic findings, not a verdict. Read the excerpts, decide yourself whether each signal really "
    "holds, answer the rubric criteria citing the excerpts (page/section), and say when the text available is "
    "not enough to decide."
)
_KIND_TITLES = (
    ("strength", "Strengths detected"),
    ("concern", "Concerns detected"),
    ("gap", "Gaps (not found in the text)"),
    ("neutral", "Other observations"),
)


def priority(value: str | None) -> PriorityHint:
    """A heuristic severity label (critical/high/medium/low) as a priority hint."""
    v = (value or "").lower()
    if v in ("critical", "high"):
        return "high"
    return "low" if v == "low" else "medium"


def extraction_confidence(source: str, sections: Iterable | None = None, length: int | None = None) -> Confidence:
    """How much text the heuristics saw. Papers: full text with >= 3 recognisable sections -> high, other full
    text with 1-2 sections -> medium, no recognisable structure or abstract only -> low. The user's draft or reviews: medium, or low under 2,000 characters."""
    if source == "fulltext":
        n = len(list(sections or []))
        return "high" if n >= 3 else "medium" if n else "low"
    if source in ("draft", "reviews"):
        return "low" if length is not None and length < 2000 else "medium"
    return "low"


def corpus_limitations(corpus: dict) -> list[str]:
    """Standard caveats for a paper corpus (`pdf_parser.get_paper_corpus` / `build_corpus`)."""
    notes = list(corpus.get("warnings") or [])
    if corpus.get("source") != "fulltext":
        notes.append("Only the title/abstract was analysed: absence of a finding is weak evidence.")
    elif not corpus.get("sections"):
        notes.append(
            "No section structure was recognised in the full text: findings that depend on a section "
            "(e.g. 'no Limitations section') are unreliable."
        )
    notes.append("Pattern-based extraction: signals can be false positives or miss paraphrased content.")
    return list(dict.fromkeys(notes))


def rubric(name: str, criteria: Iterable[tuple[str, ...]], how_to_judge: str = "") -> Rubric:
    """A rubric from `(id, question)` or `(id, question, guidance)` tuples."""
    items = [RubricCriterion(id=c[0], question=c[1], guidance=c[2] if len(c) > 2 else "") for c in criteria]
    return Rubric(name=name, criteria=items, how_to_judge=how_to_judge or JUDGE_WITH_RUBRIC)


def _signal_md(s: Signal) -> list[str]:
    head = f"- **[{s.id}] {s.description}** _(category: {s.category}; priority hint: {s.priority_hint})_"
    out = [head]
    if s.detail:
        out.append(f"  - {s.detail}")
    for ex in s.excerpts:
        out += ["", _untrusted.render(ex), ""]
    return out


def _check_md(c: ChecklistItem) -> list[str]:
    mark = {"yes": "yes", "no": "no", "unclear": "unclear"}[c.detected]
    line = f"- [{mark}] {c.question}" + (f" — `{_untrusted.neutralize(c.location)}`" if c.location else "")
    out = [line]
    if c.remediation and c.detected != "yes":
        out.append(f"  - Suggested action: {c.remediation}")
    for ex in c.evidence:
        out += ["", _untrusted.render(ex), ""]
    return out


def render_markdown(result: AnalysisResult, title: str, extra: Iterable[str] = ()) -> str:
    """Markdown view of an analytic result: findings grouped by kind, checklist, excerpts and the rubric."""
    subj = result.subject
    lines = [f"# {title}", ""]
    if subj.title and "subject.title" in result.untrusted_fields:
        lines += ["- **Subject:**", _untrusted.wrap(subj.title, subj.paper_id or "paper"), ""]
    elif subj.title or subj.paper_id:
        lines.append(f"- **Subject:** {_untrusted.neutralize(subj.title or subj.paper_id)}")
    if subj.domain:
        lines.append(f"- **Domain:** `{subj.domain}`")
    lines += [
        f"- **Method:** heuristic pattern detection over `{subj.source or 'text'}`; "
        f"extraction confidence: `{result.extraction_confidence}`",
        "",
        f"> {result.rubric.how_to_judge}",
    ]
    for kind, heading in _KIND_TITLES:
        group = [s for s in result.signals if s.kind == kind]
        if group:
            lines += ["", f"## {heading}"]
            for s in group:
                lines += _signal_md(s)
    if result.checklist:
        lines += ["", "## Checklist (detected in the text?)"]
        for item in result.checklist:
            lines += _check_md(item)
    if result.excerpts:
        lines += ["", "## Excerpts"]
        for ex in result.excerpts:
            lines += ["", _untrusted.render(ex)]
    lines += list(extra)
    lines += ["", f"## Rubric: {result.rubric.name}"]
    for crit in result.rubric.criteria:
        lines.append(f"- **{crit.id}.** {crit.question}" + (f" _{crit.guidance}_" if crit.guidance else ""))
    if result.limitations:
        lines += ["", "## Limitations of this analysis"]
        lines += [f"- {n}" for n in result.limitations]
    return "\n".join(lines).strip() + "\n"
