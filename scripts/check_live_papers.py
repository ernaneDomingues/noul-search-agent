#!/usr/bin/env python3
"""Manual check of the evidence heuristics on real papers (network; not run in CI).

Downloads each paper's open-access full text into a throw-away Noul home and prints what the analytic tools find:
ablation, code links, training/decoding hyperparameters, result tables, candidate failure modes taken from captions
(should be none), and where key claims are located. The golden set (tests/golden/evidence_signals.jsonl) pins the
same signals offline on synthetic papers; this script is the reality check against the PDFs they imitate.

Usage: python scripts/check_live_papers.py [--paper ID ...] [--keep-cache]
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PAPERS = {
    # "Attention Is All You Need": variations table (A)-(E), wrapped captions, URL split across lines, and the
    # abstract (41.8 BLEU EN-FR) vs. section 6.1 (41.0) inconsistency found in the Phase 3a plugin test.
    "10.48550/arXiv.1706.03762": {
        "ablation": True,
        "code": True,
        "training": ["optimizer", "beta1", "beta2", "warmup_steps", "dropout", "label_smoothing"],
        "min_tables": 3,
        "claims": ["The big model achieves 41.8 BLEU on English-to-French", "beta2 = 0.98"],
    },
    # BERT: ablation section and many result tables.
    "10.48550/arXiv.1810.04805": {
        "ablation": True,
        "code": True,
        "training": ["optimizer", "learning_rate", "batch_size", "dropout"],
        "min_tables": 4,
        "claims": ["BERT obtains 80.5% on GLUE"],
    },
}


def check(pid, expect):
    from noul import registry

    paper = registry.call("get_paper", {"id": pid})
    ft = registry.call("get_fulltext", {"id": paper["id"]})
    out = {"title": paper.get("title"), "fulltext": ft.get("available"), "source": ft.get("source")}
    abl = registry.call("analyze_ablation_tables", {"paper_id": paper["id"]})
    art = registry.call("scan_paper_artifacts", {"id": paper["id"], "verify_http": False, "inspect_fulltext": True})
    tables = registry.call("extract_benchmark_tables", {"paper_id": paper["id"]}).get("tables") or []
    hp = registry.call("extract_prompts_and_decoding", {"paper_id": paper["id"]})
    fm = registry.call("detect_failure_modes", {"paper_id": paper["id"]})
    captions = [
        e["text"][:80]
        for s in fm.get("signals") or []
        for e in s.get("excerpts") or []
        if re.match(r"(?i)table\s+\d", e["text"])
    ]
    claims = registry.call("verify_paper_claims", {"paper_id": paper["id"], "claims": expect["claims"]})["claims"]
    out.update(
        ablation=bool(abl.get("ablations")),
        code=art.get("code_url"),
        training=sorted(hp.get("training_hyperparameters") or {}),
        decoding=sorted(hp.get("hyperparameters") or {}),
        tables=[t.get("label") for t in tables],
        caption_failure_modes=captions,
        claims=[
            {
                "claim": c["claim"],
                "where": c["citation_tag"],
                "numbers_missing": c["numbers_missing"],
                "others": [f"p. {m.get('page')}, {m.get('section')}" for m in c.get("other_matches") or []],
            }
            for c in claims
        ],
    )
    problems = []
    if out["ablation"] != expect["ablation"]:
        problems.append("ablation")
    if bool(out["code"]) != expect["code"]:
        problems.append("code")
    missing = sorted(set(expect["training"]) - set(out["training"]))
    if missing:
        problems.append(f"training hyperparameters missing: {missing}")
    if len(tables) < expect["min_tables"]:
        problems.append(f"only {len(tables)} tables")
    if captions:
        problems.append("captions reported as failure modes")
    out["problems"] = problems
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", action="append", help="Only these ids (default: all known papers)")
    ap.add_argument("--keep-cache", action="store_true", help="Use your normal Noul cache instead of a temp one")
    a = ap.parse_args(argv)
    tmp = None
    if not a.keep_cache:
        tmp = tempfile.TemporaryDirectory(prefix="noul-live-")  # removed at exit, with the downloaded PDFs
        os.environ["NOUL_HOME"] = tmp.name
        os.environ["NOUL_DB_PATH"] = str(Path(tmp.name) / "cache.db")
    failed = 0
    for pid in a.paper or list(PAPERS):
        result = check(
            pid, PAPERS.get(pid, {"ablation": True, "code": True, "training": [], "min_tables": 0, "claims": []})
        )
        failed += bool(result["problems"])
        print(json.dumps({pid: result}, indent=2, ensure_ascii=False))
    print(f"\n{failed} paper(s) with problems" if failed else "\nall papers as expected")
    if tmp:
        tmp.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
