"""Fase 4 / Etapa 3 (#15): diff_arxiv_versions — arXiv version comparison, metric deltas, claim attenuations,
new baselines/datasets, authorship changes and the `noul diff` CLI."""
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

ATOM_V1 = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <published>2024-01-10T00:00:00Z</published>
    <updated>2024-01-10T00:00:00Z</updated>
    <title>NoulLM: A Novel State-of-the-Art Reasoner</title>
    <summary>
      Our model achieves state-of-the-art outperforming all baselines on reasoning tasks.
      We report MMLU: 78.2% and GSM8K: 71.0% with sample size n = 100.
    </summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
  </entry>
</feed>
"""

ATOM_V2 = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <published>2024-01-10T00:00:00Z</published>
    <updated>2024-03-15T00:00:00Z</updated>
    <title>NoulLM: An Empirical Study of Reasoning Models</title>
    <summary>
      Our model achieves competitive performance comparable to recent baselines on reasoning tasks.
      We report MMLU: 76.5% and GSM8K: 74.0% with sample size n = 250, and add evaluations on HumanEval and GPQA against GPT-4 and Claude-3.
    </summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <author><name>Carol Lee</name></author>
  </entry>
</feed>
"""


def setUpModule():
    global c, t, cli, arxiv_client
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t
    from noul.providers import arxiv_client as _ac

    arxiv_client = _ac


class ArxivDiffTests(unittest.TestCase):
    def setUp(self):
        patcher_json = mock.patch.object(c, "_http_json", side_effect=_support.no_network)
        patcher_json.start()
        self.addCleanup(patcher_json.stop)

    def _mock_atom_get(self, url, *args, **kwargs):
        if "2401.12345v1" in url:
            return ATOM_V1
        if "2401.12345v2" in url:
            return ATOM_V2
        raise AssertionError(f"Unexpected URL: {url}")

    def test_parse_version_args_and_validation(self):
        self.assertEqual(
            arxiv_client.parse_arxiv_version_args("arxiv:2401.12345v1", "v1", "v2"),
            ("2401.12345", "v1", "v2"),
        )
        self.assertEqual(
            arxiv_client.parse_arxiv_version_args("2401.12345v1,2401.12345v2"),
            ("2401.12345", "v1", "v2"),
        )
        self.assertEqual(
            arxiv_client.parse_arxiv_version_args("2401.12345v1..v3"),
            ("2401.12345", "v1", "v3"),
        )
        self.assertEqual(
            arxiv_client.parse_arxiv_version_args("hep-th/9901001", "1", "2"),
            ("hep-th/9901001", "v1", "v2"),
        )
        with self.assertRaises(c.HTTPError):
            arxiv_client.parse_arxiv_version_args("not-an-arxiv-id")
        with self.assertRaises(c.HTTPError):
            arxiv_client.parse_arxiv_version_args("2401.12345", "v1", "v1")

    def test_diff_via_atom_api_detects_metrics_attenuation_authors_and_baselines(self):
        with mock.patch.object(c, "_http_get", side_effect=self._mock_atom_get):
            out = t.call("diff_arxiv_versions", {"arxiv_id": "2401.12345", "from_version": "v1", "to_version": "v2"})

        self.assertEqual(out["arxiv_base_id"], "2401.12345")
        self.assertEqual((out["from_version"], out["to_version"]), ("v1", "v2"))
        self.assertTrue(out["title_changed"])
        self.assertEqual(out["authors_added"], ["Carol Lee"])
        self.assertEqual(out["authors_removed"], [])

        deltas = {d["metric"]: d for d in out["metric_deltas"]}
        self.assertIn("MMLU", deltas)
        self.assertAlmostEqual(deltas["MMLU"]["from_value"], 78.2)
        self.assertAlmostEqual(deltas["MMLU"]["to_value"], 76.5)
        self.assertAlmostEqual(deltas["MMLU"]["delta"], -1.7)
        self.assertIn("GSM8K", deltas)
        self.assertAlmostEqual(deltas["GSM8K"]["delta"], 3.0)

        categories = [cs["category"] for cs in out["claim_shifts"]]
        self.assertIn("claim_attenuation", categories)
        attenuated = [cs for cs in out["claim_shifts"] if cs["category"] == "claim_attenuation"]
        self.assertTrue(any("state-of-the-art" in (cs.get("before") or "").lower() for cs in attenuated))

        for expected_new in ("HumanEval", "GPQA", "GPT-4", "Claude-3"):
            self.assertIn(expected_new, out["new_baselines_or_datasets"])

        md = out["markdown_diff"]
        self.assertIn("## ArXiv Version Diff: 2401.12345 (v1 -> v2)", md)
        self.assertIn("### Metadata & Authorship Changes", md)
        self.assertIn("### Metric & Table Number Changes", md)
        self.assertIn("### Claim Shifts & Attenuations", md)
        self.assertIn("### New Baselines & Datasets", md)
        self.assertIn("### Text Diff Highlights", md)

    def test_diff_with_cached_fulltext_tables_and_cli(self):
        c.cache_set("paper:arxiv:2402.54321v1", {
            "id": "arxiv:2402.54321v1",
            "title": "FastNet v1",
            "abstract": "Our method proves superior to all baselines.",
            "authors": ["Dan Brown"],
        })
        c.cache_set("ft:arxiv:2402.54321v1", {
            "text": (
                "Table 1: Benchmark results\n"
                "| Model | MMLU | GSM8K |\n"
                "| --- | --- | --- |\n"
                "| Llama-2 | 65.0 | 55.0 |\n"
                "| Ours | 75.0 | 68.0 |\n"
            ),
        })
        c.cache_set("paper:arxiv:2402.54321v2", {
            "id": "arxiv:2402.54321v2",
            "title": "FastNet v2",
            "abstract": "Our method suggests promising empirical results.",
            "authors": ["Dan Brown", "Eva Green"],
        })
        c.cache_set("ft:arxiv:2402.54321v2", {
            "text": (
                "Table 1: Benchmark results\n"
                "| Model | MMLU | GSM8K |\n"
                "| --- | --- | --- |\n"
                "| Llama-2 | 65.0 | 55.0 |\n"
                "| Llama-3 | 74.0 | 72.0 |\n"
                "| Ours | 77.5 | 70.0 |\n"
            ),
        })

        with mock.patch.object(c, "_http_get", side_effect=_support.no_network):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(["diff", "2402.54321", "--from", "v1", "--to", "v2", "--pretty"]), 0)
            pretty = buf.getvalue()
            self.assertIn("## ArXiv Version Diff: 2402.54321 (v1 -> v2)", pretty)
            self.assertIn("Eva Green", pretty)
            self.assertIn("Llama-3", pretty)

            buf_json = io.StringIO()
            with contextlib.redirect_stdout(buf_json):
                self.assertEqual(cli.main(["diff", "2402.54321v1,2402.54321v2"]), 0)
            data = json.loads(buf_json.getvalue())
            deltas = {d["metric"]: d for d in data["metric_deltas"]}
            self.assertAlmostEqual(deltas["MMLU"]["delta"], 2.5)
            self.assertIn("Llama-3", data["new_baselines_or_datasets"])


if __name__ == "__main__":
    unittest.main()
