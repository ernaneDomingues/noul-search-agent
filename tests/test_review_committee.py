"""Unit tests for Phase 5 Multi-Persona Peer-Review Committee (#17 NeurIPS/ICLR AI reviewer, #20 domain-adaptive committee)."""

import io
import unittest
from contextlib import redirect_stdout

import noul_cli
import noul_core
import noul_tools


class ReviewCommitteeTests(unittest.TestCase):
    def setUp(self):
        self.cs_id = "arxiv:2501.90001"
        noul_core.cache_set(
            "paper:" + self.cs_id,
            {
                "id": self.cs_id,
                "title": "SparseRouter: Mixture-of-Experts Transformer with Sub-Linear Attention",
                "authors": ["Alice Zhang", "Bob Lin", "Carlos Silva"],
                "year": 2025,
                "journal": "ICLR",
                "arxiv_id": "2501.90001",
                "primary_category": "cs.LG",
                "categories": ["cs.LG", "cs.CL"],
                "evidence": "empirical",
                "open_access": True,
                "abstract": (
                    "We introduce SparseRouter, a 7B parameter transformer architecture for long-context language "
                    "modeling. Our method achieves 78.4% accuracy on MMLU and 82.1% on GSM8K. However, performance "
                    "degrades when sequence length exceeds 32k tokens under noisy prompts. Code is available upon "
                    "reasonable request."
                ),
            },
        )
        noul_core.cache_set(
            "ft:" + self.cs_id,
            {
                "text": (
                    "# Introduction\nWe study sparse routing in 7B transformers.\n\n"
                    "# Methodology\nLet the routing loss be:\n$$ L_{route} = \\alpha \\cdot x^2 + \\beta $$\n"
                    "We assume i.i.d. token routing distributions across layers.\n\n"
                    "# Experiments\nTable 1: Main benchmark results\n"
                    "| Model | MMLU | GSM8K |\n| --- | --- | --- |\n"
                    "| Baseline-7B | 74.0 | 76.5 |\n| SparseRouter (Ours) | 78.4 | 82.1 |\n"
                )
            },
        )

        self.bio_id = "pmid:39000111"
        noul_core.cache_set(
            "paper:" + self.bio_id,
            {
                "id": self.bio_id,
                "title": "Cardioprotective Effects of Compound-X in a Murine Myocardial Infarction Cohort",
                "authors": ["Elena Costa", "David Miller"],
                "year": 2024,
                "journal": "Journal of Translational Cardiology",
                "pmid": "39000111",
                "fields_of_study": ["Medicine", "Cardiology"],
                "evidence": "preclinical",
                "open_access": False,
                "abstract": (
                    "Background: Myocardial ischemia remains a leading cause of mortality. "
                    "Methods: We evaluated Compound-X in N = 24 mice using an in vitro and murine animal model "
                    "without blinding or a priori sample size calculation. "
                    "Results: Left ventricular ejection fraction improved by 12% (p = 0.04)."
                ),
            },
        )

        self.econ_id = "doi:10.1257/aer.20250099"
        noul_core.cache_set(
            "paper:" + self.econ_id,
            {
                "id": self.econ_id,
                "title": "Minimum Wage Shocks and Firm Employment: A Difference-in-Differences Panel Analysis",
                "authors": [" Henrik Kleven", "Maria Santos"],
                "year": 2025,
                "journal": "American Economic Review",
                "doi": "10.1257/aer.20250099",
                "primary_category": "econ.GN",
                "categories": ["econ.GN", "q-fin.EC"],
                "fields_of_study": ["Economics", "Econometrics"],
                "evidence": "empirical",
                "open_access": False,
                "abstract": (
                    "We study the employment effect of regional minimum wage increases using a difference-in-differences "
                    "design on a panel of 12,000 firms. Assuming parallel trends between treated and control regions, "
                    "we estimate a 1.8% reduction in low-skill hiring."
                ),
            },
        )

    @staticmethod
    def _concerns(res):
        return " ".join(sig["description"] for sig in res["signals"] if sig["kind"] in ("concern", "gap")).lower()

    def test_cs_neurips_iclr_personas_and_omitted_controls(self):
        res = noul_tools.call("review_paper", {"paper_id": self.cs_id})
        self.assertEqual(res["tool"], "review_paper")
        self.assertEqual(res["subject"]["domain"], "computer_science")
        self.assertIn("NeurIPS / ICLR", res["rubric_applied"])
        self.assertEqual(
            [p["role"] for p in res["perspectives"]],
            [
                "Theoretical Reviewer (NeurIPS/ICLR)",
                "Skeptical Empirical Reviewer (NeurIPS/ICLR)",
                "Systems & Reproducibility Engineer (NeurIPS/ICLR)",
            ],
        )
        ids = {sig["id"] for sig in res["signals"]}
        for p in res["perspectives"]:
            self.assertTrue(set(p["signal_ids"]) <= ids)
        concerns = self._concerns(res)
        # Cites omitted ablation table and omitted open code / evasive availability statement
        self.assertIn("no ablation", concerns)
        self.assertIn("no public code", concerns)
        self.assertTrue(res["missing_experiments_or_controls"])
        self.assertTrue(res["questions_for_authors"])
        # Evidence, not a verdict: no verdict or scores anywhere; the client judges with the rubric.
        for banned in ("verdict", "overall_score", "evaluations"):
            self.assertNotIn(banned, res)
        self.assertEqual(res["rubric"]["criteria"][-1]["id"], "recommendation")
        self.assertIn(res["extraction_confidence"], ("low", "medium", "high"))

    def test_domain_switching_changes_rubric_personas_flaws_and_questions(self):
        cs_res = noul_tools.review_paper(self.cs_id)
        bio_res = noul_tools.review_paper(self.bio_id)
        econ_res = noul_tools.review_paper(self.econ_id)

        self.assertEqual(bio_res["subject"]["domain"], "biomedical")
        self.assertEqual(econ_res["subject"]["domain"], "economics")

        self.assertNotEqual(cs_res["rubric_applied"], bio_res["rubric_applied"])
        self.assertNotEqual(bio_res["rubric_applied"], econ_res["rubric_applied"])
        self.assertIn("CONSORT", bio_res["rubric_applied"])
        self.assertIn("Econometrics", econ_res["rubric_applied"])

        bio_roles = [p["role"] for p in bio_res["perspectives"]]
        econ_roles = [p["role"] for p in econ_res["perspectives"]]
        self.assertIn("Clinical Trial & Study Design Methodologist (CONSORT/STROBE)", bio_roles)
        self.assertIn("Biostatistician & Statistical Power Auditor", bio_roles)
        self.assertIn("Translational Medicine & Clinical Safety Reviewer", bio_roles)

        self.assertIn("Causal Identification Econometrician", econ_roles)
        self.assertIn("Empirical Robustness & Inference Auditor", econ_roles)
        self.assertIn("Policy & External Validity Economist", econ_roles)

        bio_flaws = self._concerns(bio_res)
        self.assertIn("power calculation", bio_flaws)
        self.assertIn("preclinical", bio_flaws)

        econ_flaws = self._concerns(econ_res)
        self.assertIn("clustered standard errors", econ_flaws)
        self.assertIn("placebo", econ_flaws)

        self.assertNotEqual(cs_res["questions_for_authors"], bio_res["questions_for_authors"])
        self.assertNotEqual(bio_res["questions_for_authors"], econ_res["questions_for_authors"])

    def test_alias_and_cli_pretty(self):
        alias_res = noul_tools.call("review_paper_scientific", {"paper_id": self.econ_id, "inspect_fulltext": False})
        self.assertEqual(alias_res["subject"]["domain"], "economics")
        self.assertEqual(alias_res["extraction_confidence"], "low")
        self.assertIn("# Peer-review evidence", alias_res["rendered_markdown"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = noul_cli.main(["review", self.cs_id, "--pretty"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("# Peer-review evidence", out)
        self.assertIn("Theoretical Reviewer (NeurIPS/ICLR)", out)
        self.assertIn("not a verdict", out)
        self.assertNotIn("Verdict", out)


if __name__ == "__main__":
    unittest.main()
