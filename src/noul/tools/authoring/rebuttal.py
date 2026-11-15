"""Rebuttal copilot: reviewer comments segmented, classified and answered with templates to complete (`parse_and_draft_rebuttal`)."""

import re

from noul import errors as _errors
from noul.evidence import contract as _contract
from noul.evidence import untrusted as _untrusted
from noul.schemas.authoring import (
    RebuttalPoint,
    RebuttalReport,
)
from noul.schemas.evidence import AnalysisSubject
from noul.tools.authoring import common as _common

_REVIEWER_HEADER_RE = re.compile(
    r"^[ \t#*>-]*(?P<rev>Reviewer\s*#?\s*(?:[0-9]+|[A-Z])\b|R[1-9]\b|Meta[-\s]?Reviewer\b|Area\s+Chair\b)"
    r"[^\n:]*:?[ \t]*$",
    re.I | re.M,
)


_NEW_EXP_RE = re.compile(
    r"\b(?:run\s+additional|additional\s+experiments?|new\s+experiments?|more\s+experiments?|ablation|"
    r"evaluate\s+on|test\s+on\s+additional|more\s+seeds|statistical\s+significance|p[-\s]?values?|"
    r"confidence\s+intervals?|error\s+bars?|measure\s+latency|wall[-\s]clock|sensitivity\s+analysis)\b",
    re.I,
)


_BASELINE_CRIT_RE = re.compile(
    r"\b(?:compare\s+(?:against|with|to)|missing\s+baselines?|recent\s+baselines?|prior\s+work\s+by|"
    r"sota\s+baselines?|stronger\s+baselines?|outperformed\s+by|comparison\s+with)\b",
    re.I,
)


_NOTATION_FMT_RE = re.compile(
    r"\b(?:typos?|notation|equations?\s+\d*|figures?\s+\d*|tables?\s+\d*|captions?|define\s+symbol|"
    r"formatting|font\s+size|missing\s+reference|broken\s+ref)\b",
    re.I,
)


_METHOD_STAT_RE = re.compile(
    r"\b(?:assumptions?|data\s+leakage|confounding|endogeneity|selection\s+bias|overfitting|"
    r"identifi(?:cation|ability)|theoretical\s+guarantee|convergence|generalization\s+bound)\b",
    re.I,
)


_CLARITY_RE = re.compile(
    r"\b(?:unclear|clarify|how\s+does|what\s+is\s+meant|intuition|confusing|explain\s+why|elaborate)\b",
    re.I,
)


def _classify_rebuttal_comment(comment: str) -> tuple[str, str, bool]:
    low = comment or ""
    if _NEW_EXP_RE.search(low):
        return "new_experiment_request", "high", True
    if _BASELINE_CRIT_RE.search(low):
        req_exp = bool(re.search(r"\b(?:run|evaluate|compare\s+empirically|table)\b", low, re.I))
        return "baseline_criticism", "high", req_exp
    if _METHOD_STAT_RE.search(low):
        return "methodological_or_statistical", "high", False
    if _NOTATION_FMT_RE.search(low) and not _CLARITY_RE.search(low):
        return "notation_or_formatting", "low", False
    return "clarity_question", "medium", False


def _segment_reviewer_blocks(raw_text: str) -> list[tuple[str, list[str]]]:
    matches = list(_REVIEWER_HEADER_RE.finditer(raw_text or ""))
    blocks: list[tuple[str, str]] = []
    if not matches:
        blocks.append(("Reviewer 1", (raw_text or "").strip()))
    else:
        for i, m in enumerate(matches):
            rev_raw = re.sub(r"\s+", " ", m.group("rev").strip())
            if re.match(r"^R(\d+)$", rev_raw, re.I):
                rev_id = f"Reviewer {rev_raw[1:]}"
            else:
                rev_id = rev_raw.title().replace("#", "").strip()
                rev_id = re.sub(r"\s+", " ", rev_id)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
            blocks.append((rev_id, raw_text[start:end].strip()))

    out: list[tuple[str, list[str]]] = []
    for rev_id, body in blocks:
        points: list[str] = []
        for raw_line in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.)]|\[?[WQ]\d+\]?)\s+)", body):
            cleaned = re.sub(
                r"^\s*(?:Weaknesses|Questions|Comments|Major|Minor)\s*:?\s*", "", raw_line.strip(), flags=re.I
            )
            cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)]|\[?[WQ]\d+\]?:?)\s*", "", cleaned).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if len(cleaned) >= 15:
                points.append(cleaned)
        if not points and body.strip():
            points.append(re.sub(r"\s+", " ", body.strip()))
        if points:
            out.append((rev_id, points))
    return out


_REBUTTAL_TEMPLATES = {
    "new_experiment_request": (
        "We thank the reviewer for suggesting this experiment. [TODO: say whether you ran it; if so, the setup, "
        "the number of runs/seeds and the result, including any that did not favour your method.]",
        "[TODO: where the new results appear, e.g. 'Table X / Appendix Y'.]",
    ),
    "baseline_criticism": (
        "We thank the reviewer for pointing to this baseline. [TODO: whether you added the comparison, the result, "
        "and how it changes or qualifies your claims.]",
        "[TODO: the table/section updated with the baseline.]",
    ),
    "methodological_or_statistical": (
        "We thank the reviewer for this methodological point. [TODO: the assumption or analysis in question, your "
        "justification, and any sensitivity/robustness check you added.]",
        "[TODO: the methodology/limitations section updated.]",
    ),
    "notation_or_formatting": (
        "We thank the reviewer for catching this. [TODO: the notation/caption fix.]",
        "[TODO: the sections where notation or captions changed.]",
    ),
    "clarity_question": (
        "We thank the reviewer for the question. [TODO: a direct answer, pointing to the evidence in the paper.]",
        "[TODO: the paragraph or figure added/clarified, if any.]",
    ),
}


