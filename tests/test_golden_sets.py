"""Regression gate for the heuristics, measured on labelled golden sets (see tests/golden/README.md).

Fails when a metric (classifier accuracy / macro-F1, or the evidence-signal metrics) drops below the recorded
baseline. Run as a script with
`--update-baseline` after an intentional improvement, or with `--report` to print per-class metrics and misses."""

import argparse
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

import _support

GOLDEN = Path(__file__).resolve().parent / "golden"
BASELINE = GOLDEN / "baseline.json"
EPSILON = 1e-9


def _load(name):
    with open(GOLDEN / name, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _predict_design(case):
    from noul.evidence import classify as noul_core

    return noul_core.classify_evidence(case.get("title", ""), case.get("abstract", ""), case.get("pub_types"))


def _predict_domain(case):
    from noul.providers import domain_classifier

    return domain_classifier.resolve_domain(
        categories=case.get("categories"),
        title=case.get("title", ""),
        abstract=case.get("abstract", ""),
        journal=case.get("journal", ""),
    ).value


SETS = {
    "evidence_design": ("evidence_design.jsonl", _predict_design),
    "domain": ("domain.jsonl", _predict_domain),
    # Written before Phase 1c tuned the domain classifier and never used to tune it: an honest generalisation check.
    "domain_holdout": ("domain_holdout.jsonl", _predict_domain),
}


def evaluate(name):
    """Return accuracy, macro-F1, per-class precision/recall and the list of misses for one golden set."""
    filename, predict = SETS[name]
    cases = _load(filename)
    pairs = [(c["label"], predict(c), c["id"]) for c in cases]
    labels = sorted({gold for gold, _, _ in pairs})
    tp, fp, fn = Counter(), Counter(), Counter()
    for gold, pred, _ in pairs:
        if gold == pred:
            tp[gold] += 1
        else:
            fp[pred] += 1
            fn[gold] += 1
    per_class, f1s = {}, []
    for label in labels:
        precision = tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        recall = tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
        f1s.append(f1)
    return {
        "n": len(pairs),
        "accuracy": round(sum(tp.values()) / len(pairs), 4),
        "macro_f1": round(sum(f1s) / len(f1s), 4),
        "per_class": per_class,
        "misses": [{"id": cid, "gold": gold, "predicted": pred} for gold, pred, cid in pairs if gold != pred],
    }


# --------------------------------------------------------------------------- evidence signals (Phase 3b)

SIGNAL_METRICS = ("signals_accuracy", "hyperparameters_f1", "claims_located", "retrieval_recall_at_1")


def _norm(text):
    return " ".join(str(text or "").split()).lower()


def _excerpt_texts(excerpts):
    return [e["text"] if isinstance(e, dict) else e for e in (excerpts or [])]


def _seed_signal_case(case):
    from noul.library import fulltext
    from noul.net import cache

    pid = f"doi:10.5555/golden.{case['id']}"
    paper = {
        "id": pid,
        "doi": pid.split(":", 1)[1],
        "title": case["title"],
        "abstract": case["abstract"],
        "authors": ["Golden Case"],
        "year": 2024,
        "journal": "Golden",
        "domain": case["domain"],
        "open_access": False,
        "sources": ["test"],
    }
    cache.cache_set("paper:" + pid, paper)
    if case.get("file"):
        # The fixtures are raw page text as a PDF extractor returns it: run the real normalisation on it.
        text = fulltext.normalize_pdf_text((GOLDEN / case["file"]).read_text(encoding="utf-8"))
        cache.cache_set(
            "ft:" + pid, {"text": text, "url": None, "source": "golden", "format": fulltext.PDF_TEXT_FORMAT}
        )
    return pid


def _signal_case(case):
    """Run the analytic tools on one golden paper and score them against its `expect` block."""
    from noul import registry

    exp = case["expect"]
    pid = _seed_signal_case(case)
    checks = []  # (name, passed)
    abl = registry.call("analyze_ablation_tables", {"paper_id": pid})
    checks.append(("ablation", bool(abl.get("ablations")) == exp["ablation"]))
    art = registry.call("scan_paper_artifacts", {"id": pid, "verify_http": False, "inspect_fulltext": True})
    checks.append(("code", bool(art.get("has_code")) == exp["code"]))
    tables = [
        t
        for t in registry.call("extract_benchmark_tables", {"paper_id": pid}).get("tables") or []
        if t.get("format") != "inline"  # "Inline metrics" collects value pairs from prose, not a table
    ]
    checks.append(("tables", len(tables) == exp["tables"]))
    fm = registry.call("detect_failure_modes", {"paper_id": pid})
    captions = [
        t
        for sig in fm.get("signals") or []
        for t in _excerpt_texts(sig.get("excerpts"))
        if re.match(r"(?i)table\s+\d", t)
    ]
    checks.append(("no_caption_failure_modes", not captions))
    scenarios = " ".join(_excerpt_texts(fm.get("baseline_superiority_scenarios"))).lower()
    for phrase in exp.get("not_baselines") or []:  # comparisons of the method with itself
        checks.append((f"not_a_baseline:{phrase}", phrase.lower() not in scenarios))
    if exp.get("auto_claims_clean"):
        auto = registry.call("verify_paper_claims", {"paper_id": pid})["claims"]
        clean = all(
            not re.search(r"(?i)arxiv:|proceedings|conference|workshop", c["claim"]) and not c["numbers_missing"]
            for c in auto
        )
        checks.append(("auto_claims_clean_and_grounded", clean))

    prompts = registry.call("extract_prompts_and_decoding", {"paper_id": pid})
    values = {**(prompts.get("hyperparameters") or {}), **(prompts.get("training_hyperparameters") or {})}
    found, expected = set(values), set(exp["hyperparameters"])
    # A parameter counts as found only with the right value, when the case states it.
    wrong = {k for k, v in (exp.get("hyperparameter_values") or {}).items() if k in found and str(values[k]) != str(v)}
    hp = (len((found & expected) - wrong), len(found - expected) + len(wrong), len(expected - found))  # tp, fp, fn

    located = []
    if exp["claims"]:
        res = registry.call("verify_paper_claims", {"paper_id": pid, "claims": [c["claim"] for c in exp["claims"]]})
        for want, got in zip(exp["claims"], res["claims"], strict=True):
            best = got.get("best_match") or {}
            section = (best.get("section") or "").lower().removeprefix("section ")
            # exact section (with or without the "Section " prefix): "Table 2" must not match "Table 20"
            ok = any(w["section"].lower() == section and best.get("page") == w["page"] for w in want["locations"])
            located.append((want["claim"], ok))
    retrieved = []
    for q in exp["questions"]:
        res = registry.call("get_fulltext", {"id": pid, "question": q["q"]})
        top = [_norm(t) for t in _excerpt_texts(res.get("excerpts"))[:1]]
        retrieved.append((q["q"], any(_norm(q["answer"]) in t for t in top)))
    return {
        "checks": checks,
        "hp": hp,
        "found_hp": sorted(found),
        "wrong_hp_values": {k: values[k] for k in sorted(wrong)},
        "n_hp": len(expected),
        "claims": located,
        "retrieval": retrieved,
    }


def evaluate_signals():
    from unittest import mock

    cases = _load("evidence_signals.jsonl")
    with (
        mock.patch(_support.core_target("_http_json"), side_effect=_support.no_network),
        mock.patch(_support.core_target("_http_get"), side_effect=_support.no_network),
    ):
        per_case = {c["id"]: _signal_case(c) for c in cases}
    checks = [ok for r in per_case.values() for _, ok in r["checks"]]
    tp = sum(r["hp"][0] for r in per_case.values())
    fp = sum(r["hp"][1] for r in per_case.values())
    fn = sum(r["hp"][2] for r in per_case.values())
    claims = [ok for r in per_case.values() for _, ok in r["claims"]]
    questions = [ok for r in per_case.values() for _, ok in r["retrieval"]]
    misses = {
        cid: {
            "checks": [n for n, ok in r["checks"] if not ok],
            "hyperparameters_found": r["found_hp"],
            "hyperparameter_values_wrong": r["wrong_hp_values"],
            "claims": [c for c, ok in r["claims"] if not ok],
            "questions": [q for q, ok in r["retrieval"] if not ok],
        }
        for cid, r in per_case.items()
    }
    return {
        "n": len(cases),
        "signals_accuracy": round(sum(checks) / len(checks), 4),
        "hyperparameters_f1": round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else 1.0,
        "claims_located": round(sum(claims) / len(claims), 4),
        "retrieval_recall_at_1": round(sum(questions) / len(questions), 4),
        # Pinned sizes: dropping a hard question or claim from the set must not silently raise a metric.
        "n_checks": len(checks),
        "n_hyperparameters": sum(r["n_hp"] for r in per_case.values()),
        "n_claims": len(claims),
        "n_questions": len(questions),
        "misses": {
            k: v
            for k, v in misses.items()
            if any(v[x] for x in ("checks", "claims", "questions", "hyperparameter_values_wrong"))
        },
    }


def evaluate_all():
    out = {name: evaluate(name) for name in SETS}
    out["evidence_signals"] = evaluate_signals()
    return out


def _metrics(result):
    return {k: v for k, v in result.items() if k not in ("per_class", "misses")}


def setUpModule():
    _support.isolate_environment()


class GoldenSetRegressionTests(unittest.TestCase):
    def test_metrics_do_not_regress(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        results = evaluate_all()
        self.assertEqual(sorted(results), sorted(baseline))
        for name, result in results.items():
            with self.subTest(golden_set=name):
                base = baseline[name]
                for size in [k for k in base if k == "n" or k.startswith("n_")]:
                    self.assertEqual(
                        result[size], base[size], "golden set changed: refresh the baseline in the same PR"
                    )
                for metric in [k for k in base if k != "n" and not k.startswith("n_")]:
                    self.assertGreaterEqual(
                        result[metric] + EPSILON,
                        base[metric],
                        f"{name} {metric} regressed: {result[metric]} < baseline {base[metric]}; "
                        f"misses: {result['misses']}",
                    )

    def test_labels_are_in_the_taxonomy(self):
        from noul.evidence import classify as noul_core
        from noul.schemas.domain import ScientificDomain

        allowed = {
            "evidence_design": set(noul_core.EV_W),
            "domain": {d.value for d in ScientificDomain},
            "domain_holdout": {d.value for d in ScientificDomain},
        }
        for name, (filename, _) in SETS.items():
            for case in _load(filename):
                with self.subTest(case=case["id"]):
                    self.assertIn(case["label"], allowed[name])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update-baseline", action="store_true", help="Overwrite tests/golden/baseline.json")
    ap.add_argument("--report", action="store_true", help="Print per-class metrics and misses")
    args = ap.parse_args(argv)
    setUpModule()
    results = evaluate_all()
    if args.report or not args.update_baseline:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.update_baseline:
        slim = {name: _metrics(r) for name, r in results.items()}
        BASELINE.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {BASELINE}: {slim}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
