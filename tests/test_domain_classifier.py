"""Etapa 1 (#19): scientific-domain detection, pipeline integration, CLI/MCP overrides."""
import contextlib
import io
import json
import unittest
from unittest import mock

import _support

from noul.providers import domain_classifier as dc
from noul.schemas.domain import ScientificDomain as D
from noul.schemas.paper import UnifiedPaper


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class ArxivTaxonomyTests(unittest.TestCase):
    def test_prefix_mapping(self):
        cases = {
            "cs.CL": D.COMPUTER_SCIENCE, "stat.ML": D.COMPUTER_SCIENCE, "math.OC": D.COMPUTER_SCIENCE,
            "eess.IV": D.COMPUTER_SCIENCE, "q-bio.NC": D.BIOMEDICAL, "econ.EM": D.ECONOMICS,
            "q-fin.PM": D.ECONOMICS, "physics.optics": D.GENERAL_STEM, "math.AG": D.GENERAL_STEM,
            "hep-th": D.GENERAL_STEM, "cond-mat.str-el": D.GENERAL_STEM, "quant-ph": D.GENERAL_STEM,
        }
        for cat, expected in cases.items():
            with self.subTest(cat=cat):
                self.assertEqual(dc.resolve_domain(categories=[cat]), expected)

    def test_primary_category_outweighs_cross_lists_and_beats_text(self):
        paper = {"title": "Randomized placebo trial of patients", "primary_category": "cs.LG",
                 "categories": ["cs.LG", "q-bio.QM"]}
        self.assertEqual(dc.resolve_domain(paper=paper), D.COMPUTER_SCIENCE)


class MetadataAndTextTests(unittest.TestCase):
    def test_fields_of_study_and_pubmed_signals(self):
        self.assertEqual(dc.resolve_domain(paper={"fields_of_study": ["Computer Science"], "pmid": "1"}),
                         D.COMPUTER_SCIENCE)
        self.assertEqual(dc.resolve_domain(paper={"fields_of_study": ["Economics, Econometrics and Finance"]}),
                         D.ECONOMICS)
        self.assertEqual(dc.resolve_domain(paper={"fields_of_study": ["Psychology"]}), D.BIOMEDICAL)
        self.assertEqual(dc.resolve_domain(paper={"title": "A study", "pmid": "123"}), D.BIOMEDICAL)
        self.assertEqual(dc.resolve_domain(paper={"title": "A study", "sources": ["europepmc"]}), D.BIOMEDICAL)
        strong_cs = {"title": "Transformer quantization on GPU", "abstract": "LLM fine-tuning benchmark", "pmid": "9"}
        self.assertEqual(dc.resolve_domain(paper=strong_cs), D.COMPUTER_SCIENCE)

    def test_semantic_fallback_for_uncategorized_pdfs(self):
        cases = [
            ("Efficient attention for large language models",
             "We fine-tune a transformer and report GPU memory and benchmark results.", D.COMPUTER_SCIENCE),
            ("Effects of a new drug", "A randomized placebo-controlled clinical trial in 300 patients; "
             "mortality and adverse events were recorded.", D.BIOMEDICAL),
            ("Minimum wages and employment", "Using difference-in-differences and panel data we estimate the "
             "elasticity of labour demand.", D.ECONOMICS),
            ("Spectral properties of crystalline lattices", "We study phonon modes in a lattice.", D.GENERAL_STEM),
        ]
        for title, abstract, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(dc.resolve_domain(title=title, abstract=abstract), expected)

    def test_threshold_requires_two_matches_or_a_title_hit(self):
        self.assertEqual(dc.resolve_domain(title="On lattices", abstract="An efficient algorithm."), D.GENERAL_STEM)
        self.assertEqual(dc.resolve_domain(title="A new compiler", abstract=""), D.COMPUTER_SCIENCE)

    def test_accepts_unified_paper(self):
        paper = UnifiedPaper(id="x", title="Monetary policy and inflation expectations")
        self.assertEqual(dc.resolve_domain(paper=paper), D.ECONOMICS)


class OverrideTests(unittest.TestCase):
    def test_aliases(self):
        for alias, expected in (("cs", D.COMPUTER_SCIENCE), ("Computer_Science", D.COMPUTER_SCIENCE),
                                ("bio", D.BIOMEDICAL), ("med", D.BIOMEDICAL), ("econ", D.ECONOMICS),
                                ("stem", D.GENERAL_STEM), ("general_stem", D.GENERAL_STEM)):
            with self.subTest(alias=alias):
                self.assertEqual(dc.resolve_domain(categories=["q-bio.NC"], override_domain=alias), expected)

    def test_invalid_override(self):
        with self.assertRaises(ValueError):
            dc.resolve_domain(override_domain="astrology")


