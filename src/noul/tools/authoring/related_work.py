"""Related Work synthesizer: clusters papers into approach families and drafts a comparative section (`synthesize_related_work`)."""

import re

from noul import errors as _errors
from noul.schemas.authoring import (
    RelatedWorkSynthesis,
    RelatedWorkThemeCluster,
)
from noul.search import engine as _engine
from noul.tools.authoring import bibtex as _bibtex

_ARCH_FAMILY_RE = re.compile(
    r"\b(?:architectur\w+|transformer\w*|attention|backbone|representation|graph\s+neural|encoder|decoder|"
    r"token\s+routing|sparse|mixture[-\s]of[-\s]experts|moe|state[-\s]space|mamba|layer|embedding|mechanism)\b",
    re.I,
)


_TRAIN_FAMILY_RE = re.compile(
    r"\b(?:train\w+|optimiz\w+|fine[-\s]?tun\w+|rlhf|alignment|preference|distill\w+|causal|identification|"
    r"difference[-\s]in[-\s]differences|instrumental\s+variable|panel|randomized|rct|clinical\s+trial|"
    r"cohort|intervention|dosage|denoising|regulariz\w+)\b",
    re.I,
)


_EFF_FAMILY_RE = re.compile(
    r"\b(?:quantiz\w+|prun\w+|compress\w+|kv\s+cache|inference|latency|throughput|memory|vram|hardware|"
    r"scaling|benchmark\w*|evaluation|empirical|meta[-\s]analysis|systematic\s+review|survey)\b",
    re.I,
)


def _classify_approach_family(paper: dict) -> tuple[str, str]:
    title = paper.get("title") or ""
    body = f"{paper.get('abstract', '')} {paper.get('evidence', '')}"
    s_arch = len(_ARCH_FAMILY_RE.findall(title)) * 2 + len(_ARCH_FAMILY_RE.findall(body))
    s_train = len(_TRAIN_FAMILY_RE.findall(title)) * 2 + len(_TRAIN_FAMILY_RE.findall(body))
    s_eff = len(_EFF_FAMILY_RE.findall(title)) * 2 + len(_EFF_FAMILY_RE.findall(body))
    if s_arch >= s_train and s_arch >= s_eff and s_arch > 0:
        return ("Architectural & Representation Foundations", "architecture_and_representation")
    if s_train >= s_eff and s_train > 0:
        return ("Training, Optimization & Causal Identification", "training_and_identification")
    return ("Efficiency, Scaling & Empirical Evaluation", "efficiency_and_evaluation")


def _extract_core_contribution(paper: dict) -> str:
    abs_txt = (paper.get("abstract") or "").strip()
    if abs_txt:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abs_txt) if s.strip()]
        if sents:
            first = re.sub(
                r"^(?:We|In\s+this\s+(?:paper|work|study),?\s+we|This\s+(?:paper|study))\s+\w+\s+",
                "",
                sents[0],
                flags=re.I,
            )
            return first.rstrip(".")
    return (paper.get("title") or "a domain-specific methodology").rstrip(".")


def _extract_limitation_clause(paper: dict) -> str:
    abs_txt = (paper.get("abstract") or "").strip()
    for s in re.split(r"(?<=[.!?])\s+", abs_txt):
        if re.search(
            r"\b(?:however|limitation|struggles|degrades|overhead|constrained|trade[-\s]?off|cost|latency)\b", s, re.I
        ):
            cleaned = re.sub(
                r"^(?:However|Nevertheless|Nonetheless|A\s+limitation\s+is\s+that),?\s*", "", s.strip(), flags=re.I
            )
            return cleaned.rstrip(".")
    return "operates under specific structural or computational assumptions"


