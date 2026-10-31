"""Fase 3 / Etapa 3 (#09): estimate_hardware_specs — VRAM per precision, KV cache, MoE, reported hardware, CLI."""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support  # noqa: E402

SMALL = (
    "We release Atlas-7B, a 7B decoder-only language model. We compare it with GPT-3 175B and Llama-2 13B. "
    "Atlas-7B was trained on 2T tokens using 8 NVIDIA H100 GPUs."
)
LARGE = (
    "Titan-405B: Scaling Dense Transformers",
    "We introduce a 405B parameter dense transformer with 126 layers and 8 KV heads (head dim 128). "
    "Pre-training was performed on 64 NVIDIA A100 80GB GPUs for 90 days. We also release 8B and 70B variants.",
)
MOE = (
    "We present Mixtral, a sparse mixture of experts (MoE) model with 8x7B experts and top-2 routing. "
    "The model has 47B total, 13B active parameters per token. Training used TPU v4-1024."
)


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class HardwareEstimatorTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch.object(c, name, side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.small = _support.seed_fulltext_paper(c, "doi:10.1000/atlas7b", SMALL, title="Atlas-7B: an open model")
        self.large = _support.seed_fulltext_paper(c, "doi:10.1000/titan", LARGE[1], title=LARGE[0])
        self.moe = _support.seed_fulltext_paper(c, "doi:10.1000/mixtral", MOE, title="Mixtral of Experts")

    def test_small_dense_model_runs_on_consumer_gpu(self):
        out = t.call("estimate_hardware_specs", {"paper_id": self.small})
        self.assertEqual((out["total_parameters_billion"], out["architecture_type"]), (7.0, "dense"))
        self.assertIsNone(out["active_parameters_billion"])
        self.assertEqual(out["detected_model_sizes_b"], [7.0, 13.0, 175.0])
        self.assertGreater(out["fp16"]["weights_gb"], out["int8"]["weights_gb"])
        self.assertGreater(out["int8"]["weights_gb"], out["int4"]["weights_gb"])
        self.assertEqual((out["fp16"]["weights_gb"], out["int4"]["weights_gb"]), (14.0, 3.5))
        # 2 * 32 layers * 32 heads * 128 dims * 8192 tokens * 2 bytes = 4 GiB
        self.assertEqual(out["fp16"]["kv_cache_8k_context_gb"], 4.0)
        self.assertEqual(out["int4"]["kv_cache_8k_context_gb"], 1.0)
        self.assertEqual(out["int4"]["recommended_min_vram_gb"], round((3.5 + 1.0) * 1.15, 2))
        self.assertEqual(out["operational_tier"], "edge_consumer_gpu")
        self.assertEqual(out["reported_training_hardware"], "8 NVIDIA H100 GPUs")

    def test_large_model_needs_datacenter(self):
        out = t.estimate_hardware_specs(self.large)
        self.assertEqual(out["total_parameters_billion"], 405.0)
        self.assertEqual(
            out["kv_cache_assumptions"], {"layers": 126, "kv_heads": 8, "head_dim": 128, "source": "paper text"}
        )
        self.assertEqual(out["int4"]["weights_gb"], 202.5)
        self.assertEqual(out["operational_tier"], "datacenter_cluster")
        self.assertEqual(out["reported_training_hardware"], "64 NVIDIA A100 80GB GPUs")
        self.assertIn(70.0, out["detected_model_sizes_b"])
        # Explicit override (70B, longer context) -> workstation tier, KV scales with context length.
        over = t.estimate_hardware_specs(self.large, total_params_b=70, context_length=16384)
        self.assertEqual((over["total_parameters_billion"], over["operational_tier"]), (70.0, "workstation"))
        self.assertEqual(over["fp16"]["kv_cache_8k_context_gb"], 2 * out["fp16"]["kv_cache_8k_context_gb"])

    def test_moe_model(self):
        out = t.estimate_hardware_specs(self.moe)
        self.assertEqual(out["architecture_type"], "moe")
        self.assertEqual((out["total_parameters_billion"], out["active_parameters_billion"]), (47.0, 13.0))
        self.assertEqual(out["reported_training_hardware"], "TPU v4-1024")
        self.assertEqual(out["operational_tier"], "workstation")
        _support.seed_fulltext_paper(c, "doi:10.1000/moe2", "An 8x7B sparse MoE model.", title="MoE")
        derived = t.estimate_hardware_specs("doi:10.1000/moe2")
        self.assertEqual((derived["total_parameters_billion"], derived["active_parameters_billion"]), (46.7, 12.7))
        _support.seed_fulltext_paper(
            c, "doi:10.1000/ds", "DeepSeek has 671B parameters with 37B activated.", title="DeepSeek"
        )
        ds = t.estimate_hardware_specs("doi:10.1000/ds")
        self.assertEqual((ds["architecture_type"], ds["active_parameters_billion"]), ("moe", 37.0))

    def test_defaults_and_special_architectures(self):
        _support.seed_fulltext_paper(
            c,
            "doi:10.1000/bert",
            None,
            open_access=False,
            title="Fine-tuning BERT-base",
            abstract="We fine-tune BERT-base, an encoder-only model, for NER.",
        )
        bert = t.estimate_hardware_specs("doi:10.1000/bert")
        self.assertEqual((bert["total_parameters_billion"], bert["architecture_type"]), (0.11, "encoder_only"))
        self.assertEqual(bert["fp16"]["kv_cache_8k_context_gb"], round(bert["fp16"]["weights_gb"] * 0.05, 2))
        _support.seed_fulltext_paper(
            c,
            "doi:10.1000/none",
            None,
            open_access=False,
            title="A study",
            abstract="A Mamba state space model with 350M parameters.",
        )
        ssm = t.estimate_hardware_specs("doi:10.1000/none")
        self.assertEqual((ssm["total_parameters_billion"], ssm["architecture_type"]), (0.35, "rnn_ssm"))
        _support.seed_fulltext_paper(
            c, "doi:10.1000/blank", None, open_access=False, title="On things", abstract="Nothing about sizes here."
        )
        blank = t.estimate_hardware_specs("doi:10.1000/blank")
        self.assertEqual(blank["total_parameters_billion"], 7.0)
        self.assertTrue(any("documented default" in n for n in blank["notes"]))
        for kwargs in (
            {"total_params_b": -1},
            {"total_params_b": "big"},
            {"context_length": 0},
            {"total_params_b": 7, "active_params_b": 9},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(c.HTTPError) as ctx:
                t.estimate_hardware_specs(self.small, **kwargs)
            self.assertEqual(ctx.exception.code, 400)

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_cli(self):
        code, out = self._cli("hardware", self.small)
        self.assertEqual((code, json.loads(out)["operational_tier"]), (0, "edge_consumer_gpu"))
        code, out = self._cli(
            "hardware", self.small, "--params-b", "405", "--active-params-b", "37", "--context-length", "4096"
        )
        data = json.loads(out)
        self.assertEqual(
            (
                data["total_parameters_billion"],
                data["active_parameters_billion"],
                data["architecture_type"],
                data["context_length"],
            ),
            (405.0, 37.0, "moe", 4096),
        )
        code, out = self._cli("hardware", self.small, "--pretty")
        self.assertEqual(code, 0)
        self.assertIn("Tier: **edge_consumer_gpu**", out)
        self.assertIn("| INT4 | 3.5 | 1.0 | 5.17 |", out)


if __name__ == "__main__":
    unittest.main()
