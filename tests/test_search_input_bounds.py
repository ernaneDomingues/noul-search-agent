"""Search inputs are bounded: a repeated source runs once, and lists and free text have a maximum size."""

import unittest
from unittest import mock

import _support


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class SearchInputBoundsTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def test_a_repeated_source_is_searched_once(self):
        from noul.search import engine

        with mock.patch("noul.providers.pubmed._search_pubmed", return_value=[]) as pubmed:
            engine.search("creatine memory unique-query-1", sources=["pubmed"] * 50)
        self.assertEqual(pubmed.call_count, 1)

    def test_the_tool_refuses_oversized_lists_and_text(self):
        from noul import registry
        from noul.errors import HTTPError

        cases = [
            ("search_papers", {"query": "q", "sources": ["pubmed"] * 7}),
            ("search_papers", {"query": "q", "study_types": ["rct"] * 9}),
            ("search_papers", {"query": "q" * 2001}),
            ("clarify_search_scope", {"query": "q" * 2001}),
            ("suggest_boolean_queries", {"query": "q" * 2001}),
            ("get_fulltext", {"id": "doi:10.1000/x", "question": "q" * 2001}),
            ("query_fulltexts", {"ids": ["doi:10.1000/x"], "question": "q" * 2001}),
        ]
        for name, args in cases:
            with self.subTest(tool=name, args={k: (v[:12] if isinstance(v, str) else v[:2]) for k, v in args.items()}):
                with self.assertRaises(HTTPError) as caught:
                    registry.call(name, args)
                self.assertEqual(caught.exception.code, 400)

    def test_the_limits_are_advertised_in_the_schema(self):
        from noul import registry

        props = registry.input_schema("search_papers")["properties"]
        self.assertEqual((props["sources"]["maxItems"], props["study_types"]["maxItems"]), (6, 8))
        self.assertEqual(props["query"]["maxLength"], 2000)


if __name__ == "__main__":
    unittest.main()
