"""PubMed (E-utilities) and OpenAlex providers: request building and record parsing, offline."""

import unittest
import urllib.parse
from unittest import mock

import _support


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>31000001</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><MedlineDate>2019 Nov-Dec</MedlineDate></PubDate></JournalIssue>
          <Title>The   Lancet</Title>
        </Journal>
        <ArticleTitle>Creatine and <i>memory</i>: a randomized trial</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Creatine may help cognition.</AbstractText>
          <AbstractText Label="RESULTS">Memory improved.</AbstractText>
          <AbstractText></AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>Alice</ForeName></Author>
          <Author><LastName>Jones</LastName><Initials>B</Initials></Author>
          <Author><CollectiveName>Trial Group</CollectiveName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Randomized Controlled Trial</PublicationType>
          <PublicationType>Retracted Publication</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Creatine</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName>Memory</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName>Creatine</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1000/CREATINE.1</ArticleId>
        <ArticleId IdType="pmc">PMC123456</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>31000002</PMID>
      <Article><Journal><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal></Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>31000003</PMID>
      <Article>
        <Journal><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue><Title>BMJ</Title></Journal>
        <ArticleTitle>Cohort study of sleep</ArticleTitle>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class PubmedParsingTests(unittest.TestCase):
    def test_full_record(self):
        from noul.providers import pubmed

        papers = pubmed._parse_pubmed_xml(PUBMED_XML)
        self.assertEqual(len(papers), 2, "the article without a title is skipped")
        p = papers[0]
        self.assertEqual(p["title"], "Creatine and memory: a randomized trial")
        self.assertEqual(p["abstract"], "BACKGROUND: Creatine may help cognition. RESULTS: Memory improved.")
        self.assertEqual((p["year"], p["journal"]), (2019, "The Lancet"))
        self.assertEqual(p["authors"], ["Alice Smith", "B Jones"])
        self.assertEqual((p["doi"], p["pmid"], p["pmc_id"]), ("10.1000/creatine.1", "31000001", "PMC123456"))
        self.assertTrue(p["open_access"])
        self.assertIn("PMC123456", p["pdf_url"])
        self.assertEqual(p["mesh_terms"], ["Creatine", "Memory"])
        self.assertEqual(p["evidence"], "rct")
        self.assertEqual(p["retraction"], {"is_retracted": True, "kind": "retraction", "source": "pubmed"})
        self.assertEqual(p["sources"], ["pubmed"])

    def test_minimal_record(self):
        from noul.providers import pubmed

        p = pubmed._parse_pubmed_xml(PUBMED_XML)[1]
        self.assertEqual(
            (p["title"], p["year"], p["journal"], p["abstract"]), ("Cohort study of sleep", 2021, "BMJ", "")
        )
        self.assertEqual((p["pmid"], p["doi"], p["pmc_id"], p["pdf_url"]), ("31000003", None, None, None))
        self.assertFalse(p["open_access"])
        self.assertNotIn("retraction", p)


class PubmedSearchTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def test_esearch_then_efetch_with_the_contact_and_the_year(self):
        from noul.providers import pubmed

        with (
            mock.patch("noul.config._email", return_value="lab@example.org"),
            mock.patch("noul.net.http._http_json", return_value={"esearchresult": {"idlist": ["1", "2"]}}) as js,
            mock.patch("noul.net.http._http_get", return_value=PUBMED_XML) as get,
        ):
            papers = pubmed._search_pubmed("creatine memory", limit=50, year_min=2015)
        search = _query(js.call_args.args[0])
        self.assertEqual(search["term"], "(creatine memory) AND (2015:3000[pdat])")
        self.assertEqual((search["db"], search["retmax"], search["sort"]), ("pubmed", "30", "relevance"))
        self.assertEqual((search["tool"], search["email"]), ("noul-search-agent", "lab@example.org"))
        fetch = _query(get.call_args.args[0])
        self.assertEqual((fetch["id"], fetch["retmode"]), ("1,2", "xml"))
        self.assertEqual(len(papers), 2)

    def test_no_hits_means_no_efetch_and_no_email_without_one(self):
        from noul.providers import pubmed

        with (
            mock.patch("noul.config._email", return_value=None),
            mock.patch("noul.net.http._http_json", return_value={"esearchresult": {"idlist": []}}) as js,
            mock.patch("noul.net.http._http_get") as get,
        ):
            self.assertEqual(pubmed._search_pubmed("nothing"), [])
        self.assertNotIn("email", _query(js.call_args.args[0]))
        self.assertEqual(_query(js.call_args.args[0])["term"], "nothing")
        get.assert_not_called()


OPENALEX_WORK = {
    "id": "https://openalex.org/W42",
    "display_name": "  Sleep   and memory ",
    "doi": "https://doi.org/10.1000/SLEEP",
    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123456"},
    "abstract_inverted_index": {"memory": [2], "Sleep": [0], "improves": [1], "bad": ["x"]},
    "publication_year": 2022,
    "primary_location": {"source": {"display_name": "Nature"}, "pdf_url": "https://example.org/loc.pdf"},
    "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {}}, {}],
    "cited_by_count": 7,
    "open_access": {"is_oa": False, "oa_url": "https://example.org/oa.html"},
    "best_oa_location": {"pdf_url": "https://example.org/best.pdf"},
    "primary_topic": {
        "field": {"display_name": "Neuroscience"},
        "domain": {"display_name": "Life Sciences"},
        "subfield": {"display_name": "Cognitive Neuroscience"},
    },
    "type": "article",
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2/"],
    "related_works": ["https://openalex.org/W3"],
}


