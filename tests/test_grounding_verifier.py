"""Fase 3 / Etapa 4 (#10): verify_paper_claims — strict attribution, extrapolation alerts, auto claims, CLI."""

import contextlib
import io
import json
import re
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
QUOTED = re.compile(r'^\[pág\. (\d+), (Tabela|Seção) ([^:]+): "(.+)"\]$')


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class GroundingTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch.object(c, name, side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/ground", FULLTEXT, title="Sparse-7B", abstract=ABSTRACT)

    def _verify(self, *claims):
        return t.call("verify_paper_claims", {"paper_id": self.pid, "claims": list(claims)})

    def test_faithful_claims_are_verified_with_exact_citation(self):
        out = self._verify("Sparse-7B scores 56.2 on GSM8K", "Sparse-7B outperforms Llama-2 7B by 33.1 points on MMLU")
        table, section = out["claims"]
        self.assertEqual(table["status"], "verified")
        self.assertEqual(table["citation_tag"], '[pág. 4, Tabela 2: "| Sparse-7B (ours) | 78.4% | 56.2 |"]')
        self.assertEqual(
            (table["page"], table["table_or_section"], table["numbers_checked"]), (4, "Tabela 2", ["56.2"])
        )
        self.assertEqual(section["status"], "verified")
        m = QUOTED.match(section["citation_tag"])
        self.assertEqual(m.groups()[:3], ("6", "Seção", "5 Discussion"))
        self.assertIn(m.group(4), FULLTEXT)  # verbatim
        self.assertEqual(section["numbers_checked"], ["7B", "33.1"])
        self.assertEqual(out["summary"], {"verified": 2, "partially_supported": 0, "extrapolated": 0})

    def test_invented_numbers_are_flagged(self):
        out = self._verify(
            "Sparse-7B reaches 91.7% on MMLU",
            "Sparse-7B reaches 78.4% on MMLU and 99.9 on GSM8K",
            "Sparse-7B will always beat Llama-2 7B by 33.1 points on MMLU",
        )
        invented, partial, overreach = out["claims"]
        self.assertEqual(invented["status"], "extrapolated")
        self.assertTrue(
            invented["citation_tag"].startswith(
                "[EXTRAPOLAÇÃO NÃO LITERAL — sem correspondência exata no texto; trecho mais próximo na pág. "
            )
        )
        self.assertEqual(invented["numbers_checked"], ["91.7%"])
        self.assertEqual(partial["status"], "partially_supported")
        self.assertTrue(partial["citation_tag"].startswith("[pág. 4, Tabela 2:"))
        self.assertEqual(overreach["status"], "partially_supported")  # "will always" is not literal
        self.assertEqual(out["summary"]["extrapolated"], 1)
        self.assertTrue(any("NOT literally supported" in w for w in out["warnings"]))

    def test_auto_extracted_claims_and_validation(self):
        out = t.verify_paper_claims(self.pid)
        self.assertTrue(out["auto_extracted_claims"])
        self.assertEqual(out["claims"][0]["claim"], ABSTRACT)
        self.assertEqual(out["claims"][0]["status"], "verified")
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
        self.assertEqual([x["status"] for x in data["claims"]], ["verified", "extrapolated"])
        code, out = self._cli("ground", self.pid, "--claims", "Sparse-7B scores 56.2 on GSM8K", "--pretty")
        self.assertEqual(code, 0)
        self.assertIn("**verified** — Sparse-7B scores 56.2 on GSM8K", out)
        self.assertIn('[pág. 4, Tabela 2: "| Sparse-7B (ours) | 78.4% | 56.2 |"]', out)


if __name__ == "__main__":
    unittest.main()
