"""PDF text extraction keeps every page (v1 stripped `<...>` spans, so `n < d` deleted pages up to the next `>`)."""

import io
import unittest
from unittest import mock

import _support


def setUpModule():
    _support.isolate_environment()


def _pdf(pages):
    """A minimal multi-page PDF whose pages contain the given text lines."""
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for lines in pages:
        page = writer.add_blank_page(612, 792)
        ops = ["BT /F1 10 Tf 50 740 Td 14 TL"]
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


PAGES = [
    ["1 Introduction", "Self-attention is cheaper when n < d, which is the common case."],
    ["5 Training", "We used Adam with beta2 = 0.98 and warmup over 4000 steps."],
    ["6 Results", "Our code is available at https://github.com/", "tensorflow/tensor2tensor."],
    ["7 Conclusion", "Attention heads > 4 help."],
]


class PdfExtractionTests(unittest.TestCase):
    def test_every_page_survives_with_page_breaks_and_headings(self):
        from noul.library import fulltext
        from noul.providers import pdf_parser

        text = fulltext._extract_pdf_text(_pdf(PAGES))
        for needle in ("n < d", "0.98", "6 Results", "heads > 4"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("\f"), 3)
        corpus = pdf_parser.build_corpus(text, source="fulltext")
        self.assertEqual(len(corpus["pages"]), 4)
        self.assertEqual(pdf_parser.page_at(corpus["pages"], text.find("0.98")), 2)
        self.assertEqual(
            [s["title"] for s in corpus["sections"]], ["1 Introduction", "5 Training", "6 Results", "7 Conclusion"]
        )

    def test_urls_broken_across_lines_are_rejoined(self):
        from noul.library import fulltext
        from noul.providers import artifact_radar

        text = fulltext._extract_pdf_text(_pdf(PAGES))
        urls = [u["url"] for u in artifact_radar.extract_artifact_urls(text)]
        self.assertIn("https://github.com/tensorflow/tensor2tensor", urls)

    def test_old_cache_entries_are_refetched_but_kept_if_the_download_fails(self):
        from noul.library import fulltext
        from noul.net import cache

        paper = {"id": "doi:10.1/old", "title": "Old", "pdf_url": "https://example.org/old.pdf", "open_access": True}
        cache.cache_set("paper:" + paper["id"], paper)
        cache.cache_set("ft:" + paper["id"], {"text": "lossy v1 text", "url": paper["pdf_url"], "source": "x"})
        with (
            mock.patch(_support.core_target("resolve_paper"), return_value=paper),
            mock.patch(_support.core_target("_http_get"), side_effect=_support.no_network),
        ):
            self.assertTrue(fulltext.fulltext(paper["id"])["available"])  # falls back to the old extraction
        with (
            mock.patch(_support.core_target("resolve_paper"), return_value=paper),
            mock.patch(_support.core_target("_http_get"), return_value=_pdf(PAGES)) as fetch,
        ):
            self.assertTrue(fulltext.fulltext(paper["id"])["available"])
            self.assertTrue(fulltext.fulltext(paper["id"])["available"])  # second call: cache hit
        fetch.assert_called_once()
        entry = cache.cache_get("ft:" + paper["id"])
        self.assertEqual(entry["format"], fulltext.PDF_TEXT_FORMAT)
        self.assertIn("0.98", entry["text"])

    def test_analytic_tools_refresh_an_old_cached_text_before_analysing_it(self):
        """Regression (3a plugin test): review_paper read the cached v1 text directly via get_paper_corpus."""
        from noul.net import cache
        from noul.providers import pdf_parser

        paper = {"id": "doi:10.1/corpus", "title": "C", "pdf_url": "https://example.org/c.pdf", "open_access": True}
        cache.cache_set("paper:" + paper["id"], paper)
        cache.cache_set("ft:" + paper["id"], {"text": "lossy v1 text " * 30, "url": paper["pdf_url"], "source": "x"})
        with (
            mock.patch(_support.core_target("resolve_paper"), return_value=paper),
            mock.patch(_support.core_target("_http_get"), return_value=_pdf(PAGES)),
        ):
            corpus = pdf_parser.get_paper_corpus(paper["id"])
        self.assertIn("0.98", corpus["text"])
        self.assertEqual(len(corpus["pages"]), 4)
        self.assertEqual(len(corpus["sections"]), 4)


