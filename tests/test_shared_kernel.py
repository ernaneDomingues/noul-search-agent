"""The helpers every layer shares: paper cache access, API endpoints, annotation and cached output schemas."""

import unittest
from unittest import mock

import _support


def setUpModule():
    global cache, endpoints, registry
    _support.isolate_environment()
    from noul import registry
    from noul.net import cache
    from noul.providers import endpoints


class PaperCacheTests(unittest.TestCase):
    def test_papers_are_read_and_written_by_id(self):
        cache.put_paper("doi:10.1000/k1", {"id": "doi:10.1000/k1", "title": "One"})
        self.assertEqual(cache.get_paper("doi:10.1000/k1")["title"], "One")
        self.assertEqual(cache.cache_get("paper:doi:10.1000/k1")["title"], "One")
        self.assertIsNone(cache.get_paper("doi:10.1000/missing"))

    def test_scan_matches_prefixes_literally_and_skips_what_is_not_a_record(self):
        cache.put_paper("scan-a", {"id": "scan-a", "title": "A"})
        cache.put_paper("scan-b", {"id": "scan-b", "title": "B"})
        cache.cache_set("paper:scan-list", ["not", "a", "record"])
        cache.cache_set("search:scan", {"id": "a-search"})
        cache.cache_set("paperXscan", {"id": "wildcard"})  # `_` and `%` in a prefix are not wildcards
        cache.cache_set("pap_r:scan", {"id": "underscore"})
        ids = [p["id"] for p in cache.scan(cache.PAPER) if p["id"].startswith("scan-")]
        self.assertEqual(sorted(ids), ["scan-a", "scan-b"])
        self.assertEqual([p["id"] for p in cache.scan("pap_r:")], ["underscore"])
        both = {p["id"] for p in cache.scan(cache.PAPER, "search:")}
        self.assertTrue({"scan-a", "a-search"} <= both)

    def test_scan_order(self):
        for n in (1, 2, 3):
            cache.cache_set(f"order:{n}", {"n": n})
        self.assertEqual([p["n"] for p in cache.scan("order:", newest_first=False)], [1, 2, 3])
        with mock.patch("time.time", return_value=2_000_000_000):
            for n in (4, 5):
                cache.cache_set(f"order:{n}", {"n": n})
        # Newest first; rows written in the same second keep the order they were written in.
        self.assertEqual([p["n"] for p in cache.scan("order:")][:2], [4, 5])
        self.assertEqual([p["n"] for p in cache.scan("order:", limit=2)], [4, 5])

    def test_an_unreadable_cache_is_a_miss_not_an_error(self):
        with mock.patch("noul.net.cache._conn", side_effect=OSError("locked")):
            self.assertIsNone(cache.get_paper("anything"))
            self.assertEqual(cache.scan(cache.PAPER), [])
            cache.put_paper("anything", {"id": "anything"})  # does not raise


class EndpointTests(unittest.TestCase):
    def test_openalex_work_urls(self):
        with mock.patch("noul.net.http._mailto_qs", return_value="?mailto=a%40b.org"):
            self.assertEqual(
                endpoints.openalex_work(doi="10.1000/a b"),
                "https://api.openalex.org/works/https://doi.org/10.1000%2Fa%20b?mailto=a%40b.org",
            )
            self.assertEqual(
                endpoints.openalex_work(openalex_id="W1"), "https://api.openalex.org/works/W1?mailto=a%40b.org"
            )
            self.assertEqual(
                endpoints.openalex_work(pmid=123), "https://api.openalex.org/works/pmid:123?mailto=a%40b.org"
            )

    def test_openalex_work_needs_an_identifier(self):
        with mock.patch("noul.net.http._mailto_qs", return_value=""):
            self.assertEqual(endpoints.openalex_work(openalex_id=""), "https://api.openalex.org/works/")
            with self.assertRaises(ValueError):
                endpoints.openalex_work()

    def test_s2_paper_urls_quote_the_identifier(self):
        self.assertEqual(
            endpoints.s2_paper("2410.12345v2", "title", prefix="ARXIV:", safe="/."),
            "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2410.12345v2?fields=title",
        )
        self.assertEqual(
            endpoints.s2_paper("a/b?c", "title"),
            "https://api.semanticscholar.org/graph/v1/paper/a%2Fb%3Fc?fields=title",
        )


class AnnotationTests(unittest.TestCase):
    def test_providers_do_not_import_the_search_engine(self):
        import importlib
        import inspect

        for name in ("openalex", "s2", "crossref", "pubmed", "europepmc", "arxiv_search", "annotate", "endpoints"):
            source = inspect.getsource(importlib.import_module(f"noul.providers.{name}"))
            with self.subTest(provider=name):
                self.assertNotRegex(source, r"(?m)^\s*(from|import) noul\.search")

    def test_a_parsed_paper_gets_its_domain(self):
        from noul.providers import annotate

        paper = annotate.annotate_paper({"id": "x", "title": "Inflation targeting and monetary policy", "abstract": ""})
        self.assertIn("domain", paper)

    def test_enrichment_never_breaks_a_parser(self):
        from noul.providers import annotate

        broken = mock.Mock()
        broken.resolve_domain.side_effect = RuntimeError("boom")
        broken.scan_artifacts_from_paper.side_effect = RuntimeError("boom")
        with mock.patch.object(annotate, "_provider", return_value=broken):
            self.assertEqual(annotate.annotate_paper({"id": "x", "title": "T"}), {"id": "x", "title": "T"})


class OutputSchemaTests(unittest.TestCase):
    def test_each_caller_gets_its_own_copy(self):
        first = registry.output_schema("get_fulltext")
        first["properties"].clear()
        self.assertTrue(registry.output_schema("get_fulltext")["properties"])
        self.assertIsNone(registry.output_schema("search_papers"))


if __name__ == "__main__":
    unittest.main()
