"""Fase 3 / Etapa 6 (#13): extract_prompts_and_decoding — verbatim prompt templates, decoding hyperparameters,
JSON schemas and the `noul prompts` CLI."""

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

FULLTEXT = """--- Page 1 ---
3 Prompting setup
System Prompt: You are an expert research assistant. Answer using only the provided context.

User Prompt:
Context: {context}
Question: {question}
Let's think step by step.

For instruction tuning we use the following template:
```
### Instruction
Summarize the following input in one sentence.
### Input
{{input}}
```
--- Page 2 ---
4 Decoding
We sample with temperature = 0.7, top-p = 0.95 and top-k = 50, with a max new tokens of 512.
Greedy runs use repetition_penalty=1.1, a beam size of 4 and random seed 42. We train for T = 1000 steps.
The model must answer in JSON:
```json
{"answer": "<string>", "confidence": 0.0}
```
"""


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class PromptExtractorTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pid = _support.seed_fulltext_paper(c, "doi:10.1000/prompts", FULLTEXT, title="Prompting paper")

    def _extract(self, pid=None):
        return t.call("extract_prompts_and_decoding", {"paper_id": pid or self.pid})

    def test_placeholders_are_preserved_verbatim_and_listed(self):
        out = self._extract()
        system, user, template = out["prompts"]
        self.assertEqual((system["type"], system["page"]), ("system", 1))
        self.assertEqual(
            system["text"], "You are an expert research assistant. Answer using only the provided context."
        )
        self.assertEqual(user["type"], "user")
        self.assertEqual(user["text"], "Context: {context}\nQuestion: {question}\nLet's think step by step.")
        self.assertEqual(user["variables"], ["context", "question"])
        self.assertEqual(template["type"], "code_block")
        self.assertIn("{{input}}", template["text"])
        self.assertEqual(template["variables"], ["input"])
        self.assertEqual(out["variables"], ["context", "question", "input"])

    def test_decoding_hyperparameters(self):
        hp = self._extract()["hyperparameters"]
        self.assertEqual(hp["temperature"], 0.7)  # `T = 1000 steps` is not a temperature
        self.assertEqual(hp["top_p"], 0.95)
        self.assertEqual(hp["top_k"], 50)
        self.assertEqual(hp["max_tokens"], 512)
        self.assertEqual((hp["repetition_penalty"], hp["beam_size"], hp["seed"]), (1.1, 4, 42))
        self.assertIsInstance(hp["top_k"], int)

    def test_alternative_hyperparameter_phrasings(self):
        pid = _support.seed_fulltext_paper(
            c,
            "doi:10.1000/prompts-alt",
            (
                "We decode with a temp of 0.0. Sampling baselines use T = 0.2, nucleus sampling p = 0.9, top_k=40 and "
                "max_tokens = 2048.\n"
            ),
            title="Alt",
        )
        out = self._extract(pid)
        hp = out["hyperparameters"]
        self.assertEqual(hp["temperature"], [0.0, 0.2])
        self.assertEqual((hp["top_p"], hp["top_k"], hp["max_tokens"]), (0.9, 40, 2048))
        self.assertIn(
            {"parameter": "top_p", "value": 0.9, "quote": "nucleus sampling p = 0.9", "page": 1},
            out["hyperparameter_evidence"],
        )

    def test_json_schemas_are_captured(self):
        self.assertEqual(self._extract()["json_schemas"], ['{"answer": "<string>", "confidence": 0.0}'])

    def test_paper_without_prompts_warns(self):
        pid = _support.seed_fulltext_paper(
            c, "doi:10.1000/prompts-none", "We study sparse transformers.\n", title="None"
        )
        out = self._extract(pid)
        self.assertEqual((out["prompts"], out["hyperparameters"], out["json_schemas"]), ([], {}, []))
        self.assertTrue(any("No prompt templates" in w for w in out["warnings"]))

    def test_cli_prompts(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["prompts", self.pid]), 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["variables"], ["context", "question", "input"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["prompts", self.pid, "--pretty"]), 0)
        pretty = buf.getvalue()
        self.assertIn("# Prompts & decoding: Prompting paper", pretty)
        self.assertIn("Question: {question}", pretty)
        self.assertIn("- temperature: 0.7", pretty)


if __name__ == "__main__":
    unittest.main()
