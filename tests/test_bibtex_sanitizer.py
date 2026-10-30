"""Unit tests for Phase 6 Issue #25 (sanitize_bibtex & resolve_published_venue_for_preprint)."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import _support


def setUpModule():
    global c, t, cli, WS
    env = _support.isolate_environment()
    WS = Path(env["NOUL_WORKSPACE"]).resolve()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class BibtexSanitizerTests(unittest.TestCase):
    def setUp(self):
        # Seed a peer-reviewed published NeurIPS record corresponding to an arXiv preprint
        self.published_paper = {
            "id": "doi:10.5555/neurips.2023.qlora",
            "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
            "authors": ["Tim Dettmers", "Artidoro Pagnoni", "Ari Holtzman", "Luke Zettlemoyer"],
            "year": 2023,
            "journal": "Advances in Neural Information Processing Systems (NeurIPS)",
            "doi": "10.5555/neurips.2023.qlora",
            "arxiv_id": "2305.14314",
            "citations": 1500,
            "evidence": "empirical",
            "open_access": True,
        }
        c.cache_set("paper:" + self.published_paper["id"], self.published_paper)

    def test_upgrades_arxiv_preprint_deduplicates_and_fixes_latex_escapes(self):
        raw_bib = """
@misc{dettmers2023qlora,
  title = {QLoRA: Efficient Finetuning of Quantized LLMs},
  author = {Tim Dettmers; Artidoro Pagnoni & Ari Holtzman and Luke Zettlemoyer},
  year = {2023},
  eprint = {2305.14314},
  archivePrefix = {arXiv},
  journal = {arXiv preprint arXiv:2305.14314}
}

@article{dettmers2023_dup,
  title = {QLoRA: Efficient Finetuning of Quantized LLMs},
  author = {Tim Dettmers and Artidoro Pagnoni and Ari Holtzman and Luke Zettlemoyer},
  year = {2023},
  journal = {CoRR},
  eprint = {2305.14314v2}
}

@article{smith2024scaling,
  title = {Scaling GPU Inference & Memory_Footprint by 50% for BERT and RLHF},
  author = {Alice Smith and Bob Jones},
  year = {2024},
  journal = {Journal of Machine Learning & Systems_Engineering},
  doi = {10.1000/jmls.2024.01}
}
"""
        bib_path = WS / "references_dirty.bib"
        out_path = WS / "references_clean.bib"
        bib_path.write_text(raw_bib, encoding="utf-8")

        res = t.call(
            "sanitize_bibtex",
            {
                "bibtex_input": "references_dirty.bib",
                "update_preprints": True,
                "output_path": "references_clean.bib",
            },
        )

        self.assertEqual(res["entries_total"], 3)
        self.assertEqual(res["entries_out"], 2)
        self.assertEqual(res["duplicates_removed"], 1)
        self.assertEqual(res["preprints_upgraded"], 1)
        self.assertGreaterEqual(res["dois_completed"], 1)
        self.assertGreaterEqual(res["latex_escapes_fixed"], 4)

        clean_bib = res["sanitized_bibtex"]
        self.assertTrue(out_path.is_file())
        self.assertIn("@inproceedings{dettmers2023qlora,", clean_bib)
        self.assertIn("booktitle = {Advances in Neural Information Processing Systems (NeurIPS)},", clean_bib)
        self.assertIn("doi = {10.5555/neurips.2023.qlora},", clean_bib)
        self.assertNotIn("dettmers2023_dup", clean_bib)

        # Verify LaTeX special character escaping (&, %, _) and acronym protection ({GPU}, {BERT}, {RLHF})
        self.assertIn("\\&", clean_bib)
        self.assertIn("50\\%", clean_bib)
        self.assertIn("Memory\\_Footprint", clean_bib)
        self.assertIn("{GPU}", clean_bib)
        self.assertIn("{BERT}", clean_bib)
        self.assertIn("{RLHF}", clean_bib)

    def test_cli_bibtex_sanitize_pretty_and_workspace_confinement(self):
        bib_file = WS / "cli_test.bib"
        bib_file.write_text(
            "@misc{arxiv2305,\n"
            "  title = {QLoRA: Efficient Finetuning of Quantized LLMs},\n"
            "  author = {Tim Dettmers},\n"
            "  year = {2023},\n"
            "  eprint = {2305.14314}\n"
            "}\n",
            encoding="utf-8",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["bibtex", "sanitize", "cli_test.bib", "--update-preprints", "--pretty"])
        self.assertEqual(rc, 0)
        self.assertIn("@inproceedings{", buf.getvalue())

        with self.assertRaises(c.HTTPError) as ctx:
            t.sanitize_bibtex("cli_test.bib", output_path="../escape_clean.bib")
        self.assertIn(ctx.exception.code, (400, 403))


if __name__ == "__main__":
    unittest.main()