def synthesize_related_work(
    paper_ids,
    proposal: str,
    citation_style: str = "latex",
    output_format: str = "markdown",
) -> dict:
    """Issue #24: Group 1-20 papers into thematic approach families, write comparative academic prose
    (never isolated per-author lists), position the user's `proposal` against the collective gap,
    and output ready-to-paste Markdown or LaTeX (`\\section{Related Work}`) with BibTeX."""
    if isinstance(paper_ids, str):
        ids = [x.strip() for x in paper_ids.split(",") if x.strip()]
    elif isinstance(paper_ids, (list, tuple)):
        ids = [str(x).strip() for x in paper_ids if str(x).strip()]
    else:
        ids = []
    if not ids:
        raise _errors.HTTPError(400, "Provide at least one paper ID in `paper_ids`.")
    if not isinstance(proposal, str) or not proposal.strip():
        raise _errors.HTTPError(400, "Provide a non-empty `proposal` describing your work's contribution.")

    style = (citation_style or "latex").strip().lower()
    if style not in ("latex", "numeric"):
        raise _errors.HTTPError(400, f"Invalid citation_style '{citation_style}'. Use 'latex' or 'numeric'.")
    fmt = (output_format or "markdown").strip().lower()
    if fmt not in ("markdown", "latex"):
        raise _errors.HTTPError(400, f"Invalid output_format '{output_format}'. Use 'markdown' or 'latex'.")

    papers = []
    seen = set()
    for pid in ids[:20]:
        p = _engine.resolve_paper(pid)
        real_id = p.get("id") or pid
        if real_id not in seen:
            seen.add(real_id)
            papers.append(p)

    used_keys: set = set()
    key_map: dict[str, str] = {}
    num_map: dict[str, int] = {}
    bibtex_entries: list[str] = []

    for idx, p in enumerate(papers, 1):
        pid = p.get("id") or str(idx)
        k = _bibtex._make_bibtex_key(p, used_keys)
        key_map[pid] = k
        num_map[pid] = idx
        bibtex_entries.append(_bibtex._format_bibtex_entry_from_paper(p, k))

    def _cite_ref(p: dict) -> str:
        pid = p.get("id") or ""
        authors = p.get("authors") or ["Author"]
        surname = str(authors[0]).split(",")[0].split()[-1] if authors else "Author"
        if style == "latex":
            return f"{surname} et al. \\cite{{{key_map[pid]}}}"
        return f"{surname} et al. [{num_map[pid]}]"

    family_order = [
        ("Architectural & Representation Foundations", "architecture_and_representation"),
        ("Training, Optimization & Causal Identification", "training_and_identification"),
        ("Efficiency, Scaling & Empirical Evaluation", "efficiency_and_evaluation"),
    ]
    grouped: dict[str, list[dict]] = {name: [] for name, _ in family_order}
    for p in papers:
        t_name, _ = _classify_approach_family(p)
        grouped[t_name].append(p)

    clusters: list[RelatedWorkThemeCluster] = []
    for theme_name, family_slug in family_order:
        group = grouped[theme_name]
        if not group:
            continue
        p_ids = [p.get("id") or "" for p in group]
        c_keys = [key_map[pid] for pid in p_ids]

        if len(group) == 1:
            p0 = group[0]
            narrative = (
                f"Within the scope of {theme_name.lower()}, {_cite_ref(p0)} investigate "
                f"{_extract_core_contribution(p0)}, establishing an important reference point while noting that the "
                f"approach {_extract_limitation_clause(p0)}."
            )
        elif len(group) == 2:
            p0, p1 = group[0], group[1]
            narrative = (
                f"Research on {theme_name.lower()} has evolved along complementary trajectories: while "
                f"{_cite_ref(p0)} focus on {_extract_core_contribution(p0)}, {_cite_ref(p1)} extend this line of "
                f"inquiry by addressing {_extract_core_contribution(p1)}; however, whereas {_cite_ref(p0)} "
                f"{_extract_limitation_clause(p0)}, {_cite_ref(p1)} highlight that their formulation "
                f"{_extract_limitation_clause(p1)}."
            )
        else:
            p0, p1 = group[0], group[1]
            rest = group[2:]
            rest_refs = ", ".join(_cite_ref(r) for r in rest)
            rest_contribs = "; ".join(_extract_core_contribution(r) for r in rest)
            narrative = (
                f"Prior work in {theme_name.lower()} reveals a clear methodological tension: while "
                f"{_cite_ref(p0)} pioneer {_extract_core_contribution(p0)}, {_cite_ref(p1)} contrast this paradigm "
                f"by introducing {_extract_core_contribution(p1)}. Building on these foundations, {rest_refs} "
                f"further explore {rest_contribs}, yet comparative analysis shows that {_cite_ref(p0)} "
                f"{_extract_limitation_clause(p0)} whereas subsequent variants remain sensitive to "
                f"{_extract_limitation_clause(rest[-1])}."
            )

        clusters.append(
            RelatedWorkThemeCluster(
                theme_name=theme_name,
                approach_family=family_slug,
                paper_ids=p_ids,
                citation_keys=c_keys,
                comparative_narrative=narrative,
            )
        )

    all_cites = (
        f"\\cite{{{', '.join(key_map[p.get('id') or ''] for p in papers)}}}"
        if style == "latex"
        else "[" + ", ".join(str(num_map[p.get("id") or ""]) for p in papers) + "]"
    )
    lims_summary = "; ".join(_extract_limitation_clause(p) for p in papers[:2])
    positioning = (
        f"Despite these substantial advances across {', '.join(c.theme_name.lower() for c in clusters)} {all_cites}, "
        f"existing methods remain constrained by key trade-offs ({lims_summary}). "
        f"In contrast, our work ({proposal.strip().rstrip('.')}) directly bridges this gap by unifying "
        f"the strengths of prior formulations while eliminating their primary operational bottlenecks."
    )

    if fmt == "latex":
        lines = ["\\section{Related Work}", ""]
        for cl in clusters:
            lines.append(f"\\paragraph{{{cl.theme_name}.}} {cl.comparative_narrative}")
            lines.append("")
        lines.append(f"\\paragraph{{Positioning of Our Contribution.}} {positioning}")
        rendered = "\n".join(lines)
    else:
        lines = ["## Related Work", ""]
        for cl in clusters:
            lines.append(f"### {cl.theme_name}")
            lines.append(cl.comparative_narrative)
            lines.append("")
        lines.append("### Positioning of Our Contribution")
        lines.append(positioning)
        rendered = "\n".join(lines)

    res = RelatedWorkSynthesis(
        proposal_summary=proposal.strip(),
        citation_style=style,
        output_format=fmt,
        thematic_clusters=clusters,
        positioning_paragraph=positioning,
        rendered_related_work=rendered,
        bibtex_block="\n\n".join(bibtex_entries),
    )
    return res.model_dump()
