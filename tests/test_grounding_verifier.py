"""Fase 3 / Etapa 4 (#10): verify_paper_claims — strict attribution, extrapolation alerts, auto claims, CLI."""

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

ABSTRACT = "We present Sparse-7B, which achieves 78.4% on MMLU and is 3.4x faster than Llama-2 7B."
FULLTEXT = """--- Page 1 ---
Abstract
We present Sparse-7B, which achieves 78.4% on MMLU and is 3.4x faster than Llama-2 7B.
--- Page 4 ---
4 Experiments
Table 2: Main results on reasoning benchmarks.
| Model | MMLU | GSM8K |
| --- | --- | --- |
| Llama-2 7B | 45.3% | 14.6 |
| Sparse-7B (ours) | 78.4% | 56.2 |

--- Page 6 ---
5 Discussion
Sparse-7B outperforms Llama-2 7B by 33.1 points on MMLU while cutting latency 3.4x.
"""


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class GroundingTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/ground", FULLTEXT, title="Sparse-7B", abstract=ABSTRACT)

    def _verify(self, *claims):
        return t.call("verify_paper_claims", {"paper_id": self.pid, "claims": list(claims)})

    def test_faithful_claims_are_located_with_exact_excerpts(self):
        out = self._verify("Sparse-7B scores 56.2 on GSM8K", "Sparse-7B outperforms Llama-2 7B by 33.1 points on MMLU")
        table, section = out["claims"]
        self.assertNotIn("status", table)  # evidence, not a verdict
        self.assertEqual(table["citation_tag"], "[p. 4, Tabela 2]")
        self.assertEqual(table["best_match"]["text"], "| Sparse-7B (ours) | 78.4% | 56.2 |")
        self.assertEqual((table["best_match"]["page"], table["best_match"]["section"]), (4, "Tabela 2"))
        self.assertTrue(table["best_match"]["untrusted"])
        self.assertEqual((table["numbers_found"], table["numbers_missing"]), (["56.2"], []))
        self.assertFalse(table["claim_untrusted"])  # the user's own claim
        self.assertEqual(section["best_match"]["page"], 6)
        self.assertEqual(section["best_match"]["section"], "Seção 5 Discussion")
        self.assertIn(section["best_match"]["text"], " ".join(FULLTEXT.split()))  # verbatim
        self.assertEqual(section["numbers_found"], ["7B", "33.1"])
        self.assertEqual(out["tool"], "verify_paper_claims")
        self.assertEqual(out["method"], "heuristic")
        self.assertTrue(out["rubric"]["criteria"])

    def test_missing_numbers_and_overreach_are_reported_as_facts(self):
        out = self._verify(
            "Sparse-7B reaches 91.7% on MMLU",
            "Sparse-7B reaches 78.4% on MMLU and 99.9 on GSM8K",
            "Sparse-7B will always beat Llama-2 7B by 33.1 points on MMLU",
        )
        invented, partial, overreach = out["claims"]
        self.assertEqual((invented["numbers_found"], invented["numbers_missing"]), ([], ["91.7%"]))
        self.assertEqual((partial["numbers_found"], partial["numbers_missing"]), (["78.4%"], ["99.9"]))
        self.assertTrue(partial["citation_tag"].startswith("[p. 4, Tabela 2"))
        self.assertEqual(overreach["numbers_missing"], [])
        self.assertIn("always", overreach["hedging_cues_missing_in_text"])  # "will always" is not literal
        self.assertIn("will", overreach["hedging_cues_missing_in_text"])

    def test_auto_extracted_claims_and_validation(self):
        out = t.verify_paper_claims(self.pid)
        self.assertTrue(out["auto_extracted_claims"])
        self.assertEqual(out["claims"][0]["claim"], ABSTRACT)
        self.assertTrue(out["claims"][0]["claim_untrusted"])  # auto-extracted from the paper
        self.assertEqual(out["claims"][0]["numbers_missing"], [])
        self.assertTrue(all(not cl["claim"].startswith(("Table", "|")) for cl in out["claims"]))
        for bad in ({"paper_id": self.pid, "claims": "x"}, {"paper_id": self.pid, "claims": [1]}, {"claims": []}):
            with self.subTest(bad=bad), self.assertRaises(c.HTTPError) as ctx:
                t.call("verify_paper_claims", bad)
            self.assertEqual(ctx.exception.code, 400)

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_cli(self):
        code, out = self._cli("ground", self.pid, "--claims", "Sparse-7B scores 56.2 on GSM8K; Sparse-7B hits 12% ")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([x["numbers_missing"] for x in data["claims"]], [[], ["12%"]])
        code, out = self._cli("ground", self.pid, "--claims", "Sparse-7B scores 56.2 on GSM8K", "--pretty")
        self.assertEqual(code, 0)
        self.assertIn("> Sparse-7B scores 56.2 on GSM8K", out)
        self.assertIn("Location: [p. 4, Tabela 2]", out)
        self.assertIn('<untrusted_source id="doi:10.1000/ground">\n| Sparse-7B (ours) | 78.4% | 56.2 |', out)


if __name__ == "__main__":
    unittest.main()
