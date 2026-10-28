"""Etapa 5 (#07): suggest_boolean_queries — acronyms/emerging terms, AND/OR/NOT strategies, CLI `expand --run`."""
import contextlib
import io
import json
import unittest
from unittest import mock

import _support

STRATEGIES = ["precision_deepening", "synonym_and_method_expansion", "noise_exclusion"]


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class BooleanExpansionTests(unittest.TestCase):
    def setUp(self):
        self.ids = _support.seed_phase2_papers(c)
        self.llm_ids = [i for i in self.ids if i.endswith(("qlora", "gptq"))]
        patcher = mock.patch.object(c, "_http_json", side_effect=_support.no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extracts_acronyms_and_terms_from_papers(self):
        out = t.call("suggest_boolean_queries", {"query": "llm quantization", "paper_ids": self.llm_ids})
        self.assertEqual(out["original_query"], "llm quantization")
        self.assertEqual(out["corpus_size"], 2)
        for acr in ("LoRA", "QLoRA", "GPTQ", "MMLU"):
            self.assertIn(acr, out["extracted_acronyms"])
        for noise in ("LLM", "GB", "AND", "OR"):  # query word, unit, operators
            self.assertNotIn(noise, out["extracted_acronyms"])
        self.assertIn("language models", out["emerging_terms"])
        self.assertFalse(any("quantization" in x or "github" in x for x in out["emerging_terms"]))

    def test_three_strategies_with_explicit_operators(self):
        out = t.suggest_boolean_queries("llm quantization", self.llm_ids)
        sug = out["suggestions"]
        self.assertEqual([s["index"] for s in sug], [1, 2, 3])
        self.assertEqual([s["strategy"] for s in sug], STRATEGIES)
        for s in sug:
            self.assertTrue(s["boolean_query"].startswith("(llm AND quantization)")
                            or s["boolean_query"].startswith("((llm AND quantization)"), s)
            self.assertTrue(s["rationale"] and s["target_terms"])
        q1, q2, q3 = (s["boolean_query"] for s in sug)
        self.assertEqual(q1, '(llm AND quantization) AND ("MMLU" OR "language models" OR "large language")')
        self.assertIn(" OR ", q2)
        self.assertIn(" AND ", q2)
        self.assertIn(" NOT ", q3)
        self.assertIn('"survey"', q3)
        joined = " ".join((q1, q2, q3))
        for op in (" AND ", " OR ", " NOT "):
            self.assertIn(op, joined)
        self.assertNotIn(" NOT ", q1 + q2)

    def test_query_only_uses_cache_and_domain_aware_noise(self):
        out = t.suggest_boolean_queries("exercise bdnf")
        self.assertEqual((out["domain"], out["corpus_size"]), ("biomedical", 2))
        self.assertIn("older adults", out["emerging_terms"])
        q3 = out["suggestions"][2]["boolean_query"]
        self.assertEqual(q3, '(exercise AND bdnf) NOT ("mice" OR "rats" OR "in vitro" OR "animal model")')
        self.assertIn('"double-blind"', out["suggestions"][1]["boolean_query"])

        out = t.suggest_boolean_queries("graph neural networks")
        self.assertEqual(out["corpus_size"], 0)
        self.assertTrue(out["warnings"])
        self.assertEqual(len(out["suggestions"]), 3)

        for bad in ({}, {"query": "  ", "paper_ids": []}, {"paper_ids": "doi:10.1000/x"}):
            with self.assertRaises(c.HTTPError) as ctx:
                t.call("suggest_boolean_queries", bad)
            self.assertEqual(ctx.exception.code, 400)

    def test_cli_expand_and_run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["expand", "llm quantization", "--ids", ",".join(self.llm_ids), "--pretty"])
        text = buf.getvalue()
        self.assertEqual(code, 0)
        for n in ("[1] precision_deepening", "[2] synonym_and_method_expansion", "[3] noise_exclusion"):
            self.assertIn(n, text)

        expected = t.suggest_boolean_queries("llm quantization", self.llm_ids)["suggestions"][0]["boolean_query"]
        fake = {"papers": [], "sources": {}, "warnings": []}
        buf = io.StringIO()
        with mock.patch.object(c, "search", return_value=fake) as search, contextlib.redirect_stdout(buf):
            code = cli.main(["expand", "llm quantization", "--ids", ",".join(self.llm_ids), "--run", "1",
                             "--limit", "5"])
        data = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(search.call_args.args[0], expected)
        self.assertEqual(data["applied_suggestion"]["boolean_query"], expected)
        self.assertIn("search", data)


if __name__ == "__main__":
    unittest.main()
