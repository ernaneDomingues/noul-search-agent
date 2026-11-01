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


if __name__ == "__main__":
    unittest.main()
