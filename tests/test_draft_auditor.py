"""Unit tests for Phase 6 Issue #23 (generate_manuscript_blueprint & audit_manuscript_draft)."""

import io
import os
import tempfile
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


class DraftAuditorTests(unittest.TestCase):
    def test_generate_manuscript_blueprint_markdown_and_latex(self):
        bp_md = t.call(
            "generate_manuscript_blueprint",
            {
                "topic": "Sub-Quadratic Sparse Attention for Long-Context Reasoning",
                "venue": "neurips",
                "hypothesis": "Block-sparse routing preserves 99% exact token recall at 1/4th the KV cache memory.",
                "key_findings": ["+6.2% on GSM8K", "3.8x throughput on A100 GPUs"],
                "output_format": "markdown",
            },
        )
        self.assertEqual(bp_md["venue"], "neurips")
        roles = [s["imrad_role"] for s in bp_md["sections"]]
        for expected_role in (
            "abstract",
            "introduction",
            "related_work",
            "methodology",
            "experiments",
            "discussion",
            "conclusion",
            "venue_mandatory",
        ):
            self.assertIn(expected_role, roles)
        self.assertIn("NeurIPS Paper Checklist", bp_md["rendered_template"])
        self.assertIn("+6.2% on GSM8K", bp_md["rendered_template"])

        bp_tex = t.generate_manuscript_blueprint(
            topic="Clinical Efficacy of GLP-1 Receptor Agonists",
            venue="lancet",
            output_format="latex",
        )
        self.assertIn("\\section{Introduction}", bp_tex["rendered_template"])
        self.assertIn("CONSORT/STROBE", bp_tex["rendered_template"])

    def test_audit_manuscript_draft_detects_weak_claims_notation_and_reproducibility(self):
        draft_file = WS / "draft_neurips.tex"
        draft_file.write_text(
            "\\section{Introduction}\n"
            "Our method clearly proves that sparse routing achieves a massive improvement over every baseline.\n"
            "Furthermore, our model achieves state-of-the-art performance across tasks.\n"
            "We apply fine-tuning on one split and finetuning on another split.\n"
            "\\section{Methodology}\n"
            "We minimize $L_{total}$ in step 1 and then optimize $\\mathcal{L}_{total}$ for sample $x_i$ and $x^{(i)}$:\n"
            "\\begin{equation}\n"
            "\\mathcal{L}_{total} = \\alpha \\cdot \\Omega(\\theta)\n"
            "\\end{equation}\n"
            "\\section{Experiments}\n"
            "Accuracy is 91.2%.\n",
            encoding="utf-8",
        )

        res = t.call(
            "audit_manuscript_draft",
            {"draft_input": "draft_neurips.tex", "venue": "neurips", "output_format": "markdown"},
        )
        self.assertEqual(res["subject"]["title"], "draft_neurips.tex")
        by_cat = {}
        for sig in res["signals"]:
            by_cat.setdefault(sig["category"], []).append(sig)
            self.assertNotIn("severity", sig)
        self.assertGreaterEqual(len(by_cat["weak_claim"]), 2)
        self.assertGreaterEqual(len(by_cat["notation_inconsistency"]), 2)
        self.assertGreaterEqual(len(by_cat["reproducibility_omission"]), 4)
        quote = by_cat["weak_claim"][0]["excerpts"][0]
        self.assertEqual(quote["origin"], "draft")
        self.assertTrue(quote["untrusted"])  # a draft may be a downloaded PDF
        self.assertEqual(by_cat["reproducibility_omission"][0]["excerpts"], [])  # an omission has no passage
        self.assertEqual({c["id"]: c["detected"] for c in res["checklist"]}["claims_anchored"], "no")
        self.assertIn("# Manuscript draft audit", res["rendered_report"])

    def test_workspace_confinement_blocks_outside_files_and_symlinks(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="noul-draft-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside_dir, ignore_errors=True))
        secret_file = outside_dir / "secret.md"
        secret_file.write_text("Secret content outside workspace", encoding="utf-8")

        for bad_path in (str(secret_file), "../outside_draft.md"):
            with self.subTest(bad_path=bad_path), self.assertRaises(c.HTTPError) as ctx:
                t.audit_manuscript_draft(bad_path)
            self.assertIn(ctx.exception.code, (400, 403))

        if hasattr(os, "symlink"):
            link_path = WS / "symlink_draft.md"
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            os.symlink(secret_file, link_path)
            with self.assertRaises(c.HTTPError) as ctx:
                t.audit_manuscript_draft("symlink_draft.md")
            self.assertIn(ctx.exception.code, (400, 403))

    def test_cli_draft_structure_and_audit_pretty(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc1 = cli.main(
                [
                    "draft",
                    "structure",
                    "--topic",
                    "Causal Inference in Panel Data",
                    "--venue",
                    "aer",
                    "--findings",
                    "Significant ATT reduction; Robust to placebo tests",
                    "--pretty",
                ]
            )
        self.assertEqual(rc1, 0)
        self.assertIn("## 1. Introduction", buf.getvalue())

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cli.main(["draft", "audit", "This method clearly proves a massive improvement.", "--pretty"])
        self.assertEqual(rc2, 0)
        self.assertIn("category: weak_claim", buf2.getvalue())
        self.assertIn("\nThis method clearly proves a massive improvement.\n</untrusted_source nonce=", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
