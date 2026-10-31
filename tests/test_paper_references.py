"""Etapa 3 (#03): get_paper_references — S2 graph, sorting, chained cache, OpenAlex fallback on 429."""

import contextlib
import io
import json
import unittest
import urllib.error
from unittest import mock

import _support


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


ANCHOR = {
    "id": "doi:10.1000/anchor",
    "title": "Anchor paper",
    "authors": ["A"],
    "year": 2020,
    "journal": "J",
    "doi": "10.1000/anchor",
    "citations": 10,
    "abstract": "",
    "evidence": "empirical",
    "open_access": False,
    "sources": ["openalex"],
    "openalex_id": "W1",
}


def s2(pid, title, year, cites, doi=None):
    return {
        "paperId": pid,
        "title": title,
        "year": year,
        "citationCount": cites,
        "venue": "V",
        "authors": [{"name": "X Y"}],
        "externalIds": {"DOI": doi} if doi else {},
    }


REFS = {
    "data": [
        {"isInfluential": False, "citedPaper": s2("a" * 40, "Old classic", 1990, 5000)},
        {"isInfluential": True, "citedPaper": s2("b" * 40, "Key method", 2015, 300, doi="10.1000/key")},
        {"isInfluential": True, "citedPaper": s2("c" * 40, "Recent key", 2019, 40)},
        {"isInfluential": False, "citedPaper": {"paperId": None, "title": None}},
    ]
}
CITS = {
    "data": [
        {"isInfluential": False, "citingPaper": s2("d" * 40, "Follow up", 2023, 2)},
        {"isInfluential": True, "citingPaper": s2("e" * 40, "Extension", 2021, 90)},
    ]
}


def s2_router(url, headers=None, timeout=15):
    if "/references?" in url:
        return REFS
    if "/citations?" in url:
        return CITS
    raise AssertionError(url)


class ReferencesTests(unittest.TestCase):
    def setUp(self):
        c.cache_set("paper:" + ANCHOR["id"], dict(ANCHOR))

    def refs(self, **kw):
        with mock.patch.object(c, "_http_json", side_effect=s2_router) as m:
            out = t.call("get_paper_references", {"paper_id": ANCHOR["id"], **kw})
        return out, m

    def test_both_directions_with_chainable_ids(self):
        out, m = self.refs()
        self.assertEqual(out["source"], "semantic_scholar")
        self.assertIn("/paper/DOI:10.1000/anchor/references?", m.call_args_list[0].args[0])
        self.assertIn("isInfluential", m.call_args_list[0].args[0])
        back, fwd = out["backward_citations"], out["forward_citations"]
        self.assertEqual([r["title"] for r in back], ["Key method", "Recent key", "Old classic"])
        self.assertEqual([r["title"] for r in fwd], ["Extension", "Follow up"])
        first = back[0]
        self.assertEqual(
            (first["paper_id"], first["year"], first["citation_count"], first["is_influential"]),
            (c._paper_id(doi="10.1000/key"), 2015, 300, True),
        )
        self.assertEqual(first["url"], "https://doi.org/10.1000/key")
        # Chained navigation: the reference is cached and works with the other tools.
        self.assertEqual(t.call("get_paper", {"id": first["paper_id"]})["title"], "Key method")

    def test_sorting_limit_and_direction(self):
        out, _ = self.refs(sort_by="citations", direction="backward")
        self.assertEqual([r["citation_count"] for r in out["backward_citations"]], [5000, 300, 40])
        self.assertNotIn("forward_citations", out)
        out, _ = self.refs(sort_by="year", direction="forward", limit=1)
        self.assertEqual([r["year"] for r in out["forward_citations"]], [2023])
        self.assertEqual(out["total_forward"], 2)

    def test_invalid_arguments(self):
        for kw in ({"limit": 0}, {"limit": 51}, {"direction": "sideways"}, {"sort_by": "random"}):
            with self.subTest(kw=kw), self.assertRaises(c.HTTPError) as ctx:
                t.call("get_paper_references", {"paper_id": ANCHOR["id"], **kw})
            self.assertEqual(ctx.exception.code, 400)

    def test_chained_cache_does_not_overwrite_richer_record(self):
        rich = {
            "id": c._paper_id(doi="10.1000/key"),
            "title": "Key method",
            "abstract": "Full abstract",
            "doi": "10.1000/key",
            "citations": 300,
            "journal": "Nature",
            "sources": ["openalex"],
        }
        c.cache_set("paper:" + rich["id"], rich)
        self.refs()
        cached = c.cache_get("paper:" + rich["id"])
        self.assertEqual((cached["abstract"], cached["journal"]), ("Full abstract", "Nature"))
        self.assertEqual(cached["s2_id"], "b" * 40)
        self.assertEqual(set(cached["sources"]), {"openalex", "s2"})

    def test_openalex_fallback_on_429(self):
        def boom(url, headers=None, timeout=15):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        graph = {
            "id": ANCHOR["id"],
            "title": "Anchor",
            "references": [
                {**ANCHOR, "id": "oa:W2", "title": "Minor", "citations": 49, "year": 2010, "doi": None},
                {**ANCHOR, "id": "oa:W3", "title": "Major", "citations": 50, "year": 2005, "doi": None},
            ],
            "citing": [{**ANCHOR, "id": "oa:W4", "title": "Citer", "citations": 1, "year": 2024, "doi": None}],
        }
        with (
            mock.patch.object(c, "_http_json", side_effect=boom),
            mock.patch.object(c, "citation_graph", return_value=graph) as cg,
        ):
            out = t.get_paper_references(ANCHOR["id"])
        cg.assert_called_once_with(ANCHOR["id"])
        self.assertEqual(out["source"], "openalex")
        self.assertIn("HTTP 429", out["warnings"][0])
        self.assertEqual(
            [(r["title"], r["is_influential"]) for r in out["backward_citations"]], [("Major", True), ("Minor", False)]
        )
        self.assertEqual(out["forward_citations"][0]["paper_id"], "oa:W4")
        self.assertIsNotNone(c.cache_get("paper:oa:W3"))

    def test_cli(self):
        buf = io.StringIO()
        with mock.patch.object(c, "_http_json", side_effect=s2_router), contextlib.redirect_stdout(buf):
            code = cli.main(["references", ANCHOR["id"], "--direction", "backward", "--sort", "year", "--limit", "2"])
        out = json.loads(buf.getvalue())
        self.assertEqual((code, [r["year"] for r in out["backward_citations"]]), (0, [2019, 2015]))


if __name__ == "__main__":
    unittest.main()