class OpenAlexParsingTests(unittest.TestCase):
    def test_full_work(self):
        from noul.providers import openalex

        p = openalex._parse_openalex_work(OPENALEX_WORK)
        self.assertEqual((p["title"], p["abstract"]), ("Sleep and memory", "Sleep improves memory"))
        self.assertEqual((p["doi"], p["pmid"], p["openalex_id"]), ("10.1000/sleep", "123456", "W42"))
        self.assertEqual((p["year"], p["journal"], p["citations"]), (2022, "Nature", 7))
        self.assertEqual(p["authors"], ["Ada Lovelace"])
        self.assertEqual(p["pdf_url"], "https://example.org/best.pdf")
        self.assertTrue(p["open_access"], "a PDF link makes the work open access")
        self.assertEqual(p["fields_of_study"], ["Neuroscience", "Life Sciences"])
        self.assertEqual(p["primary_category"], "Cognitive Neuroscience")
        self.assertEqual((p["referenced_works"], p["related_works"]), (["W1", "W2"], ["W3"]))
        self.assertNotIn("retraction", p)

    def test_concepts_are_the_fallback_for_fields(self):
        from noul.providers import openalex

        work = {
            "title": "Deep nets",
            "concepts": [
                {"display_name": "Computer science", "level": 0, "score": 0.9},
                {"display_name": "Physics", "level": 0, "score": 0.1},
                {"display_name": "Neural network", "level": 2, "score": 0.9},
            ],
        }
        p = openalex._parse_openalex_work(work)
        self.assertEqual((p["fields_of_study"], p["primary_category"]), (["Computer science"], "Computer science"))
        self.assertFalse(p["open_access"])

    def test_retractions_but_not_retraction_notices(self):
        from noul.providers import openalex

        retracted = openalex._parse_openalex_work({**OPENALEX_WORK, "is_retracted": True})
        notice = openalex._parse_openalex_work({**OPENALEX_WORK, "is_retracted": True, "type": "retraction"})
        self.assertEqual(retracted["retraction"]["source"], "openalex")
        self.assertNotIn("retraction", notice)

    def test_unusable_input(self):
        from noul.providers import openalex

        for work in (None, [], {}, {"title": "  "}):
            with self.subTest(work=work):
                self.assertIsNone(openalex._parse_openalex_work(work))
        for inv in (None, [], {}, {"word": "1"}, {"word": ["1"]}):
            with self.subTest(inv=inv):
                self.assertEqual(openalex._reconstruct_openalex_abstract(inv), "")


class OpenAlexSearchTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def test_filters_and_page_size(self):
        from noul.providers import openalex

        answer = {"results": [OPENALEX_WORK, {"title": ""}, None]}
        with mock.patch("noul.net.http._http_json", return_value=answer) as js:
            papers = openalex._search_openalex("sleep memory", limit=80, year_min=2020, open_access=True)
        params = _query(js.call_args.args[0])
        self.assertEqual((params["search"], params["per-page"]), ("sleep memory", "50"))
        self.assertEqual(params["filter"], "from_publication_date:2020-01-01,is_oa:true")
        self.assertEqual([p["openalex_id"] for p in papers], ["W42"])

    def test_no_filter_and_no_results(self):
        from noul.providers import openalex

        with mock.patch("noul.net.http._http_json", return_value={}) as js:
            self.assertEqual(openalex._search_openalex("q", limit=5), [])
        params = _query(js.call_args.args[0])
        self.assertNotIn("filter", params)
        self.assertEqual(params["per-page"], "5")


if __name__ == "__main__":
    unittest.main()


class InvalidXmlTests(unittest.TestCase):
    """A 200 answer that is not XML (empty body, HTML error page) is an unavailable source, not a crash."""

    def setUp(self):
        _support.block_http(self)

    def test_the_parsers_raise_an_http_error(self):
        from noul.errors import HTTPError
        from noul.providers import arxiv_search, pubmed

        for parse in (arxiv_search._parse_feed, pubmed._parse_pubmed_xml):
            for body in ("", "<html><body>Rate exceeded.</body></html"):
                with self.subTest(parser=parse.__module__, body=body), self.assertRaises(HTTPError) as caught:
                    parse(body)
                self.assertEqual(caught.exception.code, 502)

    def test_an_arxiv_lookup_ends_in_not_found(self):
        from noul import registry
        from noul.errors import HTTPError

        def fake_json(url, *args, **kwargs):
            if "semanticscholar" in url:
                raise HTTPError(429, "HTTP Error 429: Too Many Requests")
            raise HTTPError(404, "HTTP Error 404: Not Found")

        def fake_get(url, *args, **kwargs):
            if "export.arxiv.org" in url:
                return "<html><body>Rate exceeded.</body></html"
            raise HTTPError(404, "HTTP Error 404: Not Found")

        with (
            mock.patch("noul.net.http._http_json", side_effect=fake_json),
            mock.patch("noul.net.http._http_get", side_effect=fake_get),
            self.assertRaises(HTTPError),
        ):
            registry.call("get_paper", {"id": "arXiv:2401.01234"})
