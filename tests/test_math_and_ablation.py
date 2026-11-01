"""Fase 3 / Etapa 5 (#11, #12): extract_paper_math (LaTeX / typed Python) and analyze_ablation_tables, plus CLI."""

import ast
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

MATH_TEXT = r"""3 Method
Scaled dot-product attention is
\begin{equation}
\mathrm{Attention}(Q, K, V) = \mathrm{softmax}\left(\frac{QK^{\top}}{\sqrt{d_k}}\right)V
\label{eq:attention}
\end{equation}
The entropy term is $$H(p) = -\sum_{i=1}^{N} p_i \log p_i$$ and the update is
\begin{align}
x_{t+1} &= x_t - \eta \nabla f(x_t) \\
        &= x_t - \eta g_t
\end{align}
The training objective is
L_total = L_ce + λ · L_kl

Algorithm 1: Greedy decoding
Input: model f, prompt x, max length T
1. y <- empty sequence
2. for t = 1..T append argmax f(x, y)
3. return y
"""

ABLATION_TEXT = """4 Ablations
Table 3: Ablation study of our components.
| Model | MMLU | GSM8K |
| --- | --- | --- |
| Baseline | 60.0 | 40.0 |
| Full model (ours) | 70.0 | 55.0 |
| w/o KL loss | 69.8 | 54.9 |
| w/o retrieval | 62.0 | 43.0 |
| w/o curriculum | 68.0 | 52.0 |
"""


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class _Offline(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        self.assertEqual(code, 0)
        return buf.getvalue()


class MathExtractionTests(_Offline):
    def setUp(self):
        super().setUp()
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/math", MATH_TEXT, title="Math paper")

    def test_latex_mode_returns_clean_delimited_equations_and_algorithms(self):
        out = t.call("extract_paper_math", {"paper_id": self.pid})
        self.assertEqual(out["mode"], "latex")
        self.assertEqual((out["equation_count"], out["algorithm_count"]), (4, 1))
        kinds = [it["kind"] for it in out["items"]]
        self.assertEqual(kinds, ["equation"] * 4 + ["algorithm"])
        first = out["items"][0]
        self.assertEqual(first["label"], "eq:attention")
        self.assertTrue(first["latex"].startswith("$$ ") and first["latex"].endswith(" $$"))
        self.assertIn(r"\frac{QK^{\top}}{\sqrt{d_k}}", first["latex"])
        self.assertNotIn(r"\label", first["latex"])
        self.assertTrue(all(it["python_code"] == "" for it in out["items"]))

    def test_python_mode_returns_typed_functions_that_parse(self):
        out = t.call("extract_paper_math", {"paper_id": self.pid, "mode": "python"})
        for it in out["items"]:
            self.assertEqual(it["latex"], "")
            tree = ast.parse(it["python_code"])
            self.assertIn("import math", it["python_code"])
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
            self.assertIsNotNone(fn.returns)
            self.assertTrue(all(arg.annotation is not None for arg in fn.args.args))
            self.assertIsNotNone(ast.get_docstring(fn))
        attention, entropy, update, loss, algo = out["items"]
        self.assertIn("def equation_1(", attention["python_code"])
        self.assertIn("-> Any:", attention["python_code"])  # matrix expression (K transposed)
        self.assertIn("def equation_4(L_ce: float, lam: float, L_kl: float) -> float:", loss["python_code"])
        self.assertIn("math.sqrt(d_k)", attention["python_code"])
        self.assertIn(r"\frac{QK^{\top}}{\sqrt{d_k}}", attention["python_code"])  # original LaTeX in docstring
        self.assertIn("math.log(p[i])", entropy["python_code"])
        self.assertIn("for i in range(1, (N) + 1)", entropy["python_code"])
        self.assertEqual(loss["variables"], ["L_ce", "lam", "L_kl"])
        self.assertIn("return L_ce + lam * L_kl", loss["python_code"])
        self.assertIn("def algorithm_1(f: Any, x: Any, T: Any) -> Any:", algo["python_code"])
        self.assertIn("# 2. for t = 1..T append argmax f(x, y)", algo["python_code"])
        ns = {}
        exec(compile(loss["python_code"], "<loss>", "exec"), ns)
        self.assertAlmostEqual(ns["equation_4"](L_ce=1.0, lam=0.5, L_kl=2.0), 2.0)

    def test_both_mode_fills_latex_and_python(self):
        out = t.call("extract_paper_math", {"paper_id": self.pid, "mode": "both"})
        self.assertTrue(all(it["latex"] and it["python_code"] for it in out["items"]))

    def test_invalid_mode_is_rejected_with_400(self):
        with self.assertRaises(c.HTTPError) as ctx:
            t.call("extract_paper_math", {"paper_id": self.pid, "mode": "markdown"})
        self.assertEqual(ctx.exception.code, 400)

    def test_cli_math(self):
        out = json.loads(self._cli("math", self.pid, "--mode", "python"))
        self.assertEqual(out["mode"], "python")
        self.assertEqual(len(out["items"]), 5)
        pretty = self._cli("math", self.pid, "--mode", "both", "--pretty")
        self.assertIn("# Math & algorithms: Math paper", pretty)
        self.assertIn("```python", pretty)
        self.assertIn("def algorithm_1(", pretty)


class AblationTests(_Offline):
    def setUp(self):
        super().setUp()
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/ablation", ABLATION_TEXT, title="Ablation paper")

    def test_entries_are_ranked_by_percentual_impact_with_deltas(self):
        out = t.call("analyze_ablation_tables", {"paper_id": self.pid})
        self.assertEqual(out["reference"]["component"], "Full model (ours)")
        self.assertEqual(out["reference"]["baseline"]["component"], "Baseline")
        ranked = [(e["component"], e["metric"]) for e in out["ablations"]]
        self.assertEqual(
            ranked,
            [
                ("w/o retrieval", "GSM8K"),
                ("w/o retrieval", "MMLU"),
                ("w/o curriculum", "GSM8K"),
                ("w/o curriculum", "MMLU"),
                ("w/o KL loss", "MMLU"),
                ("w/o KL loss", "GSM8K"),
            ],
        )
        top = out["ablations"][0]
        self.assertEqual((top["baseline_value"], top["variant_value"], top["delta"]), (55.0, 43.0, -12.0))
        self.assertEqual(top["delta_percent"], -21.82)
        self.assertFalse(top["is_marginal"])
        kl = [e for e in out["ablations"] if e["component"] == "w/o KL loss"]
        self.assertEqual([e["delta_percent"] for e in kl], [-0.29, -0.18])
        self.assertTrue(all(e["is_marginal"] for e in kl))
        self.assertNotIn("Baseline", {e["component"] for e in out["ablations"]})

    def test_marginal_and_dominant_component_alerts(self):
        alerts = t.call("analyze_ablation_tables", {"paper_id": self.pid})["warnings_and_alerts"]
        marginal = [a for a in alerts if "Marginal component" in a]
        dominant = [a for a in alerts if "Dominant component" in a]
        self.assertEqual(len(marginal), 1)
        self.assertIn("'w/o KL loss'", marginal[0])
        self.assertEqual(len(dominant), 2)
        self.assertTrue(all("'w/o retrieval'" in a and "80%" in a for a in dominant))

    def test_text_list_ablation_uses_sum_of_deltas_for_dominance(self):
        pid = _support.seed_fulltext_paper(
            c,
            "doi:10.1000/ablation-list",
            ("Ablation results (accuracy):\nFull model: 75.3\nw/o adapter: 71.2\nno warmup: 75.1\n"),
            title="List",
        )
        out = t.call("analyze_ablation_tables", {"paper_id": pid})
        self.assertEqual([e["component"] for e in out["ablations"]], ["w/o adapter", "no warmup"])
        self.assertEqual(out["ablations"][0]["delta_percent"], -5.44)
        self.assertTrue(out["ablations"][1]["is_marginal"])
        self.assertTrue(any("'w/o adapter'" in a and "95%" in a for a in out["warnings_and_alerts"]))

    def test_paper_without_ablation_warns(self):
        pid = _support.seed_fulltext_paper(c, "doi:10.1000/none", "We study transformers.\n", title="None")
        out = t.call("analyze_ablation_tables", {"paper_id": pid})
        self.assertEqual(out["ablations"], [])
        self.assertTrue(any("No ablation" in w for w in out["warnings"]))

    def test_cli_ablation(self):
        out = json.loads(self._cli("ablation", self.pid))
        self.assertEqual(out["ablations"][0]["component"], "w/o retrieval")
        pretty = self._cli("ablation", self.pid, "--pretty")
        self.assertIn("# Ablation analysis: Ablation paper", pretty)
        self.assertIn("| w/o retrieval | GSM8K | 55.0 | 43.0 | -12 | -21.82% |  |", pretty)
        self.assertIn("## Alerts", pretty)


if __name__ == "__main__":
    unittest.main()
