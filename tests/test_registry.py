"""Phase 1b: the tool registry is the single source of truth and keeps the v1 MCP contract."""

import json
import unittest
from pathlib import Path
from unittest import mock

import _support

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tools_list_v1.json"


def setUpModule():
    _support.isolate_environment()


class ContractTests(unittest.TestCase):
    def test_tools_list_matches_the_v1_contract(self):
        """inputSchema, descriptions and the set of tools are identical to the hand-written v1 schemas."""
        from noul.server import legacy

        expected = {t["name"]: t for t in json.loads(FIXTURE.read_text(encoding="utf-8"))}
        actual = {t["name"]: t for t in legacy.tools_list()}
        self.assertEqual(sorted(actual), sorted(expected))
        for name in expected:
            with self.subTest(tool=name):
                self.assertEqual(actual[name]["description"], expected[name]["description"])
                self.assertEqual(actual[name]["inputSchema"], expected[name]["inputSchema"])

    def test_every_tool_function_is_registered_once_with_a_matching_signature(self):
        import inspect

        from noul import api, registry

        self.assertEqual(len(registry.REGISTRY), 41)
        for name, tool in registry.REGISTRY.items():
            with self.subTest(tool=name):
                self.assertIs(getattr(api, name), tool.fn)
                params = list(inspect.signature(tool.fn).parameters)
                self.assertEqual(params, list(tool.input.model_fields), "function params == input model fields")

    def test_write_tools_come_from_the_registry(self):
        from noul import registry
        from noul.server import legacy

        self.assertEqual(
            legacy.WRITE_TOOLS,
            {"download_papers", "save_research_dossier", "export_obsidian_notes", "configure_noul"},
        )
        self.assertEqual(legacy.WRITE_TOOLS, registry.write_tools())

    def test_duplicate_registration_is_rejected(self):
        from noul import api, registry  # noqa: F401  (api registers the tools)
        from noul.schemas.tool_inputs import ConfigureNoulInput

        with self.assertRaises(ValueError):
            registry.tool("configure_noul", description="dup", input=ConfigureNoulInput)(lambda: None)


class CallValidationTests(unittest.TestCase):
    def test_errors_keep_the_v1_messages(self):
        from noul import registry
        from noul.errors import HTTPError

        cases = [
            ("nope", {}, 404, "Unknown tool nope"),
            ("search_papers", [], 400, "must be a JSON object"),
            ("search_papers", {}, 400, "Missing required argument(s) for search_papers: query"),
            ("search_papers", {"query": "x", "typo": 1}, 400, "Unknown argument(s) for search_papers: typo"),
            ("search_papers", {"query": 3}, 400, "Invalid argument(s) for search_papers: 'query'"),
            ("search_papers", {"query": "x", "sort": "random"}, 400, "'sort'"),
            ("evidence_meter", {"items": [{"id": "a"}]}, 400, "'items.0.stance'"),
        ]
        for name, args, code, text in cases:
            with self.subTest(name=name, args=args), self.assertRaises(HTTPError) as ctx:
                registry.call(name, args)
            self.assertEqual(ctx.exception.code, code)
            self.assertIn(text, str(ctx.exception))

    def test_only_sent_arguments_reach_the_function(self):
        from noul import api, registry  # noqa: F401

        tool = registry.REGISTRY["search_papers"]
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return {"ok": True}

        replacement = registry.Tool(tool.name, tool.description, tool.input, fake, tool.writes, tool.network)
        with mock.patch.dict(registry.REGISTRY, {"search_papers": replacement}):
            self.assertEqual(registry.call("search_papers", {"query": "q", "limit": "5"}), {"ok": True})
        self.assertEqual(seen, {"query": "q", "limit": 5})

    def test_nested_models_are_passed_as_plain_dicts(self):
        from noul import registry

        out = registry.call(
            "evidence_meter",
            {"items": [{"id": "x", "stance": "supportive", "evidence": "rct"}, {"stance": "contrary"}]},
        )
        self.assertEqual(out["total_papers"], 2)


class LoggingTests(unittest.TestCase):
    def test_tool_calls_log_to_stderr_only(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from noul import log, registry

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            handler = __import__("logging").StreamHandler(err)
            log.logger.addHandler(handler)
            log.logger.setLevel("DEBUG")
            try:
                registry.call("configure_noul", {})
            finally:
                log.logger.removeHandler(handler)
                log.logger.setLevel("WARNING")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("tool done name=configure_noul trace_id=", err.getvalue())


if __name__ == "__main__":
    unittest.main()
