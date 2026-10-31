"""Etapa 4 (#01): clarification loop — broad-query heuristic, domain questions, refined query, MCP and CLI."""

import contextlib
import datetime
import io
import json
import unittest
from unittest import mock

import _support
from noul.prompts import clarification as cl

THIS_YEAR = datetime.date.today().year


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


FAKE_SEARCH = {"papers": [], "sources": ["openalex"], "warnings": []}


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def run_cli(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        mock.patch.object(cli.sys, "stdin", stdin if stdin is not None else io.StringIO("")),
    ):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class HeuristicTests(unittest.TestCase):
    def test_broad_vs_specific(self):
        for q in ("quantização de LLMs", "LLM reasoning", "creatina", "inflação no Brasil", "LLM quantization"):
            with self.subTest(q=q):
                self.assertTrue(cl.needs_clarification(q)["needs_clarification"])
        for q in (
            "QLoRA 4-bit quantization Llama-2 MMLU benchmark 2023",
            "creatine supplementation working memory healthy adults rct",
            "QLoRA 4-bit quantization Llama-3 MMLU latency",
            '"minimum wage" AND employment since 2015',
        ):
            with self.subTest(q=q):
                self.assertFalse(cl.needs_clarification(q)["needs_clarification"])

    def test_criteria_detail(self):
        r = cl.needs_clarification("QLoRA 4-bit quantization Llama-2 MMLU benchmark 2023")
        self.assertEqual(
            r["criteria"],
            {
                "significant_words": True,
                "boolean_or_phrase": False,
                "temporal_constraint": True,
                "method_and_outcome": True,
            },
        )
        # One criterion alone is not enough.
        self.assertTrue(cl.needs_clarification("LLM papers since 2022")["needs_clarification"])


class QuestionTests(unittest.TestCase):
    def test_questions_per_domain(self):
        expected = {
            "quantização de LLMs": ("computer_science", "Benchmarks e avaliação empírica"),
            "creatina": ("biomedical", "Ensaios Clínicos Randomizados (RCTs)"),
            "inflação no Brasil": ("economics", "Identificação causal (Diff-in-Diff, IV, RDD)"),
            "crystal lattice": ("general_stem", "Incluir preprints recentes (arXiv)"),
        }
        for query, (domain, some_option) in expected.items():
            with self.subTest(query=query):
                out = cl.generate_clarification_questions(query)
                self.assertEqual(out["domain"], domain)
                self.assertEqual([q["id"] for q in out["questions"]], ["q1", "q2", "q3"])
                self.assertTrue(all(2 <= len(q["options"]) <= 4 and q["question"] for q in out["questions"]))
                self.assertIn(some_option, [o for q in out["questions"] for o in q["options"]])

    def test_domain_override(self):
        self.assertEqual(cl.generate_clarification_questions("creatina", domain="econ")["domain"], "economics")
        with self.assertRaises(ValueError):
            cl.generate_clarification_questions("creatina", domain="astrology")


class RefinedQueryTests(unittest.TestCase):
    def test_biomedical_filters(self):
        out = cl.build_refined_query("creatina", {"q1": "Adultos saudáveis", "q2": 2, "q3": "Últimos 5 anos"})
        self.assertEqual(out["refined_query"], "creatina healthy adults randomized controlled trial")
        f = out["recommended_filters"]
        self.assertEqual((f["year_min"], f["study_types"], f["domain"]), (THIS_YEAR - 5, ["rct"], "biomedical"))
        self.assertIn("pubmed", f["sources"])

    def test_cs_and_flexible_answers(self):
        out = cl.build_refined_query(
            "LLM quantization", {"focus": "eficiência", "q2": "1", "q3": "seminais", "extra": "ignored key"}
        )
        self.assertEqual(out["refined_query"], "LLM quantization efficiency latency memory consumer GPU edge")
        self.assertEqual(
            (out["recommended_filters"]["sort"], out["recommended_filters"]["year_min"]), ("citations", None)
        )
        out = cl.build_refined_query("LLM quantization", {"q1": "speculative decoding", "q2": 9})
        self.assertEqual(out["refined_query"], "LLM quantization speculative decoding")
        self.assertEqual(out["ignored_answers"], ["q2"])

    def test_meta_analysis_and_peer_review_filters(self):
        f = cl.build_refined_query("creatina", {"q2": 1})["recommended_filters"]
        self.assertEqual(f["study_types"], ["meta-analysis", "systematic-review"])
        f = cl.build_refined_query("crystal lattice", {"q2": 1, "q3": 1})["recommended_filters"]
        self.assertEqual((f["sources"], f["year_min"]), (["openalex", "s2", "crossref"], THIS_YEAR - 3))


