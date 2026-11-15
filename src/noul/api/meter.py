"""The Evidence Meter: a deterministic tally of the stances the host model assigned to papers."""

from noul import errors as _errors
from noul import registry as _registry
from noul.api import common as _common
from noul.evidence import classify as _classify
from noul.evidence import integrity as _integrity
from noul.schemas import tool_inputs as _inputs


@_registry.tool(
    "evidence_meter",
    description=(
        "Compute the Evidence Meter deterministically from YOUR stance classification of each paper: raw "
        "counts and percentages plus study-design-weighted percentages (meta-analysis 1.0 … preclinical 0.2). "
        "Papers known to be retracted (from search_papers/get_paper records) are left out and listed in "
        "`excluded` with a warning; if every countable paper is retracted the tool answers with an error that lists "
        "them. Use its output instead of computing percentages yourself."
    ),
    input=_inputs.EvidenceMeterInput,
    network=False,
    profiles=("research",),
)
def evidence_meter(items):
    """Compute reproducible raw and design-weighted Evidence Meter (Noul Meter) percentages from LLM stance
    classifications. Each item: {"id": str, "stance": "supportive"|"inconclusive"|"contrary", "evidence"?: str}.
    The study-design weight comes from the cached paper record when available."""
    if not isinstance(items, list) or not items:
        raise _errors.HTTPError(400, "Provide at least one classified paper in `items`")
    raw_counts = {"supportive": 0, "inconclusive": 0, "contrary": 0}
    weighted = {"supportive": 0.0, "inconclusive": 0.0, "contrary": 0.0}
    breakdown = []
    skipped = []
    excluded = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            skipped.append({"item": it, "reason": "not an object"})
            continue
        pid = it.get("id")
        stance = _common.STANCE_ALIASES.get(str(it.get("stance") or "").lower().strip())
        if stance is None:
            skipped.append({"id": pid, "reason": f"unknown stance {it.get('stance')!r}"})
            continue
        if pid:
            if str(pid) in seen:
                skipped.append({"id": pid, "reason": "duplicate id"})
                continue
            seen.add(str(pid))
        p = _common._cached_paper(pid)
        known = _integrity.enrich(p, network=False) if p else None
        if known and _integrity.is_retracted(known):
            # A retracted paper is not evidence for or against anything: leave it out of the counts, say so.
            excluded.append(
                {
                    "id": pid,
                    "stance": stance,
                    "reason": "retracted: " + _integrity.describe(known),
                    "notice_doi": known["retraction"].get("notice_doi"),
                }
            )
            continue
        ev = (p or {}).get("evidence") or it.get("evidence") or "other"
        w = float(_classify.EV_W.get(ev, _classify.EV_W.get("other", 0.3)))
        raw_counts[stance] += 1
        weighted[stance] += w
        breakdown.append({"id": pid, "stance": stance, "evidence": ev, "weight": w})
    total_n = sum(raw_counts.values())
    if total_n == 0:
        if excluded:
            listed = "; ".join(f"{x['id']} ({x['reason']})" for x in excluded)
            others = "; ".join(f"{x.get('id', x.get('item'))} ({x['reason']})" for x in skipped)
            ignored = f" Also ignored: {others}." if skipped else ""
            raise _errors.HTTPError(
                400, f"Every countable paper is retracted, so there is nothing to count: {listed}.{ignored}"
            )
        raise _errors.HTTPError(400, "No valid stances provided (use supportive, inconclusive or contrary)")
    total_w = sum(weighted.values()) or 1.0
    warnings = (
        [
            f"{len(excluded)} retracted paper(s) excluded from the meter: "
            + ", ".join(str(x["id"]) for x in excluded)
            + ". Mention them only as retracted."
        ]
        if excluded
        else []
    )
    return {
        "total_papers": total_n,
        "raw_counts": raw_counts,
        "raw_percentages": {k: round(v * 100.0 / total_n, 1) for k, v in raw_counts.items()},
        "weighted_percentages": {k: round(v * 100.0 / total_w, 1) for k, v in weighted.items()},
        "weights": dict(_classify.EV_W),
        "breakdown": breakdown,
        "skipped": skipped,
        "excluded": excluded,
        "warnings": warnings,
        "untrusted_fields": ["excluded[].notice_doi"] if excluded else [],
    }
