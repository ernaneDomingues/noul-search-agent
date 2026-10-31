"""Phase 1b: the CLI (`noul tool`) and the MCP server expose the same tools with the same results and errors."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support

OFFLINE_CASES = {
    "evidence_meter": {"items": [{"id": "a", "stance": "supportive", "evidence": "rct"}, {"stance": "contrary"}]},
    "configure_noul": {},
    "list_research_dossiers": {},
    "suggest_boolean_queries": {"query": "LLM quantization 4-bit accuracy"},
    "clarify_search_scope": {"query": "creatine"},
}


def setUpModule():
    _support.isolate_environment()


def run_cli(name, args):
    from noul import cli

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["tool", name, "--args", json.dumps(args)])
    return code, out.getvalue(), err.getvalue()


def run_mcp(name, args):
    from noul.server import legacy

    reply = legacy.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )
    return reply["result"]


class ParityTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_registered_tool_is_reachable_from_the_cli(self):
        from noul import cli, registry

        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["tool", "--list"]), 0)
        self.assertEqual(json.loads(out.getvalue())["tools"], list(registry.TOOLS))

    def test_offline_tools_return_the_same_result_via_cli_and_mcp(self):
        for name, args in OFFLINE_CASES.items():
            with self.subTest(tool=name):
                code, out, err = run_cli(name, args)
                self.assertEqual(code, 0, err)
                mcp = run_mcp(name, args)
                self.assertFalse(mcp.get("isError"), mcp)
                self.assertEqual(json.loads(out), json.loads(mcp["content"][0]["text"]))

    def test_every_tool_rejects_bad_arguments_identically(self):
        from noul import registry

        for name in registry.TOOLS:
            with self.subTest(tool=name):
                code, _, err = run_cli(name, {"__bogus__": 1})
                self.assertEqual(code, 1)
                cli_error = json.loads(err)["error"]
                mcp = run_mcp(name, {"__bogus__": 1})
                self.assertTrue(mcp["isError"])
                self.assertIn(cli_error, mcp["content"][0]["text"])
                self.assertIn("Unknown argument(s)", cli_error)


if __name__ == "__main__":
    unittest.main()
