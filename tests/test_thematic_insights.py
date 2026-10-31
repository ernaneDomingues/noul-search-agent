"""Etapa 3 (#05): get_thematic_insights — 3 cross-cutting axes, explicit citations, citations_map, CLI."""

import contextlib
import io
import json
import re
import unittest
from unittest import mock

import _support

CITATION = re.compile(r"\[[^\[\]]+, (?:\d{4}[a-z]?|n\.d\.)\]")
AXES = ("consolidated_consensuses", "methodological_tensions", "emerging_future_work")


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.ids = _support.seed_phase2_papers(c)
        patcher = mock.patch(_support.core_target("_http_json"), side_effect=_support.no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_axes_and_citations_map(self):
        out = t.call("get_thematic_insights", {"paper_ids": self.ids, "topic": "Efficiency and BDNF"})
        self.assertEqual(out["topic"], "Efficiency and BDNF")
        self.assertEqual(
            out["citations_map"],
            {
                "[Smith et al., 2023]": ["doi:10.1000/qlora"],
                "[Brown & Green, 2024]": ["doi:10.1000/gptq"],
                "[Garcia & Lee, 2021]": ["doi:10.1000/rct"],
                "[Tanaka, 2024]": ["doi:10.1000/mice"],
            },
        )
        for axis in AXES:
            self.assertTrue(out[axis], axis)

    def test_every_statement_is_cited_and_crosses_papers(self):
        out = t.get_thematic_insights(self.ids)
        known = set(out["citations_map"])
        for axis in AXES:
            for item in out[axis]:
                tags = CITATION.findall(item)
                self.assertTrue(tags, f"{axis}: uncited statement {item!r}")
                self.assertTrue(set(tags) <= known, item)

        # Synthesis crosses papers instead of listing isolated summaries.
        def crossing(axis):
            return [x for x in out[axis] if len(set(CITATION.findall(x))) >= 2]

        self.assertTrue(crossing("consolidated_consensuses"))
        self.assertTrue(crossing("methodological_tensions"))
        self.assertTrue(all(" vs. " in x for x in crossing("methodological_tensions")))

        cons, tens, fut = (" ".join(out[a]) for a in AXES)
        self.assertIn('Convergent focus on "quantization"', cons)
        self.assertIn("MMLU is used by [Smith et al., 2023], [Brown & Green, 2024]", cons)
        self.assertIn("Highest-level evidence (rct) [Garcia & Lee, 2021]", cons)
        self.assertIn("[Garcia & Lee, 2021] (rct, weight 0.8) vs. [Tanaka, 2024] (preclinical", tens)
        self.assertIn("Reproducibility asymmetry (computer_science): [Smith et al., 2023]", tens)
        self.assertIn("Conditional/mixed results: [Brown & Green, 2024]", tens)
        self.assertIn("translation from preclinical models to humans: flagged by [Tanaka, 2024]", fut)
        self.assertIn("'Future work should study quantization-aware training.' [Brown & Green, 2024]", fut)
        self.assertEqual(out["topic"], "quantization / bdnf")

    def test_single_paper_and_missing_metadata_still_cited(self):
        c.cache_set("paper:oa:W9", {"id": "oa:W9", "title": "Untitled note", "abstract": "", "authors": []})
        out = t.get_thematic_insights(["oa:W9"])
        self.assertEqual(out["citations_map"], {"[Anon., n.d.]": ["oa:W9"]})
        for axis in AXES:
            self.assertTrue(out[axis] and all(CITATION.search(x) for x in out[axis]), axis)
        with self.assertRaises(c.HTTPError) as ctx:
            t.get_thematic_insights([])
        self.assertEqual(ctx.exception.code, 400)

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["insights", *self.ids[:2], "--topic", "LLM quantization"])
        data = json.loads(buf.getvalue())
        self.assertEqual((code, data["topic"]), (0, "LLM quantization"))
        self.assertEqual(len(data["citations_map"]), 2)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["insights", ",".join(self.ids), "--pretty"])
        text = buf.getvalue()
        self.assertEqual(code, 0)
        for heading in (
            "## Consolidated consensuses",
            "## Methodological tensions",
            "## Emerging future work",
            "## References",
        ):
            self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
