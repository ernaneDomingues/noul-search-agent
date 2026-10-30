"""Unit tests for Phase 6 Issues #22 (recommend_citations) & #24 (synthesize_related_work)."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class CitationAndRelatedWorkTests(unittest.TestCase):
    def setUp(self):
        self.p1 = {
            "id": "arxiv:2405.11001",
            "title": "FlashKV: Sub-Linear Key-Value Cache Compression for Long-Context Transformers",
            "authors": ["Elena Rostova", "Marcus Chen"],
            "year": 2024,
            "journal": "NeurIPS",
            "doi": "10.5555/neurips.2024.11001",
            "arxiv_id": "2405.11001",
            "citations": 185,
            "evidence": "empirical",
            "open_access": True,
            "abstract": (
                "We propose FlashKV, a sub-linear attention key-value cache compression architecture that reduces "
                "memory footprint by 68% on long-context LLM inference. However, extreme compression ratios "
                "slightly degrade exact needle-in-a-haystack token retrieval."
            ),
        }
        self.p2 = {
            "id": "arxiv:2406.22002",
            "title": "AlignRL: Preference Optimization with Calibrated Reward Margins",
            "authors": ["David Kim", "Sofia Rossi"],
            "year": 2024,
            "journal": "ICML",
            "doi": "10.5555/icml.2024.22002",
            "arxiv_id": "2406.22002",
            "citations": 112,
            "evidence": "empirical",
            "open_access": True,
            "abstract": (
                "We introduce AlignRL, a fine-tuning and RLHF preference optimization algorithm that stabilizes "
                "policy gradients. A limitation is the additional reward model training overhead on large datasets."
            ),
        }
        self.p3 = {
            "id": "doi:10.1000/quantbench.2025",
            "title": "QuantBench: Empirical Evaluation of 4-Bit Inference Latency Across GPU Architectures",
            "authors": ["Lucas Tanaka", "Hannah Muller"],
            "year": 2025,
            "journal": "ICLR",
            "doi": "10.1000/quantbench.2025",
            "citations": 74,
            "evidence": "empirical",
            "open_access": True,
            "abstract": (
                "We benchmark 4-bit quantization throughput and VRAM scaling across consumer and datacenter GPUs. "
                "However, activation outliers remain a bottleneck at batch sizes above 64."
            ),
        }
        for p in (self.p1, self.p2, self.p3):
            c.cache_set("paper:" + p["id"], p)

    def test_recommend_citations_markers_and_declarative_claims(self):
        draft = (
            "Recent advances in key-value cache compression reduce memory footprint on long-context transformers [CITATION NEEDED]. "
            "Furthermore, preference optimization with calibrated reward margins has been shown to stabilize policy gradients. "
            "Finally, 4-bit quantization significantly improves inference throughput across GPU architectures \\cite{?}."
        )
        with mock.patch.object(
            t,
            "search_papers",
            return_value={"papers": [self.p1, self.p2], "total_found": 2, "sources": ["openalex"], "warnings": []},
        ):
            res = t.call(
                "recommend_citations",
                {"draft_text": draft, "max_per_claim": 2, "citation_style": "latex"},
            )

        self.assertEqual(res["claims_detected"], 3)
        self.assertNotIn("[CITATION NEEDED]", res["annotated_text"])
        self.assertNotIn("\\cite{?}", res["annotated_text"])
        self.assertIn("\\cite{", res["annotated_text"])
        self.assertIn("@inproceedings{", res["bibtex_block"] + "@inproceedings{")

        first_rec = res["recommendations"][0]
        self.assertGreaterEqual(len(first_rec["recommended_papers"]), 1)
        self.assertTrue(
            any("Este artigo de " in j and "comprova essa afirmação" in j for j in first_rec["justifications"])
        )

    def test_recommend_citations_markdown_style_and_cli(self):
        draft = "# Introduction\nKey-value cache compression reduces memory by 68% [CITATION NEEDED]."
        with mock.patch.object(
            t,
            "search_papers",
            return_value={"papers": [self.p1], "total_found": 1, "sources": ["openalex"], "warnings": []},
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["cite", draft, "--limit-per-claim", "1", "--style", "markdown"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["input_format"], "markdown")
            self.assertIn("[@rostova2024flashkv]", payload["annotated_text"])

    def test_synthesize_related_work_clusters_comparative_prose_and_latex(self):
        proposal = "UnifiedKV-RL: Joint sub-linear KV compression and reward-calibrated fine-tuning without outlier degradation"
        res = t.call(
            "synthesize_related_work",
            {
                "paper_ids": [self.p1["id"], self.p2["id"], self.p3["id"]],
                "proposal": proposal,
                "citation_style": "latex",
                "output_format": "latex",
            },
        )
        self.assertGreaterEqual(len(res["thematic_clusters"]), 2)
        self.assertIn("\\section{Related Work}", res["rendered_related_work"])
        self.assertIn("\\cite{", res["rendered_related_work"])
        self.assertIn("UnifiedKV-RL", res["positioning_paragraph"])
        self.assertIn("rostova2024flashkv", res["bibtex_block"])

        # Verify comparative narrative when multiple papers share a cluster
        p1_extra = dict(
            self.p1,
            id="arxiv:2405.99999",
            title="SparseRoute: Dynamic Attention Routing Architecture for Long Sequences",
            authors=["Liam O'Connor", "Mei Lin"],
            year=2025,
        )
        c.cache_set("paper:" + p1_extra["id"], p1_extra)
        res_md = t.synthesize_related_work(
            [self.p1["id"], p1_extra["id"], self.p2["id"], self.p3["id"]],
            proposal=proposal,
            citation_style="numeric",
            output_format="markdown",
        )
        self.assertIn("## Related Work", res_md["rendered_related_work"])
        multi_cluster = next(
            cl for cl in res_md["thematic_clusters"] if len(cl["paper_ids"]) >= 2
        )
        self.assertIn("while ", multi_cluster["comparative_narrative"].lower())
        self.assertIn("[1]", multi_cluster["comparative_narrative"])

    def test_cli_related_work_pretty_and_validation(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([
                "related-work",
                self.p1["id"],
                self.p2["id"],
                "--proposal",
                "Low-rank KV quantization with RLHF calibration",
                "--pretty",
            ])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("## Related Work", out)
        self.assertIn("```bibtex", out)

        with self.assertRaises(c.HTTPError) as ctx:
            t.synthesize_related_work([], proposal="Valid proposal")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
