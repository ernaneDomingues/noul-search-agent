"""citation_graph and related_papers: OpenAlex references and citing works, Semantic Scholar recommendations."""

import unittest
from unittest import mock

import _support


def setUpModule():
    global registry, cache
    _support.isolate_environment()
    from noul import registry
    from noul.net import cache


ANCHOR_ID = "doi:10.1000/anchor"


def _work(n, title, year=2021, cited=5, **extra):
    return {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/10.1000/w{n}",
        "title": title,
        "publication_year": year,
        "cited_by_count": cited,
        "type": "article",
        **extra,
    }


WORKS = {
    "anchor": _work(
        1,
        "Anchor paper on creatine",
        referenced_works=["https://openalex.org/W2"],
        related_works=["https://openalex.org/W4"],
    ),
    "reference": _work(2, "A randomized controlled trial of creatine", year=2015),
    "citing": _work(3, "A later cohort study of creatine", year=2024),
    "related": _work(4, "Creatine and cognition: a related study", year=2019),
}
RECOMMENDED = {
    "paperId": "a" * 40,
    "title": "Recommended paper about creatine",
    "year": 2023,
    "externalIds": {"DOI": "10.1000/rec"},
    "citationCount": 12,
}


def fake_http_json(url, *args, **kwargs):
    if "semanticscholar.org/recommendations" in url:
        return {"recommendedPapers": [RECOMMENDED, {"paperId": "b" * 40, "title": ""}]}
    if "/works/" in url:
        return WORKS["anchor"]
    if "cites%3AW1" in url:
        return {"results": [WORKS["citing"]]}
    if "openalex_id%3AW2" in url:
        return {"results": [WORKS["reference"]]}
    if "openalex_id%3AW4" in url:
        return {"results": [WORKS["related"]]}
    raise AssertionError(f"unexpected request: {url}")


class GraphTests(unittest.TestCase):
    def setUp(self):
        cache.cache_set(
            "paper:" + ANCHOR_ID,
            {"id": ANCHOR_ID, "title": "Anchor paper on creatine", "doi": "10.1000/anchor", "year": 2020},
        )
        patcher = mock.patch(_support.core_target("_http_json"), side_effect=fake_http_json)
        self.http = patcher.start()
        self.addCleanup(patcher.stop)

    def test_citation_graph_lists_references_and_citing_papers(self):
        out = registry.call("citation_graph", {"id": ANCHOR_ID})
        self.assertEqual(out["id"], ANCHOR_ID)
        self.assertEqual([p["doi"] for p in out["references"]], ["10.1000/w2"])
        self.assertEqual([p["doi"] for p in out["citing"]], ["10.1000/w3"])
        self.assertIn("references[].abstract", out["untrusted_fields"])
        # Every returned paper can be opened next without another request.
        self.assertEqual(cache.cache_get("paper:" + out["citing"][0]["id"])["year"], 2024)

    def test_related_papers_returns_the_three_relations(self):
        out = registry.call("related_papers", {"id": ANCHOR_ID})
        self.assertEqual(out["anchor_paper"]["id"], ANCHOR_ID)
        self.assertEqual(out["relation"], "all")
        self.assertEqual({p["doi"] for p in out["similar"]}, {"10.1000/rec", "10.1000/w4"})
        self.assertEqual([p["doi"] for p in out["references"]], ["10.1000/w2"])
        self.assertEqual([p["doi"] for p in out["citing"]], ["10.1000/w3"])

    def test_relation_and_filters_narrow_the_result(self):
        out = registry.call("related_papers", {"id": ANCHOR_ID, "relation": "references"})
        self.assertEqual((out["similar"], out["citing"]), ([], []))
        self.assertEqual(len(out["references"]), 1)
        self.assertFalse(any("recommendations" in call.args[0] for call in self.http.call_args_list))

        recent = registry.call("related_papers", {"id": ANCHOR_ID, "year_min": 2020})
        self.assertEqual(recent["references"], [])
        self.assertEqual([p["doi"] for p in recent["citing"]], ["10.1000/w3"])

        trials = registry.call("related_papers", {"id": ANCHOR_ID, "study_types": ["rct"]})
        self.assertEqual([p["doi"] for p in trials["references"]], ["10.1000/w2"])
        self.assertEqual(trials["citing"], [])

    def test_a_failing_source_leaves_that_list_empty(self):
        from noul.errors import HTTPError

        def failing(url, *args, **kwargs):
            if "semanticscholar.org" in url or "cites%3A" in url:
                raise HTTPError(502, "Network error (ConnectError)")  # what noul.net.http raises for a dead host
            return fake_http_json(url, *args, **kwargs)

        self.http.side_effect = failing
        out = registry.call("related_papers", {"id": ANCHOR_ID})
        self.assertEqual(out["citing"], [])
        self.assertEqual([p["doi"] for p in out["similar"]], ["10.1000/w4"])
        self.assertEqual([p["doi"] for p in out["references"]], ["10.1000/w2"])


if __name__ == "__main__":
    unittest.main()
