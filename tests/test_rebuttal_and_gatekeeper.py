"""Unit tests for Phase 6 Issues #26 (parse_and_draft_rebuttal) & #27 (audit_submission_readiness)."""

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


class RebuttalAndGatekeeperTests(unittest.TestCase):
    def test_parse_and_draft_rebuttal_segments_reviewers_and_flags_new_experiments(self):
        reviews_txt = """
Reviewer 1:
- The intuition behind the gating parameter beta in Section 3.1 is unclear. Could the authors clarify how beta is initialized?
- Please run additional ablation experiments on GSM8K with 5 random seeds and report p-values for statistical significance.
- There is a minor typo in Equation 4 where symbol Z is not defined.

Reviewer #2:
- The evaluation is missing baseline comparisons against recent SOTA baseline FlashAttention-2.
- The causal identification assumption in Section 3 may suffer from selection bias on long sequences.
"""
        rev_file = WS / "reviews.txt"
        rev_file.write_text(reviews_txt, encoding="utf-8")

        res = t.call(
            "parse_and_draft_rebuttal",
            {"reviews_input": "reviews.txt", "output_format": "markdown"},
        )
        self.assertEqual(res["reviewers_detected"], ["Reviewer 1", "Reviewer 2"])
        self.assertEqual(res["total_points"], 5)
        self.assertGreaterEqual(res["new_experiments_required"], 1)

        categories = {pt["point_id"]: pt["category"] for pt in res["points"]}
        self.assertEqual(categories["R1.1"], "clarity_question")
        self.assertEqual(categories["R1.2"], "new_experiment_request")
        self.assertEqual(categories["R1.3"], "notation_or_formatting")
        self.assertEqual(categories["R2.1"], "baseline_criticism")
        self.assertEqual(categories["R2.2"], "methodological_or_statistical")

        r1_2 = next(pt for pt in res["points"] if pt["point_id"] == "R1.2")
        self.assertTrue(r1_2["requires_new_experiment"])
        self.assertIn("Response to Reviewer 1", res["markdown_rebuttal"])
        self.assertIn("\\subsection*{Response to Reviewer 2}", res["latex_rebuttal"])

    def test_gatekeeper_detects_anonymity_violations_and_overclaiming(self):
        bad_draft = WS / "leaky_submission.tex"
        bad_draft.write_text(
            "\\title{Universal Solver}\n"
            "\\author{John Doe, Jane Smith \\\\ MIT CSAIL \\\\ \\texttt{johndoe@mit.edu}}\n"
            "\\hypersetup{pdfauthor={John Doe}}\n"
            "\\begin{abstract}\n"
            "Our method proves beyond any doubt that it universally outperforms all prior models with zero limitations.\n"
            "\\end{abstract}\n"
            "\\section{Introduction}\n"
            "In our previous work \\cite{doe2023}, we introduced the base kernel.\n"
            "Our code and weights are available at https://github.com/johndoe/universal-solver.\n"
            "\\section{Experiments}\n"
            "We achieve 99.9% accuracy.\n",
            encoding="utf-8",
        )

        res = t.call(
            "audit_submission_readiness",
            {"draft_path": "leaky_submission.tex", "venue": "neurips"},
        )
        self.assertEqual(res["venue"], "neurips")
        self.assertEqual(res["desk_reject_risk"], "critical")
        self.assertLess(res["readiness_score"], 40)

        anon_ids = {v["check_id"] for v in res["anonymity_violations"]}
        self.assertIn("anon_repo_url", anon_ids)
        self.assertIn("anon_author_block", anon_ids)
        self.assertIn("anon_pdf_metadata", anon_ids)
        self.assertIn("anon_self_citation", anon_ids)
        self.assertGreaterEqual(len(res["overclaiming_flags"]), 2)
        self.assertTrue(len(res["mandatory_corrective_actions"]) >= 4)

    def test_gatekeeper_passes_compliant_anonymous_manuscript_and_pdf(self):
        good_draft = WS / "clean_submission.tex"
        good_draft.write_text(
            "\\title{Calibrated Sparse Attention}\n"
            "\\author{Anonymous Author(s)}\n"
            "\\begin{abstract}\n"
            "We study calibrated sparse attention for long-context transformers.\n"
            "\\end{abstract}\n"
            "\\section{Introduction}\n"
            "Prior work by Doe et al. \\cite{doe2023} explored dense kernels. "
            "Anonymized code is released under the MIT License at https://anonymous.4open.science/r/sparse-attn-123.\n"
            "\\section{Methodology}\n"
            "We formulate the sparse routing operator.\n"
            "\\section{Experiments}\n"
            "We train with AdamW (learning rate 1e-4, batch size 64) across 5 random seeds on 4x NVIDIA A100 GPUs "
            "and report mean accuracy 84.2% \\pm 0.3% (95% CI).\n"
            "\\section{Limitations}\n"
            "Our method requires flash-kernel support on modern GPUs.\n"
            "\\section{Broader Impact}\n"
            "This work improves inference efficiency and lowers energy consumption.\n"
            "\\section*{References}\n"
            "[1] Doe et al., 2023.\n",
            encoding="utf-8",
        )

        res = t.audit_submission_readiness("clean_submission.tex", venue="neurips")
        self.assertEqual(len(res["anonymity_violations"]), 0)
        self.assertEqual(len(res["overclaiming_flags"]), 0)
        self.assertEqual(res["readiness_score"], 100)
        self.assertEqual(res["desk_reject_risk"], "low")

        # Also test PDF file support in workspace
        pdf_file = WS / "manuscript.pdf"
        pdf_file.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nstream\n"
            b"(Abstract: We study sparse attention. 1. Introduction: Code at https://github.com/realuser/repo)\n"
            b"endstream\nendobj\n%%EOF"
        )
        res_pdf = t.audit_submission_readiness("manuscript.pdf", venue="icml")
        self.assertEqual(res_pdf["desk_reject_risk"], "critical")
        self.assertTrue(any(v["check_id"] == "anon_repo_url" for v in res_pdf["anonymity_violations"]))

    def test_cli_rebuttal_and_gatekeeper_pretty(self):
        rev_file = WS / "rev_cli.txt"
        rev_file.write_text(
            "Reviewer 1:\n- Please run additional ablation experiments with more seeds.", encoding="utf-8"
        )
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            rc1 = cli.main(["rebuttal", "parse", "rev_cli.txt", "--format", "latex", "--pretty"])
        self.assertEqual(rc1, 0)
        self.assertIn("\\section*{Author Response to Reviewers}", buf1.getvalue())

        sub_file = WS / "cli_submission.tex"
        sub_file.write_text(
            "\\title{Test}\\author{Anonymous Author(s)}\\begin{abstract}Text.\\end{abstract}\n"
            "\\section{Introduction}\nIntro.\n\\section{Methodology}\nMethod.\n"
            "\\section{Experiments}\nAdamW seed 42 on A100 GPU 80.0 \\pm 0.2% MIT License.\n"
            "\\section{Limitations}\nLimits.\n\\section{Broader Impact}\nImpact.\n\\section*{References}\nRef.\n",
            encoding="utf-8",
        )
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cli.main(["gatekeeper", "audit", "cli_submission.tex", "--venue", "neurips", "--pretty"])
        self.assertEqual(rc2, 0)
        self.assertIn("# Pre-Submission Gatekeeper Audit", buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
