"""Regression gate for the heuristic classifiers, measured on labelled golden sets (see tests/golden/README.md).

Fails when accuracy or macro-F1 of a classifier drops below the recorded baseline. Run as a script with
`--update-baseline` after an intentional improvement, or with `--report` to print per-class metrics and misses."""

import argparse
import json
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


def setUpModule():
    _support.isolate_environment()


class GoldenSetRegressionTests(unittest.TestCase):
    def test_metrics_do_not_regress(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for name in SETS:
            with self.subTest(golden_set=name):
                result = evaluate(name)
                base = baseline[name]
                self.assertEqual(result["n"], base["n"], "golden set changed: refresh the baseline in the same PR")
                for metric in ("accuracy", "macro_f1"):
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
    results = {name: evaluate(name) for name in SETS}
    if args.report or not args.update_baseline:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.update_baseline:
        slim = {name: {k: r[k] for k in ("n", "accuracy", "macro_f1")} for name, r in results.items()}
        BASELINE.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {BASELINE}: {slim}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