class ArxivResolutionTests(unittest.TestCase):
    ATOM = (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
        "<id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title>"
        "<summary>We propose the Transformer.</summary><published>2017-06-12T17:57:34Z</published>"
        "<author><name>Ashish Vaswani</name></author><arxiv:primary_category term='cs.CL'/></entry></feed>"
    )

    def test_semantic_scholar_429_falls_back_to_openalex_then_arxiv(self):
        from noul import errors
        from noul.search import engine

        calls = []

        def json_side_effect(url, *a, **k):
            calls.append(url)
            raise errors.HTTPError(429, "rate limited")

        with (
            mock.patch(_support.core_target("_http_json"), side_effect=json_side_effect),
            mock.patch(_support.core_target("_http_get"), return_value=self.ATOM.encode()),
        ):
            paper = engine.resolve_paper("arxiv:1706.03762")
        self.assertEqual(paper["title"], "Attention Is All You Need")
        self.assertEqual(paper["arxiv_id"], "1706.03762v7")
        self.assertTrue(any("semanticscholar" in u for u in calls))
        self.assertTrue(any("10.48550%2FarXiv.1706.03762" in u for u in calls))  # OpenAlex by arXiv DOI
        # cached under the identifier the user typed
        with mock.patch(_support.core_target("_http_json"), side_effect=AssertionError("no network")):
            self.assertEqual(engine.resolve_paper("arxiv:1706.03762")["title"], "Attention Is All You Need")


class JatsTests(unittest.TestCase):
    def test_sections_and_captions_are_kept(self):
        from noul.library import fulltext
        from noul.providers import pdf_parser

        xml = (
            b"<article><front><article-meta><title-group><article-title>K2 trial</article-title></title-group>"
            b"<abstract><p>Vitamin K2 reduced stiffness.</p></abstract></article-meta></front><body>"
            b"<sec><label>2</label><title>Methods</title><p>We randomized 240 adults.</p>"
            b"<sec><title>Outcomes</title><p>The primary outcome was PWV.</p></sec></sec>"
            b"<sec><title>Results</title><table-wrap><label>Table 1</label><caption><p>Baseline.</p></caption>"
            b"<table><tr><th>Group</th><th>PWV</th></tr><tr><td>K2</td><td>-0.3</td></tr></table>"
            b"</table-wrap><p>PWV fell by 0.4 m/s.</p></sec></body></article>"
        )
        text = fulltext._jats_text(xml)
        titles = [s["title"] for s in pdf_parser.find_sections(text)]
        self.assertEqual(titles, ["Abstract", "2 Methods", "Outcomes", "Results"])
        self.assertIn("Table 1: Baseline.\nGroup PWV\nK2 -0.3", text)

    def test_dtd_entities_and_huge_documents_are_refused(self):
        from noul.library import fulltext

        bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xxxxxxxxxx"><!ENTITY y "&x;&x;&x;">]><a>&y;</a>'
        self.assertEqual(fulltext._jats_text(bomb), "")
        self.assertEqual(fulltext._jats_text(b"<a>" + b"x" * (fulltext.MAX_JATS_BYTES + 1) + b"</a>"), "")
        self.assertEqual(fulltext._jats_text(b"<a><unclosed></a>"), "")


class RetrievalTests(unittest.TestCase):
    def test_bm25_returns_section_and_page_and_never_crosses_them(self):
        from noul.library import fulltext, retrieval

        text = fulltext.normalize_pdf_text(
            [
                "1 Introduction\nTransformers are popular for translation.",
                "5 Training\nWe use Adam with warmup over 4000 steps.\n6 Results\nBLEU is 28.4 on WMT14.",
            ]
        )
        corpus = retrieval.corpus_for(text)
        chunks = retrieval.chunk_corpus(corpus)
        self.assertEqual(
            [(c["section"], c["page"]) for c in chunks], [("1 Introduction", 1), ("5 Training", 2), ("6 Results", 2)]
        )
        top = retrieval.search(corpus, "which optimizer and warmup steps were used?", n=2, paper_id="p")
        self.assertEqual((top[0].section, top[0].page, top[0].chunk_id), ("5 Training", 2, chunks[1]["id"]))
        self.assertEqual(text[top[0].char_start : top[0].char_end].split()[:2], ["5", "Training"])
        self.assertTrue(top[0].untrusted)
        self.assertGreater(top[0].relevance, 0)
        self.assertEqual([e.chunk_id for e in retrieval.search(corpus, "", n=2)], [c["id"] for c in chunks[:2]])
        self.assertEqual(retrieval.tokens("The heads and the Cabeças"), ["head", "cabeça"])

    def test_long_paragraphs_and_unpaged_text(self):
        from noul.library import retrieval

        para = " ".join(f"Sentence {i} talks about topic {i}." for i in range(120))  # one 4,000-char line
        corpus = retrieval.corpus_for("## Results\n" + para + " The answer is forty-two.")
        chunks = retrieval.chunk_corpus(corpus)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c["text"]) <= 1200 for c in chunks))
        top = retrieval.search(corpus, "what is the answer", n=1)[0]
        self.assertIn("forty-two", top.text)
        self.assertFalse(top.truncated)
        self.assertIsNone(top.page)  # no page breaks in the source: no page to cite

    def test_dehyphenation_keeps_capitalised_compounds(self):
        from noul.library import fulltext

        out = fulltext.normalize_pdf_text(
            ["trans-\nlation on English-\nto-German, state-\nof-the-art, self-\nattention"]
        )
        self.assertEqual(out, "translation on English-to-German, state-of-the-art, self-attention")


if __name__ == "__main__":
    unittest.main()
