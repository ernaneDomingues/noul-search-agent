"""Unit tests for Phase 5 Autonomous Research Gap & Hypothesis Engine (#18)."""

import io
import unittest
from contextlib import redirect_stdout

import noul_cli
import noul_core
import noul_tools


class ResearchGapEngineTests(unittest.TestCase):
    def setUp(self):
        self.id1 = "arxiv:2503.10001"
        self.id2 = "arxiv:2503.10002"
        self.id3 = "arxiv:2503.10003"

        noul_core.cache_set(
            "paper:" + self.id1,
            {
                "id": self.id1,
                "title": "LinearCache: Constant-Memory KV Compression for Long-Context LLMs",
                "authors": ["Victor Alvarez", "Nadia Kim"],
                "year": 2024,
                "journal": "NeurIPS",
                "arxiv_id": "2503.10001",
                "primary_category": "cs.CL",
                "categories": ["cs.CL", "cs.LG"],
                "evidence": "empirical",
                "open_access": True,
                "abstract": (
                    "We propose LinearCache, reducing KV cache memory by 70% on MMLU and GSM8K. "
                    "However, our method struggles with noisy retrieval prompts and degrades on exact token recall."
                ),
            },
        )
        noul_core.cache_set(
            "paper:" + self.id2,
            {
                "id": self.id2,
                "title": "NoiseGuard: Robust Attention Denoising for Retrieval-Augmented Generation",
                "authors": ["Priya Patel", "Thomas Weber"],
                "year": 2025,
                "journal": "ICLR",
                "arxiv_id": "2503.10002",
                "primary_category": "cs.CL",
                "categories": ["cs.CL"],
                "evidence": "empirical",
                "open_access": True,
                "abstract": (
                    "We introduce NoiseGuard, an attention denoising layer that improves accuracy by 9.4% on GSM8K "
                    "and MMLU under adversarial prompt perturbations. A limitation is its quadratic memory overhead "
                    "beyond 16k tokens."
                ),
            },
        )
        noul_core.cache_set(
            "paper:" + self.id3,
            {
                "id": self.id3,
                "title": "ExactRecall: Hybrid Sparse-Dense Token Routing for Long Documents",
                "authors": ["Kenji Sato", "Laura Meyer"],
                "year": 2025,
                "journal": "ICML",
                "arxiv_id": "2503.10003",
                "primary_category": "cs.LG",
                "categories": ["cs.LG"],
                "evidence": "empirical",
                "open_access": False,
                "abstract": (
                    "We present ExactRecall to preserve needle-in-a-haystack token retrieval up to 64k tokens on MMLU. "
                    "However, latency increases by 35% on short sequences."
                ),
            },
        )

    def test_cross_analyzes_contradictions_complementarities_and_hypotheses(self):
        res = noul_tools.call(
            "generate_research_hypotheses",
            {
                "paper_ids": [self.id1, self.id2, self.id3],
                "topic": "Long-Context Robust LLM Inference",
                "max_hypotheses": 3,
            },
        )
        self.assertEqual(res["paper_ids"], [self.id1, self.id2, self.id3])
        self.assertTrue(len(res["contradictions_found"]) >= 1)
        self.assertTrue(len(res["unexplored_complementarities"]) >= 1)
        self.assertEqual(len(res["hypotheses"]), 3)

        joined_comp = " ".join(res["unexplored_complementarities"])
        self.assertIn("[Alvarez & Kim, 2024]", joined_comp)
        self.assertIn("[Patel & Weber, 2025]", joined_comp)

        for h in res["hypotheses"]:
            self.assertGreaterEqual(len(h["source_papers"]), 2)
            self.assertTrue(h["title"].strip())
            self.assertTrue(h["rationale"].strip())
            self.assertTrue(h["proposed_methodology"].strip())
            self.assertTrue(len(h["required_baselines"]) >= 2)
            self.assertTrue(len(h["target_datasets_or_population"]) >= 1)
            self.assertTrue(len(h["evaluation_metrics"]) >= 1)

        self.assertIn("# Autonomous Research Gap & Hypothesis Report:", res["markdown_report"])

    def test_rejects_single_paper_with_400(self):
        with self.assertRaises(noul_core.HTTPError) as ctx:
            noul_tools.generate_research_hypotheses([self.id1])
        self.assertEqual(ctx.exception.code, 400)

        with self.assertRaises(noul_core.HTTPError) as ctx2:
            noul_tools.generate_research_hypotheses([self.id1, self.id1])
        self.assertEqual(ctx2.exception.code, 400)

    def test_cli_hypotheses_pretty(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = noul_cli.main(["hypotheses", self.id1, self.id2, "--max", "2", "--pretty"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("# Autonomous Research Gap & Hypothesis Report:", out)
        self.assertIn("Hypothesis 1:", out)
        self.assertIn("Hypothesis 2:", out)


if __name__ == "__main__":
    unittest.main()
