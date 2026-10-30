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
        for expected_role in ("abstract", "introduction", "related_work", "methodology", "experiments", "discussion", "conclusion", "venue_mandatory"):
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
        self.assertEqual(res["source_label"], "draft_neurips.tex")
        self.assertGreaterEqual(len(res["weak_or_unsubstantiated_claims"]), 2)
        self.assertGreaterEqual(len(res["notation_or_terminology_inconsistencies"]), 2)
        self.assertGreaterEqual(len(res["reproducibility_omissions"]), 4)
        self.assertIn("# Manuscript Audit Report", res["rendered_report"])

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
            rc1 = cli.main([
                "draft", "structure",
                "--topic", "Causal Inference in Panel Data",
                "--venue", "aer",
                "--findings", "Significant ATT reduction; Robust to placebo tests",
                "--pretty",
            ])
        self.assertEqual(rc1, 0)
        self.assertIn("## 1. Introduction", buf.getvalue())

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cli.main(["draft", "audit", "This method clearly proves a massive improvement.", "--pretty"])
        self.assertEqual(rc2, 0)
        self.assertIn("Weak or Unsubstantiated Claims", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
