"""The paper record: one builder for every source, and lookups that tell "missing" from "source unavailable"."""

import typing
import unittest
from unittest import mock

import _support


def setUpModule():
    global assemble, engine, errors, registry
    _support.isolate_environment()
    from noul import errors, registry
    from noul.providers import assemble
    from noul.search import engine


class AssemblePaperTests(unittest.TestCase):
    def test_derived_fields_are_the_same_for_every_source(self):
        paper = assemble.assemble_paper(
            "crossref",
            title="A randomized controlled trial of creatine",
            abstract="Participants were randomized. Code: https://github.com/a/b",
            authors=["A B"],
            year=2020,
            journal="The Lancet",
            citations=300,
            ids={"doi": "10.1000/abc"},
            mesh_terms=["Creatine"],
        )
        self.assertEqual(paper["id"], "doi:10.1000_abc")
        self.assertEqual(paper["evidence"], "rct")
        self.assertEqual(paper["sources"], ["crossref"])
        self.assertEqual(paper["mesh_terms"], ["Creatine"])
        self.assertTrue(paper["has_code"])
        for key in ("impact", "tier", "domain", "citations", "open_access", "pdf_url"):
            self.assertIn(key, paper)

    def test_only_the_identifiers_a_source_knows_are_stored(self):
        paper = assemble.assemble_paper("arxiv", title="T", ids={"doi": None, "arxiv_id": "2410.12345"})
        self.assertEqual(paper["id"], "arxiv:2410.12345")
        self.assertIn("doi", paper)
        self.assertNotIn("pmid", paper)
        untitled = assemble.assemble_paper("s2", title="Only a title")
        self.assertTrue(untitled["id"].startswith("id:"))

    def test_the_author_list_is_capped_once(self):
        many = [f"Author {i}" for i in range(250)]
        self.assertEqual(len(assemble.assemble_paper("pubmed", title="T", authors=many)["authors"]), 100)

    def test_every_key_a_parser_produces_is_declared(self):
        from noul.providers import crossref, s2
        from noul.schemas.paper import Paper

        declared = set(typing.get_type_hints(Paper))
        item = {"paperId": "a" * 40, "title": "T", "externalIds": {"ArXiv": "2410.1"}, "fieldsOfStudy": ["CS"]}
        with mock.patch(
            _support.core_target("_http_json"),
            return_value={"message": {"items": [{"title": ["C"], "DOI": "10.1/c", "subject": ["X"]}]}},
        ):
            papers = [s2._parse_s2_paper(item), *crossref._search_crossref("q")]
        for paper in papers:
            self.assertEqual(set(paper) - declared, set())

    def test_sources_with_more_than_ten_authors_keep_them(self):
        from noul.providers import crossref

        authors = [{"given": f"G{i}", "family": "F"} for i in range(12)]
        with mock.patch(
            _support.core_target("_http_json"),
            return_value={"message": {"items": [{"title": ["Many hands"], "DOI": "10.1/m", "author": authors}]}},
        ):
            (paper,) = crossref._search_crossref("q")
        self.assertEqual(len(paper["authors"]), 12)


class ResolvePaperTests(unittest.TestCase):
    def lookup(self, error):
        with (
            mock.patch(_support.core_target("_http_json"), side_effect=error),
            self.assertRaises(errors.HTTPError) as caught,
        ):
            engine.resolve_paper("10.1000/never-cached")
        return caught.exception

    def test_a_missing_paper_is_404(self):
        self.assertEqual(self.lookup(errors.HTTPError(404, "HTTP Error 404: Not Found")).code, 404)
        self.assertEqual(self.lookup(ValueError("bad payload")).code, 404)

    def test_a_rate_limited_or_failing_source_is_not_reported_as_missing(self):
        limited = self.lookup(errors.HTTPError(429, "HTTP Error 429: Too Many Requests"))
        self.assertEqual(limited.code, 429)
        self.assertIn("try again", limited.message)
        self.assertEqual(self.lookup(errors.HTTPError(502, "bad gateway")).code, 503)

    def test_tools_report_the_difference(self):
        with (
            mock.patch(_support.core_target("_http_json"), side_effect=errors.HTTPError(429, "limited")),
            self.assertRaises(errors.HTTPError) as caught,
        ):
            registry.call("get_paper", {"id": "10.1000/also-never-cached"})
        self.assertEqual(caught.exception.code, 429)


class UnavailableSourceTests(unittest.TestCase):
    """Tools that take several ids keep "not found" apart from "the source did not answer"."""

    def setUp(self):
        from noul.net import cache

        cache.put_paper("doi:10.1000/known", {"id": "doi:10.1000/known", "title": "Known", "doi": "10.1000/known"})

        def answer(url, *args, **kwargs):
            raise errors.HTTPError(429 if "limited" in url else 404, "upstream")

        patcher = mock.patch(_support.core_target("_http_json"), side_effect=answer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_project_add_papers(self):
        slug = registry.call("project_create", {"title": "Unavailable sources"})["slug"]
        out = registry.call(
            "project_add_papers",
            {"project": slug, "ids": ["doi:10.1000/known", "10.1000/limited", "10.1000/absent"]},
        )
        self.assertEqual(out["added"], ["doi:10.1000/known"])
        self.assertEqual(out["unavailable"], ["10.1000/limited"])
        self.assertEqual(out["not_found"], ["10.1000/absent"])

    def test_export_papers(self):
        out = registry.call("export_papers", {"ids": ["doi:10.1000/known", "10.1000/limited", "10.1000/absent"]})
        text = " | ".join(out.warnings)
        self.assertIn("could not be found: 10.1000/absent", text)
        self.assertIn("did not answer (try again): 10.1000/limited", text)
        with self.assertRaises(errors.HTTPError) as caught:
            registry.call("export_papers", {"ids": ["10.1000/limited"]})
        self.assertEqual(caught.exception.code, 503)
        with self.assertRaises(errors.HTTPError) as caught:
            registry.call("export_papers", {"ids": ["10.1000/absent"]})
        self.assertEqual(caught.exception.code, 404)

    def test_save_research_dossier_names_the_papers_it_left_out(self):
        out = registry.call(
            "save_research_dossier",
            {"topic": "Left out", "synthesis_markdown": "x", "paper_ids": ["doi:10.1000/known", "10.1000/limited"]},
        )
        self.assertEqual((out["paper_count"], out["papers_left_out"]), (1, ["10.1000/limited"]))


class SearchSignatureTests(unittest.TestCase):
    def test_search_takes_query_filters_and_sources(self):
        import inspect

        self.assertEqual(list(inspect.signature(engine.search).parameters), ["query", "filters", "sources"])


if __name__ == "__main__":
    unittest.main()
