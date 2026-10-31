"""Etapa 2 (#04): compare_papers — 5 dimensions, Markdown matrix, convergence/divergence, CLI."""

import contextlib
import io
import json
import unittest
from unittest import mock

import _support


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.ids = _support.seed_phase2_papers(c, ["llm_a", "rct", "mice"])
        patcher = mock.patch(_support.core_target("_http_json"), side_effect=_support.no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_five_dimensions_per_paper(self):
        out = t.call("compare_papers", {"paper_ids": self.ids})
        llm, rct, mice = out["papers"]
        self.assertEqual([p["paper_id"] for p in out["papers"]], self.ids)

        self.assertTrue(llm["core_hypothesis"].startswith("We propose QLoRA"))
        self.assertIn("Design: empirical | Domain: computer_science", llm["methodology"])
        self.assertIn("we train", llm["methodology"])
        self.assertEqual(llm["benchmarks_used"][:2], ["MMLU", "GSM8K"])
        self.assertIn("84.5%", llm["primary_metrics"]["percentages"])
        self.assertEqual(llm["primary_metrics"]["speedup"], "2.5x")
        self.assertIn("achieves 84.5%", llm["primary_metrics"]["headline_finding"])
        self.assertTrue(any("computational cost" in x for x in llm["reported_limitations"]))

        self.assertTrue(rct["core_hypothesis"].startswith("Objective: To evaluate"))
        self.assertIn("Randomized double-blind", rct["methodology"])
        self.assertIn("n = 1200", rct["benchmarks_used"])
        self.assertTrue(any("1,200 patients" in x for x in rct["benchmarks_used"]))
        self.assertEqual(rct["primary_metrics"]["HR"], "0.75")
        self.assertEqual(rct["primary_metrics"]["p_values"], ["p < 0.01"])
        self.assertIn("95% CI 0.62-0.91", rct["primary_metrics"]["confidence_intervals"])
        self.assertTrue(rct["primary_metrics"]["headline_finding"].startswith("Results:"))
        self.assertTrue(any("single-center" in x for x in rct["reported_limitations"]))

        self.assertEqual(mice["benchmarks_used"], ["Not explicitly stated in abstract"])
        lims = " ".join(mice["reported_limitations"])
        self.assertTrue(mice["reported_limitations"][0].startswith("However, the small sample"))
        self.assertIn("Preclinical evidence", lims)
        self.assertIn("Evasive availability statement", lims)

    def test_markdown_table_and_summaries(self):
        c.cache_set(
            "paper:doi:10.1000/pipe",
            {
                **_support.PHASE2_PAPERS["llm_a"],
                "id": "doi:10.1000/pipe",
                "title": "A | B\nsplit title",
                "abstract": "",
            },
        )
        out = t.compare_papers(self.ids + ["doi:10.1000/pipe"])
        rows = out["markdown_table"].splitlines()
        self.assertEqual(
            rows[0],
            "| Paper | Core Problem | Methodology | Datasets / Population | Key Results | Declared Limitations |",
        )
        self.assertEqual(rows[1], "| :--- | :--- | :--- | :--- | :--- | :--- |")
        self.assertEqual(len(rows), 2 + 4)
        self.assertIn("A \\| B split title (2023)", rows[-1])
        for row in rows:  # every row has exactly 6 cells (escaped pipes do not split cells)
            self.assertEqual(len([x for x in row.replace("\\|", "").split("|")]) - 2, 6, row)

        conv, div = out["convergence_summary"], out["divergence_summary"]
        self.assertIn("Shared domain(s): computer_science (2), biomedical (2)", conv)
        self.assertIn("Shared reported metric types: percentages", conv)
        self.assertIn("[Smith et al., 2023a]", conv)  # same author/year -> disambiguated tags
        self.assertIn("[Smith et al., 2023b]", conv)
        self.assertIn("Evidence levels differ: rct", div)
        self.assertIn("preclinical (weight 0.2: [Tanaka, 2024])", div)
        self.assertIn("Openness differs", div)
        self.assertIn("[Garcia & Lee, 2021]", div)

    def test_validation_errors(self):
        for bad in ([], ["  "], [1, 2], ["x"] * 0 + [f"id{i}" for i in range(11)]):
            with self.subTest(bad=bad), self.assertRaises(c.HTTPError) as ctx:
                t.compare_papers(bad)
            self.assertEqual(ctx.exception.code, 400)
        with self.assertRaises(c.HTTPError) as ctx:
            t.call("compare_papers", {"paper_ids": "doi:10.1000/rct"})
        self.assertEqual(ctx.exception.code, 400)

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_cli_positional_and_comma_ids(self):
        code, out = self._cli("compare", *self.ids)
        data = json.loads(out)
        self.assertEqual((code, [p["paper_id"] for p in data["papers"]]), (0, self.ids))

        code, out = self._cli("compare", ",".join(self.ids), "--pretty")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Comparative matrix"))
        self.assertIn("| Paper | Core Problem |", out)
        self.assertLess(out.index("## Convergence"), out.index("## Divergence"))


if __name__ == "__main__":
    unittest.main()