def parse_and_draft_rebuttal(
    reviews_input: str,
    paper_id: str | None = None,
    draft_input: str | None = None,
    output_format: str = "markdown",
) -> dict:
    """Issue #26: Parse raw peer-review comments by reviewer and objection, classify each point,
    flag items requiring new experiments, and build a rebuttal skeleton (Markdown and LaTeX) whose responses are
    templates with [TODO] placeholders - the author supplies the actual results."""
    t = _common._tools()
    reviews_text, _, _ = _common._load_text_or_workspace_file(reviews_input)
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    context: list = []
    if paper_id and str(paper_id).strip():
        try:
            p = t._resolve(str(paper_id).strip())
            if p.get("abstract"):
                context.append(_untrusted.excerpt(p["abstract"], paper_id=p.get("id"), section="Abstract"))
        except Exception:
            pass
    if draft_input and str(draft_input).strip():
        try:
            d_txt, d_label, _ = _common._load_text_or_workspace_file(str(draft_input).strip())
            context.append(_untrusted.excerpt(d_txt, origin="draft", section=d_label, limit=1200))
        except Exception:
            pass

    segmented = _segment_reviewer_blocks(reviews_text)
    reviewers_detected = [rev for rev, _ in segmented]
    points: list[RebuttalPoint] = []
    for rev_idx, (rev_id, comments) in enumerate(segmented, 1):
        for pt_idx, comment in enumerate(comments, 1):
            cat, sev, req_exp = _classify_rebuttal_comment(comment)
            response, action = _REBUTTAL_TEMPLATES.get(cat, _REBUTTAL_TEMPLATES["clarity_question"])
            points.append(
                RebuttalPoint(
                    point_id=f"R{rev_idx}.{pt_idx}",
                    reviewer_id=_untrusted.neutralize(rev_id),
                    category=cat,
                    priority_hint=_contract.priority(sev),
                    requires_new_experiment=req_exp,
                    comment=_untrusted.excerpt(comment, origin="reviews", limit=2000),
                    response_template=response,
                    manuscript_action_template=action,
                )
            )

    new_exp_count = sum(1 for pt in points if pt.requires_new_experiment)
    md_lines = [
        "# Author rebuttal skeleton",
        "",
        "> Templates only: replace every [TODO] with what you actually did and found. Noul does not know your "
        "results; never send a response that claims experiments you have not run.",
        "",
        f"- **Reviewers:** {', '.join(reviewers_detected)}",
        f"- **Points:** {len(points)} ({new_exp_count} asking for new experiments)",
    ]
    tex_lines = ["% Author rebuttal skeleton (Noul): replace every [TODO]", "\\section*{Author Response to Reviewers}"]
    current_rev = None
    for pt in points:
        if pt.reviewer_id != current_rev:
            current_rev = pt.reviewer_id
            md_lines += ["", f"## Response to {current_rev}"]
            tex_lines += ["", f"\\subsection*{{Response to {current_rev}}}"]
        badge = " — asks for a new experiment" if pt.requires_new_experiment else ""
        md_lines += [
            "",
            f"### [{pt.point_id}] {pt.category} (priority hint: {pt.priority_hint}){badge}",
            _untrusted.render(pt.comment),
            "",
            f"**Response:** {pt.response_template}",
            "",
            f"**Change in the manuscript:** {pt.manuscript_action_template}",
        ]
        tex_lines += [
            f"\\paragraph{{[{pt.point_id}] {pt.category}}} \\textit{{(reviewer comment {pt.point_id})}}",
            f"\\noindent\\textbf{{Response:}} {pt.response_template}\\\\",
            f"\\noindent\\textbf{{Change:}} {pt.manuscript_action_template}",
        ]

    report = RebuttalReport(
        tool="parse_and_draft_rebuttal",
        subject=AnalysisSubject(paper_id=str(paper_id).strip() if paper_id else None, source="reviews"),
        excerpts=context,
        rubric=_contract.rubric(
            "Rebuttal planning",
            (
                (
                    "valid",
                    "Which reviewer points are correct, which are misunderstandings, and which are out of scope?",
                ),
                ("evidence", "For each point, what evidence (existing results, new analysis) answers it?"),
                ("experiments", "Which requested experiments are feasible before the deadline?"),
                ("tone", "Is each response specific, polite and free of claims you cannot back up?"),
            ),
            "The categories and priorities are pattern-based guesses and the responses are templates. Help the "
            "author decide each point and fill the [TODO]s with their real results; never invent results.",
        ),
        extraction_confidence=_contract.extraction_confidence("reviews", length=len(reviews_text)) if points else "low",
        limitations=["Comment segmentation and classification are pattern-based; check the grouping per reviewer."],
        reviewers_detected=reviewers_detected,
        total_points=len(points),
        new_experiments_requested=new_exp_count,
        points=points,
        markdown_rebuttal="\n".join(md_lines).strip(),
        latex_rebuttal="\n".join(tex_lines).strip(),
    )
    report.rendered_markdown = report.markdown_rebuttal if fmt == "markdown" else report.latex_rebuttal
    return report.model_dump(mode="json")
