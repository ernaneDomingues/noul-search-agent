"""Fase 3 / Etapa 2 (#08): extract_benchmark_tables — Markdown, LaTeX and plain-text tables, normalization, CLI."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support  # noqa: E402

FULLTEXT = r"""--- Page 1 ---
Sparse-7B: Efficient Reasoning
Abstract
We present Sparse-7B. It achieves MMLU: 78.4% and a BLEU of 31.2 on WMT.
--- Page 4 ---
4 Experiments
Table 2: Main results on reasoning benchmarks.
| Model | MMLU | gsm-8k | human-eval |
| --- | --- | --- | --- |
| Llama-2 7B | 45.3% | 14.6 | 12.8 |
| Sparse-7B (ours) | **78.4%** | 56.2 ± 0.4 | 33 |
--- Page 5 ---
\begin{table}[t]
\centering
\begin{tabular}{lccc}
\toprule
Model & \textbf{HellaSwag} & ARC-c & TruthfulQA \\
\midrule
Llama-2 7B & 77.2 & 43.2 & 38.8 \\
Sparse-7B & \textbf{80.1} & \underline{51.7} & 45.0\% \\
\bottomrule
\end{tabular}
\caption{Commonsense benchmarks.}
\end{table}

Table 3: Inference efficiency.
Model          Latency (ms)   Throughput   PPL
Llama-2 7B     42.1           1,250        5.47
Sparse-7B      12.4ms         3,900        5.12

5 Conclusion
"""


def setUpModule():
    global c, t, cli, tt
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t
    from noul.tools import technical_tools as tt


class BenchmarkTablesTests(unittest.TestCase):
    def setUp(self):
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/sparse7b", FULLTEXT, title="Sparse-7B")
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch.object(c, name, side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tables(self):
        out = t.call("extract_benchmark_tables", {"paper_id": self.pid})
        return out, {tb["table_id"]: tb for tb in out["tables"]}

    def test_markdown_table_normalized(self):
        out, tables = self._tables()
        self.assertEqual((out["paper_id"], out["title"], out["source"]), (self.pid, "Sparse-7B", "fulltext"))
        md = tables["table_2"]
        self.assertEqual((md["format"], md["page"], md["label"]), ("markdown", 4, "Table 2"))
        self.assertEqual(md["caption"], "Main results on reasoning benchmarks.")
        self.assertEqual(md["headers"], ["Model", "MMLU", "GSM8K", "HumanEval"])
        self.assertEqual(md["rows"], [
            {"Model": "Llama-2 7B", "MMLU": 45.3, "GSM8K": 14.6, "HumanEval": 12.8},
            {"Model": "Sparse-7B (ours)", "MMLU": 78.4, "GSM8K": 56.2, "HumanEval": 33},
        ])
        self.assertIsInstance(md["rows"][1]["MMLU"], float)
        self.assertIsInstance(md["rows"][1]["HumanEval"], int)
        self.assertEqual(md["normalized_metrics"]["Llama-2 7B"], {"MMLU": 45.3, "GSM8K": 14.6, "HumanEval": 12.8})

    def test_latex_table(self):
        _, tables = self._tables()
        tex = next(tb for tb in tables.values() if tb["format"] == "latex")
        self.assertEqual(tex["caption"], "Commonsense benchmarks.")
        self.assertEqual(tex["page"], 5)
        self.assertEqual(tex["headers"], ["Model", "HellaSwag", "ARC-Challenge", "TruthfulQA"])
        self.assertEqual(tex["rows"][1], {"Model": "Sparse-7B", "HellaSwag": 80.1, "ARC-Challenge": 51.7,
                                          "TruthfulQA": 45.0})
        self.assertEqual(len(tex["rows"]), 2)

    def test_plain_text_table_and_inline_pairs(self):
        out, tables = self._tables()
        txt = tables["table_3"]
        self.assertEqual((txt["format"], txt["caption"]), ("text", "Inference efficiency."))
        self.assertEqual(txt["headers"], ["Model", "Latency_ms", "Throughput", "Perplexity"])
        self.assertEqual(txt["rows"], [
            {"Model": "Llama-2 7B", "Latency_ms": 42.1, "Throughput": 1250, "Perplexity": 5.47},
            {"Model": "Sparse-7B", "Latency_ms": 12.4, "Throughput": 3900, "Perplexity": 5.12},
        ])
        inline = tables["inline_metrics"]
        self.assertEqual(inline["normalized_metrics"]["reported"], {"MMLU": 78.4, "BLEU": 31.2})
        best = out["normalized_summary"]["best_by_metric"]
        self.assertEqual(best["Latency_ms"], {"model": "Sparse-7B", "value": 12.4, "direction": "lower_is_better"})
        self.assertEqual(best["GSM8K"]["model"], "Sparse-7B (ours)")
        self.assertEqual(out["normalized_summary"]["table_count"], 4)

    def test_number_and_metric_normalization(self):
        self.assertEqual(tt.parse_number("78.4%"), 78.4)
        self.assertEqual(tt.parse_number("84.5 ± 0.3"), 84.5)
        self.assertEqual(tt.parse_number("12.4ms"), 12.4)
        self.assertEqual(tt.parse_number("42"), 42)
        self.assertIsNone(tt.parse_number("-"))
        self.assertEqual(tt.parse_number("n-gram"), "n-gram")
        for raw, canon in (("gsm-8k", "GSM8K"), ("human-eval", "HumanEval"), ("arc_challenge", "ARC-Challenge"),
                           ("ROUGE-L", "ROUGE-L"), ("f1-score", "F1"), ("AUROC", "AUC"), ("acc", "Accuracy"),
                           ("MMLU (5-shot)", "MMLU"), ("Latency (ms)", "Latency_ms")):
            self.assertEqual(tt.normalize_metric_name(raw), canon, raw)

    def test_abstract_fallback_and_validation(self):
        pid = _support.seed_fulltext_paper(c, "doi:10.1000/abs", None, open_access=False,
                                           abstract="Our model scores 67.1% accuracy on GSM8K.")
        out = t.extract_benchmark_tables(pid)
        self.assertEqual(out["source"], "abstract")
        self.assertEqual(out["normalized_summary"]["by_metric"]["GSM8K"], {"reported": 67.1})
        self.assertTrue(any("title/abstract" in w for w in out["warnings"]))
        with self.assertRaises(c.HTTPError) as ctx:
            t.call("extract_benchmark_tables", {})
        self.assertEqual(ctx.exception.code, 400)

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_cli(self):
        code, out = self._cli("benchmarks", self.pid)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["tables"][0]["headers"], ["Model", "MMLU", "GSM8K", "HumanEval"])
        code, out = self._cli("benchmarks", self.pid, "--pretty")
        self.assertEqual(code, 0)
        self.assertIn("## Table 2 (page 4, markdown)", out)
        self.assertIn("| Model | MMLU | GSM8K | HumanEval |", out)
        self.assertIn("- Latency_ms: Sparse-7B = 12.4", out)


if __name__ == "__main__":
    unittest.main()
