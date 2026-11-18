"""Third-party metadata in exported files stays data: it cannot close a BibTeX field, start a RIS record, run as a
spreadsheet formula or render as live Markdown/HTML in an Obsidian note."""

import csv
import io
import unittest

import _support


def setUpModule():
    _support.isolate_environment()


HOSTILE = {
    "id": "doi:10.1000/evil",
    "doi": "10.1000/evil}",
    "title": '=HYPERLINK("https://attacker/?x") } @misc{owned, note={\\input{/etc/passwd}}',
    "authors": ["Mallory\nER  - \n\nTY  - JOUR\nTI  - Forged", "Bob"],
    "year": 2024,
    "journal": "J\nER  - ",
}


class ExportTests(unittest.TestCase):
    def test_bibtex_values_cannot_close_their_field(self):
        from noul.library import export
        from noul.tools.authoring import bibtex

        text, _ = export.export([HOSTILE], "bibtex")
        entries = bibtex.parse_bibtex_entries(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["fields"]), {"title", "author", "year", "journal", "doi"})
        self.assertNotIn("\\input", text)
        self.assertNotIn("}", entries[0]["fields"]["doi"])

    def test_entries_built_for_related_work_are_escaped_too(self):
        from noul.tools.authoring import bibtex

        entry = bibtex._format_bibtex_entry_from_paper(HOSTILE, "k")
        self.assertEqual(len(bibtex.parse_bibtex_entries(entry)), 1)
        self.assertNotIn("\\input", entry)

    def test_ris_fields_cannot_start_a_record(self):
        from noul.library import export

        text, _ = export.export([HOSTILE], "ris")
        lines = text.splitlines()  # RIS tags count only at the start of a line
        self.assertEqual(sum(line.startswith("TY  - ") for line in lines), 1)
        self.assertEqual(sum(line.startswith("ER  - ") for line in lines), 1)

    def test_csv_cells_never_start_a_formula(self):
        from noul.library import export

        text, _ = export.export([HOSTILE, {**HOSTILE, "title": "-2+3", "id": "@SUM(A1)"}], "csv")
        rows = list(csv.reader(io.StringIO(text)))[1:]
        for row in rows:
            for cell in row:
                with self.subTest(cell=cell):
                    self.assertFalse(cell.startswith(("=", "+", "-", "@")))


class ObsidianTests(unittest.TestCase):
    def test_paper_markdown_is_escaped_and_file_names_are_plain(self):
        from noul.tools import comparative_tools as c

        self.assertEqual(c._md("a ![x](https://e/p) <iframe>"), "a \\!\\[x\\](https://e/p) \\<iframe\\>")
        self.assertNotIn("‮", c._safe_filename_part("Report‮fdp.exe", 60))


if __name__ == "__main__":
    unittest.main()
