"""Unit tests for Phase 5 Polyglot Code & Experimental Protocol Scaffolder (#16 PyTorch, #21 domain-adaptive)."""
import ast
import io
import unittest
from contextlib import redirect_stdout

import noul_cli
import noul_core
import noul_tools


class ScaffolderTests(unittest.TestCase):
    def setUp(self):
        self.cs_id = "arxiv:2502.77001"
        noul_core.cache_set(
            "paper:" + self.cs_id,
            {
                "id": self.cs_id,
                "title": "GatedAttention: Sub-Quadratic Sequence Modeling with Adaptive Decay",
                "authors": ["Lina Chen", "Marco Rossi"],
                "year": 2025,
                "journal": "NeurIPS",
                "arxiv_id": "2502.77001",
                "primary_category": "cs.LG",
                "categories": ["cs.LG", "cs.AI"],
                "evidence": "empirical",
                "open_access": True,
                "abstract": (
                    "We propose GatedAttention, a 1.5B parameter transformer model with decay gate gamma_decay "
                    "and scaling factor omega_gate. It achieves 81.2% on GSM8K."
                ),
            },
        )
        noul_core.cache_set(
            "ft:" + self.cs_id,
            {
                "text": (
                    "# Method\nThe adaptive gate is computed as:\n"
                    "$$ G(x) = \\gamma_{decay} \\cdot x + \\omega_{gate} $$\n"
                )
            },
        )

        self.econ_id = "doi:10.1093/qje/2025888"
        noul_core.cache_set(
            "paper:" + self.econ_id,
            {
                "id": self.econ_id,
                "title": "Carbon Taxes and Manufacturing Output: Difference-in-Differences Evidence",
                "authors": ["Sarah Jenkins", "Paul Krug"],
                "year": 2025,
                "journal": "Quarterly Journal of Economics",
                "doi": "10.1093/qje/2025888",
                "primary_category": "econ.GN",
                "categories": ["econ.GN"],
                "fields_of_study": ["Economics"],
                "evidence": "empirical",
                "open_access": False,
                "abstract": (
                    "Using a difference-in-differences panel of 5,000 plants, we estimate the effect of carbon "
                    "pricing on output with firm-clustered standard errors."
                ),
            },
        )

        self.bio_id = "pmid:39555001"
        noul_core.cache_set(
            "paper:" + self.bio_id,
            {
                "id": self.bio_id,
                "title": "Randomized Double-Blind Trial of Peptide-9 in Moderate Heart Failure",
                "authors": ["Hannah Lee", "Carlos Gomez"],
                "year": 2025,
                "journal": "New England Journal of Medicine",
                "pmid": "39555001",
                "fields_of_study": ["Medicine"],
                "evidence": "rct",
                "open_access": False,
                "abstract": (
                    "Methods: We conducted a randomized, double-blind, placebo-controlled trial in N = 160 patients "
                    "with heart failure to assess 6-minute walk distance and adverse events."
                ),
            },
        )

    def test_cs_pytorch_scaffold_and_ast_validation(self):
        res = noul_tools.call("scaffold_implementation", {"paper_id": self.cs_id})
        self.assertEqual(res["domain"], "computer_science")
        self.assertEqual(res["target"], "pytorch")
        self.assertEqual(res["language"], "python")
        self.assertTrue(res["syntax_valid"])
        self.assertTrue(res["shape_verified"])
        self.assertIn("torch>=2.0.0", res["requirements_txt"])

        code = res["source_code"]
        tests = res["unit_tests"]
        self.assertIn("# requirements.txt:", code)
        self.assertIn("class PaperModule(nn.Module):", code)
        self.assertIn("def forward(self, x: torch.Tensor", code)
        self.assertIn("gamma_decay", code)
        self.assertIn("omega_gate", code)
        self.assertNotRegex(code, r"^\s*pass\s*$")
        self.assertIn("expected_shape = (batch_size, seq_len, config.d_model)", tests)
        self.assertIn("assert out.shape == expected_shape", tests)

        # Must parse cleanly with Python's AST
        ast.parse(code)
        ast.parse(tests)

    def test_economics_statsmodels_and_r_scaffolds(self):
        sm_res = noul_tools.scaffold_implementation(self.econ_id)
        self.assertEqual(sm_res["domain"], "economics")
        self.assertEqual(sm_res["target"], "statsmodels")
        self.assertEqual(sm_res["language"], "python")
        self.assertTrue(sm_res["syntax_valid"])
        self.assertIn("def generate_synthetic_panel(", sm_res["source_code"])
        self.assertIn("def run_robustness_matrix(", sm_res["source_code"])
        self.assertIn("difference_in_differences", sm_res["source_code"])
        ast.parse(sm_res["source_code"])
        ast.parse(sm_res["unit_tests"])

        # Execute the generated pure-Python fallback of the econometric scaffold to verify runtime behavior
        ns = {}
        exec(sm_res["source_code"], ns)
        matrix = ns["run_robustness_matrix"]()
        self.assertIn("baseline_spec", matrix)
        self.assertIn("placebo_pretrend", matrix)

        r_res = noul_tools.scaffold_implementation(self.econ_id, target="r")
        self.assertEqual(r_res["target"], "r")
        self.assertEqual(r_res["language"], "r")
        self.assertIn("feols(", r_res["source_code"])
        self.assertIn("stopifnot(", r_res["unit_tests"])

    def test_biomedical_clinical_protocol_scaffold(self):
        bio_res = noul_tools.scaffold_implementation(self.bio_id)
        self.assertEqual(bio_res["domain"], "biomedical")
        self.assertEqual(bio_res["target"], "clinical_protocol")
        self.assertTrue(bio_res["syntax_valid"])
        code = bio_res["source_code"]
        self.assertIn("Eligibility Criteria (Inclusion & Exclusion)", code)
        self.assertIn("Intervention, Dosing & Blinding Protocol", code)
        self.assertIn("def generate_synthetic_cohort(", code)
        self.assertIn("def run_protocol_analysis(", code)
        ast.parse(code)
        ast.parse(bio_res["unit_tests"])

        ns = {}
        exec(code, ns)
        cohort = ns["generate_synthetic_cohort"]()
        analysis = ns["run_protocol_analysis"](cohort)
        self.assertEqual(analysis["n_total"], 160.0)
        self.assertIn("treatment_effect", analysis)

    def test_alias_invalid_target_and_cli_pretty(self):
        alias_res = noul_tools.call("scaffold_paper_code", {"paper_id": self.cs_id, "target_language": "pytorch"})
        self.assertEqual(alias_res["target"], "pytorch")

        with self.assertRaises(noul_core.HTTPError) as ctx:
            noul_tools.scaffold_implementation(self.cs_id, target="cobol")
        self.assertEqual(ctx.exception.code, 400)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = noul_cli.main(["scaffold", self.cs_id, "--pretty"])
        self.assertEqual(rc, 0)
        self.assertIn("# Code & Protocol Scaffold:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