class ToolAndCliTests(unittest.TestCase):
    def test_mcp_tool(self):
        out = t.call("clarify_search_scope", {"query": "creatina"})
        self.assertTrue(out["needs_clarification"])
        self.assertEqual(len(out["questions"]), 3)
        self.assertNotIn("refined_query", out)
        out = t.call("clarify_search_scope", {"query": "creatina", "answers": {"q2": 2}})
        self.assertEqual(out["recommended_filters"]["study_types"], ["rct"])
        for args in ({"query": "x", "domain": "astrology"}, {"query": "  "}, {"query": "x", "answers": ["q1"]}):
            with self.subTest(args=args), self.assertRaises(c.HTTPError) as ctx:
                t.call("clarify_search_scope", args)
            self.assertEqual(ctx.exception.code, 400)

    def test_search_papers_clarify_flag(self):
        with mock.patch(_support.core_target("search"), return_value=FAKE_SEARCH):
            broad = t.call("search_papers", {"query": "LLM quantization", "clarify": True})
            specific = t.call(
                "search_papers", {"query": "QLoRA 4-bit quantization Llama-2 MMLU benchmark 2023", "clarify": True}
            )
            default = t.call("search_papers", {"query": "LLM quantization"})
        self.assertEqual(len(broad["clarification_suggested"]["questions"]), 3)
        self.assertNotIn("clarification_suggested", specific)
        self.assertNotIn("clarification_suggested", default)

    def test_cli_skip_flags_never_prompt(self):
        for flag in ("--skip-clarification", "--skip-clarify"):
            with self.subTest(flag=flag), mock.patch(_support.core_target("search"), return_value=FAKE_SEARCH) as s:
                tty = FakeTTY("1\n")
                code, out, err = run_cli("search", "LLM quantization", flag, "--interactive", stdin=tty)
                self.assertEqual((code, json.loads(out)["query"]), (0, "LLM quantization"))
                self.assertEqual(s.call_args.args[0], "LLM quantization")
                self.assertEqual(tty.tell(), 0)  # stdin untouched
                self.assertEqual(err, "")

    def test_cli_interactive_non_tty_does_not_block(self):
        with mock.patch(_support.core_target("search"), return_value=FAKE_SEARCH):
            code, out, err = run_cli("search", "LLM quantization", "--interactive", stdin=io.StringIO("1\n"))
        self.assertEqual((code, json.loads(out)["query"]), (0, "LLM quantization"))
        self.assertIn("not a terminal", err)

    def test_cli_interactive_tty_refines_search(self):
        with mock.patch(_support.core_target("search"), return_value=FAKE_SEARCH) as s:
            code, out, err = run_cli(
                "search", "creatina", "--interactive", "--year-min", "2015", stdin=FakeTTY("1\n2\n\n")
            )
        self.assertEqual(code, 0)
        self.assertIn("1. Adultos saudáveis", err)
        res = json.loads(out)
        self.assertEqual(res["query"], "creatina healthy adults randomized controlled trial")
        query, _mode, _unused, filters, _lang, sources = s.call_args.args
        self.assertEqual((filters["year_min"], filters["study_types"]), (2015, ["rct"]))  # CLI flag wins
        self.assertIn("pubmed", sources)
        self.assertEqual(res["clarification"]["applied_answers"][1]["answer"], "Ensaios Clínicos Randomizados (RCTs)")

    def test_cli_clarify_subcommand(self):
        code, out, _ = run_cli("clarify", "inflação no Brasil", "--answers", '{"q1": 1, "q3": "Pós-2020"}')
        res = json.loads(out)
        self.assertEqual((code, res["domain"], res["recommended_filters"]["year_min"]), (0, "economics", 2020))
        self.assertIn("difference-in-differences", res["refined_query"])
        code, _, err = run_cli("clarify", "x", "--answers", "{bad")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
