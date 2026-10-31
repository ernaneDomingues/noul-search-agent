"""Fase 4 / Etapa 2 (#14): detect_failure_modes — failure mode categorization, critical assumptions,
baseline superiority scenarios (text & benchmark table) and the `noul failures` CLI."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support  # noqa: E402

FULLTEXT = """--- Page 1 ---
1 Introduction
We propose NoulReasoner for English-only reasoning tasks with a context window of 32k tokens.
Our method assumes that training and test distributions are i.i.d. and requires that clean labels are available.

Table 1: Main evaluation results across benchmarks.
| Model | MMLU | GSM8K | Latency (ms) |
| --- | --- | --- | --- |
| BM25 + Linear Model | 71.0 | 68.5 | 12.0 |
| Llama-3-8B | 74.2 | 79.5 | 28.0 |
| Ours (NoulReasoner) | 78.4 | 76.0 | 45.0 |

--- Page 2 ---
5 Limitations and Error Analysis
First, our model fails when the sequence length exceeds > 32k tokens due to quadratic complexity O(n^2) and memory overflow.
Second, the system is brittle and sensitive to noisy prompts and adversarial perturbations in user inputs.
Third, accuracy drops slightly under out-of-distribution (OOD) domain shift and fails to generalize to unseen domains or cross-lingual settings.
Fourth, multi-step decoding introduces prohibitive latency (45 ms per token) and computational cost overhead.
Fifth, clinical validation is a retrospective single-center study with a small sample (n = 42 patients) and selection bias, tested only in vitro and in mice.
Finally, in low-data regimes on small datasets, our approach is outperformed by a simple baseline such as logistic regression or BM25.
Under the condition of parallel trends and no unmeasured confounding, the estimator remains unbiased.
"""


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class FailureModesTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pid = _support.seed_fulltext_paper(
            c,
            "doi:10.1000/failure-modes",
            FULLTEXT,
            title="NoulReasoner: Multi-step Reasoning",
            abstract="We present NoulReasoner. A limitation is that it struggles with missing data.",
        )

    def test_categorizes_all_failure_modes_and_severities(self):
        out = t.call("detect_failure_modes", {"paper_id": self.pid})
        categories = {fm["category"] for fm in out["failure_modes"]}
        for expected in (
            "long_context",
            "noise_sensitivity",
            "out_of_distribution",
            "latency_or_compute",
            "sample_or_population_bias",
        ):
            self.assertIn(expected, categories)

        by_cat = {}
        for fm in out["failure_modes"]:
            by_cat.setdefault(fm["category"], []).append(fm)

        self.assertEqual(by_cat["long_context"][0]["severity"], "high")
        self.assertTrue(by_cat["long_context"][0]["trigger_condition"])
        self.assertEqual(by_cat["latency_or_compute"][0]["severity"], "high")
        self.assertEqual(by_cat["sample_or_population_bias"][0]["severity"], "high")

    def test_extracts_critical_assumptions_and_operational_limits(self):
        out = t.call("detect_failure_modes", {"paper_id": self.pid})
        assumptions_blob = " ".join(out["critical_assumptions"]).lower()
        self.assertIn("i.i.d.", assumptions_blob)
        self.assertIn("clean labels", assumptions_blob)
        self.assertIn("parallel trends", assumptions_blob)
        self.assertIn("no unmeasured confounding", assumptions_blob)

        limits = out["operational_limits"]
        self.assertEqual(limits.get("max_context_tokens"), 32000)
        self.assertEqual(limits.get("min_sample_size"), 42)
        self.assertEqual(limits.get("max_latency_ms"), 45)
        self.assertIn("English", limits.get("supported_languages", []))

    def test_detects_baseline_superiority_from_text_and_benchmark_tables(self):
        out = t.call("detect_failure_modes", {"paper_id": self.pid})
        scenarios = out["baseline_superiority_scenarios"]
        blob = "\n".join(scenarios)
        # From text
        self.assertTrue(any("outperformed by a simple baseline" in s for s in scenarios))
        # From Table 1: Llama-3-8B (79.5) beats Ours (76.0) on GSM8K
        self.assertIn("GSM8K", blob)
        self.assertIn("Llama-3-8B", blob)

    def test_no_fulltext_flag_and_cli_pretty(self):
        out_abs = t.call("detect_failure_modes", {"paper_id": self.pid, "inspect_fulltext": False})
        cats_abs = {fm["category"] for fm in out_abs["failure_modes"]}
        self.assertIn("noise_sensitivity", cats_abs)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["failures", self.pid, "--pretty"]), 0)
        pretty = buf.getvalue()
        self.assertIn("# Failure modes & critical assumptions:", pretty)
        self.assertIn("[long_context]", pretty)
        self.assertIn("## Critical Assumptions", pretty)
        self.assertIn("## Baseline Superiority Scenarios", pretty)


if __name__ == "__main__":
    unittest.main()