PAPER = {
    "id": "arxiv:2401.99999", "title": "Mixture of experts routing", "authors": ["A. B."], "year": 2024,
    "journal": "arXiv", "doi": None, "arxiv_id": "2401.99999", "citations": 3, "abstract": "Routing tokens.",
    "evidence": "empirical", "open_access": True, "sources": ["arxiv"], "primary_category": "cs.LG",
    "categories": ["cs.LG"], "domain": "computer_science",
}


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        c.cache_set("paper:" + PAPER["id"], PAPER)

    def test_arxiv_parser_extracts_categories_and_domain(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry><id>http://arxiv.org/abs/2402.00001v1</id><title>Neural decoding of spikes</title>
          <summary>We decode signals.</summary><published>2024-02-01T00:00:00Z</published>
          <author><name>Ana Lima</name></author>
          <arxiv:primary_category term="q-bio.NC"/><category term="q-bio.NC"/><category term="cs.LG"/></entry></feed>"""
        with mock.patch.object(c, "_http_get", return_value=atom):
            [paper] = c._search_arxiv("neural decoding")
        self.assertEqual(paper["primary_category"], "q-bio.NC")
        self.assertEqual(paper["categories"], ["q-bio.NC", "cs.LG"])
        self.assertEqual(paper["domain"], "biomedical")

    def test_openalex_s2_and_merge_annotate_domain(self):
        work = {"id": "https://openalex.org/W1", "title": "Inflation targeting", "publication_year": 2020,
                "primary_topic": {"field": {"display_name": "Economics, Econometrics and Finance"},
                                  "domain": {"display_name": "Social Sciences"},
                                  "subfield": {"display_name": "Finance"}}}
        oa = c._parse_openalex_work(work)
        self.assertEqual((oa["domain"], oa["primary_category"]), ("economics", "Finance"))
        s2 = c._parse_s2_paper({"paperId": "abc", "title": "Inflation targeting", "fieldsOfStudy": ["Economics"]})
        self.assertEqual(s2["domain"], "economics")
        merged = c._merge_papers([oa, s2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["domain"], "economics")
        self.assertIn("Economics", merged[0]["fields_of_study"])

    def test_search_papers_domain_filter_and_slim(self):
        bio = {**PAPER, "id": "pmid:1", "title": "Placebo trial", "pmid": "1", "domain": "biomedical"}
        fake = {"papers": [PAPER, bio], "sources": ["arxiv"], "warnings": []}
        with mock.patch.object(t.c, "search", return_value=fake):
            out = t.search_papers("routing", domain="cs")
            self.assertEqual([p["id"] for p in out["papers"]], [PAPER["id"]])
            self.assertEqual((out["domain"], out["papers"][0]["domain"]), ("computer_science", "computer_science"))
            self.assertEqual(t.search_papers("routing")["total_found"], 2)
            with self.assertRaises(c.HTTPError) as ctx:
                t.search_papers("routing", domain="astrology")
            self.assertEqual(ctx.exception.code, 400)

    def test_mcp_override_and_detect_tool(self):
        self.assertEqual(t.call("get_paper", {"id": PAPER["id"]})["domain"], "computer_science")
        forced = t.call("get_paper", {"id": PAPER["id"], "domain": "econ"})
        self.assertEqual((forced["domain"], forced["domain_overridden"]), ("economics", True))
        detected = t.call("detect_paper_domain", {"paper_id": PAPER["id"]})
        self.assertEqual((detected["domain"], detected["method"]), ("computer_science", "categories"))
        self.assertEqual(t.call("detect_paper_domain", {"paper_id": PAPER["id"], "domain": "bio"})["domain"],
                         "biomedical")

    def test_cli_domain_override(self):
        code, out = run_cli("paper", PAPER["id"], "--domain", "bio")
        self.assertEqual((code, json.loads(out)["domain"]), (0, "biomedical"))
        code, out = run_cli("domain", PAPER["id"])
        self.assertEqual((code, json.loads(out)["domain"]), (0, "computer_science"))
        with mock.patch.object(t.c, "search", return_value={"papers": [PAPER], "sources": [], "warnings": []}):
            code, out = run_cli("search", "routing", "--domain", "econ")
        self.assertEqual((code, json.loads(out)["total_found"]), (0, 0))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_cli("paper", PAPER["id"], "--domain", "astrology")[0], 1)


if __name__ == "__main__":
    unittest.main()
