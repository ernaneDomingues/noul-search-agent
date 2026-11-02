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
        """Every v1 tool still exists with the same inputSchema (and description, except the listed changes);
        tools added since v1 are listed with their phase."""
        from noul.server import legacy

        expected = {t["name"]: t for t in json.loads(FIXTURE.read_text(encoding="utf-8"))}
        actual = {t["name"]: t for t in legacy.tools_list()}
        added_since_v1 = {
            name: "3b-2: research projects"
            for name in (
                "project_create",
                "project_list",
                "project_get",
                "project_add_papers",
                "project_set_status",
                "project_note",
            )
        }
        self.assertEqual(sorted(actual), sorted([*expected, *added_since_v1]))
        # Intentional description changes since v1, each with the phase that made it.
        changed_descriptions = {
            "detect_paper_domain": "2a: venue signal and `method` values",
            **dict.fromkeys(
                (
                    "review_paper",
                    "review_paper_scientific",
                    "verify_paper_claims",
                    "detect_failure_modes",
                    "audit_manuscript_draft",
                    "parse_and_draft_rebuttal",
                    "audit_submission_readiness",
                ),
                "3a: evidence, not a verdict (AnalysisResult)",
            ),
            "recommend_citations": "3b-1: English, non-judgemental justifications",
            "get_fulltext": "3b-1: BM25 passages with section and page",
            "query_fulltexts": "3b-1: BM25 passages with section and page",
            "save_research_dossier": "3b-2: the dossier exports the research project with the same slug",
        }
        for name in expected:
            with self.subTest(tool=name):
                if name not in changed_descriptions:
                    self.assertEqual(actual[name]["description"], expected[name]["description"])
                self.assertEqual(actual[name]["inputSchema"], expected[name]["inputSchema"])

    def test_every_tool_function_is_registered_once_with_a_matching_signature(self):
        import inspect

        from noul import api, registry

        self.assertEqual(len(registry.REGISTRY), 47)  # 41 v1 tools + 6 project_* tools (3b-2)
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
            {
                "download_papers",
                "save_research_dossier",
                "export_obsidian_notes",
                "configure_noul",
                "project_create",
                "project_add_papers",
                "project_set_status",
                "project_note",
            },
        )
        self.assertEqual(legacy.WRITE_TOOLS, registry.write_tools())

    def test_write_arguments_are_blocked_over_http_without_write_permission(self):
        """sanitize_bibtex is read-only unless `output_path` is set; with it, HTTP without write access refuses."""
        from noul import registry
        from noul.server import legacy

        self.assertTrue(registry.call_writes("sanitize_bibtex", {"bibtex_input": "x", "output_path": "out.bib"}))
        self.assertFalse(registry.call_writes("sanitize_bibtex", {"bibtex_input": "x"}))
        self.assertFalse(registry.call_writes("sanitize_bibtex", {"bibtex_input": "x", "output_path": ""}))
        self.assertTrue(registry.call_writes("download_papers", {"ids": ["a"]}))
        self.assertFalse(registry.call_writes("no_such_tool", {}))

        def call(args, allow_write):
            msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sanitize_bibtex"}}
            msg["params"]["arguments"] = args
            return legacy.handle(msg, allow_write=allow_write)["result"]

        blocked = call({"bibtex_input": "@article{a, title={T}}", "output_path": "clean.bib"}, allow_write=False)
        self.assertTrue(blocked["isError"])
        self.assertIn("PermissionError", blocked["content"][0]["text"])
        listed = {
            t["name"]
            for t in legacy.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, False)["result"]["tools"]
        }
        self.assertIn("sanitize_bibtex", listed)  # the read-only form stays available over HTTP

    def test_error_logs_do_not_echo_exception_messages(self):
        import io
        import logging

        from noul import log, registry
        from noul.errors import HTTPError

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        log.logger.addHandler(handler)
        log.logger.setLevel("INFO")
        try:
            with self.assertRaises(HTTPError):
                registry.call("configure_noul", {"email": "not-an-email-secret@"})
        finally:
            log.logger.removeHandler(handler)
            log.logger.setLevel("WARNING")
        self.assertIn("tool error name=configure_noul", stream.getvalue())
        self.assertNotIn("secret", stream.getvalue())

    def test_profiles_are_cumulative_and_cover_every_tool(self):
        from noul import registry

        names = {p: {t["name"] for t in registry.catalog(p)} for p in registry.PROFILES}
        self.assertEqual(len(names["all"]), 47)
        self.assertEqual(len(names["research"]), 23)
        self.assertLess(names["research"], names["review"])
        self.assertLess(names["research"], names["write"])
        self.assertEqual(names["review"] | names["write"], names["all"])
        self.assertIn("search_papers", names["research"])
        self.assertNotIn("review_paper", names["write"])
        with self.assertRaises(ValueError):
            registry.catalog("everything")

    def test_annotations_follow_the_write_and_network_flags(self):
        from noul import registry

        tools = {t["name"]: t["annotations"] for t in registry.catalog()}
        self.assertEqual(
            tools["search_papers"],
            {
                "title": "Search papers",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )
        self.assertFalse(tools["download_papers"]["readOnlyHint"])
        self.assertFalse(tools["sanitize_bibtex"]["readOnlyHint"])  # writes with output_path
        self.assertTrue(tools["configure_noul"]["destructiveHint"])
        self.assertFalse(tools["evidence_meter"]["openWorldHint"])
        self.assertFalse(tools["suggest_boolean_queries"]["openWorldHint"])  # offline heuristics
        self.assertTrue(tools["download_papers"]["idempotentHint"])  # skips existing files
        self.assertTrue(tools["export_obsidian_notes"]["destructiveHint"])  # overwrites vault notes
        self.assertTrue(tools["sanitize_bibtex"]["destructiveHint"])  # output_path overwrites a file
        self.assertEqual(tools["sanitize_bibtex"]["title"], "Sanitize BibTeX")

    def test_output_schemas_describe_the_returned_models(self):
        import jsonschema

        from noul import registry

        with_schema = {t["name"] for t in registry.catalog() if t["outputSchema"]}
        self.assertEqual(len(with_schema), 21)
        sample = registry.call(
            "generate_manuscript_blueprint", {"topic": "Sparse attention", "hypothesis": "It is faster"}
        )
        jsonschema.validate(json.loads(json.dumps(sample)), registry.output_schema("generate_manuscript_blueprint"))
        bib = registry.call("sanitize_bibtex", {"bibtex_input": "@article{a, title={T}, author={A}, year={2020}}"})
        jsonschema.validate(json.loads(json.dumps(bib)), registry.output_schema("sanitize_bibtex"))

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

    def test_evidence_meter_keeps_v1_item_tolerance(self):
        from noul import registry

        out = registry.call(
            "evidence_meter",
            {"items": [{"id": 7, "stance": "supportive", "title": "extra keys are ignored"}, {"id": "b"}, "junk"]},
        )
        self.assertEqual(out["total_papers"], 1)
        self.assertEqual(len(out["skipped"]), 2)

    def test_enum_case_and_null_defaults_follow_v1(self):
        from noul import api, registry  # noqa: F401

        tool = registry.REGISTRY["search_papers"]
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return {}

        replacement = registry.Tool(tool.name, tool.description, tool.input, fake, tool.writes, tool.network)
        with mock.patch.dict(registry.REGISTRY, {"search_papers": replacement}):
            registry.call("search_papers", {"query": "q", "sort": "Citations", "study_types": ["RCT"], "limit": None})
        self.assertEqual(seen, {"query": "q", "sort": "citations", "study_types": ["rct"]})

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
