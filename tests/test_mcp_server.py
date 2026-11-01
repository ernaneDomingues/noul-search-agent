"""Phase 2a: the SDK-based MCP server (noul.server.mcp), exercised with the SDK's own in-memory client."""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import anyio
import mcp.types as types
from mcp import Client
from mcp.client._memory import InMemoryTransport

import _support


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


def run(coro_fn, *args):
    return anyio.run(coro_fn, *args)


class McpServerTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def client(self, server, **kwargs):
        return Client(InMemoryTransport(server), **kwargs)

    def test_tools_follow_profile_and_write_permission(self):
        from noul.server.mcp import build_server

        async def names(profile, allow_write):
            async with self.client(build_server(profile=profile, allow_write=allow_write)) as c:
                return {t.name for t in (await c.list_tools()).tools}

        self.assertEqual(len(run(names, "all", True)), 41)
        research = run(names, "research", True)
        self.assertEqual(len(research), 17)
        self.assertTrue({"save_research_dossier", "download_papers"} <= research)  # the research flow saves
        self.assertIn("search_papers", research)
        self.assertNotIn("review_paper", research)
        no_write = run(names, "all", False)
        self.assertFalse(no_write & {"download_papers", "save_research_dossier", "configure_noul"})
        self.assertIn("sanitize_bibtex", no_write)  # read-only unless output_path is set

    def test_tool_metadata_annotations_and_output_schema(self):
        from noul.server.mcp import build_server

        async def tools():
            async with self.client(build_server()) as c:
                return {t.name: t for t in (await c.list_tools()).tools}

        tools = run(tools)
        search = tools["search_papers"]
        self.assertEqual(search.title, "Search papers")
        self.assertTrue(search.annotations.read_only_hint)
        self.assertTrue(search.annotations.open_world_hint)
        self.assertFalse(tools["download_papers"].annotations.read_only_hint)
        self.assertTrue(tools["configure_noul"].annotations.destructive_hint)
        self.assertIsNotNone(tools["sanitize_bibtex"].output_schema)
        self.assertIsNone(search.output_schema)
        self.assertEqual(search.input_schema["required"], ["query"])

    def test_call_returns_structured_content_and_errors_are_flagged(self):
        from noul.server.mcp import build_server

        async def calls():
            async with self.client(build_server()) as c:
                ok = await c.call_tool("evidence_meter", {"items": [{"stance": "supportive"}]})
                bad = await c.call_tool("search_papers", {"query": 3})
                unknown = await c.call_tool("nope", {})
                return ok, bad, unknown

        ok, bad, unknown = run(calls)
        self.assertFalse(ok.is_error)
        self.assertEqual(ok.structured_content["total_papers"], 1)
        self.assertEqual(json.loads(ok.content[0].text), ok.structured_content)
        self.assertTrue(bad.is_error)
        self.assertIn("Invalid argument(s) for search_papers", bad.content[0].text)
        self.assertTrue(unknown.is_error)
        self.assertTrue(bad.content[0].text.startswith("InvalidArguments:"))  # not mistaken for a network error
        self.assertTrue(unknown.content[0].text.startswith("NotFound:"))

    def test_write_calls_are_refused_without_write_permission(self):
        from noul.server.mcp import build_server

        async def calls():
            async with self.client(build_server(allow_write=False)) as c:
                return (
                    await c.call_tool(
                        "sanitize_bibtex", {"bibtex_input": "@misc{a,title={T}}", "output_path": "x.bib"}
                    ),
                    await c.call_tool("download_papers", {"ids": ["doi:10.1/x"]}),
                )

        writes_file, hidden = run(calls)
        self.assertTrue(writes_file.is_error)
        self.assertIn("PermissionError", writes_file.content[0].text)
        self.assertTrue(hidden.is_error)
        self.assertFalse(os.path.exists(os.path.join(ENV["NOUL_WORKSPACE"], "x.bib")))

    def test_profile_hides_tools_from_calls_too(self):
        from noul.server.mcp import build_server

        async def call():
            async with self.client(build_server(profile="research")) as c:
                return await c.call_tool("review_paper", {"paper_id": "x"})

        result = run(call)
        self.assertTrue(result.is_error)
        self.assertIn("not in the 'research' tool profile", result.content[0].text)

    def test_prompts(self):
        from noul.server.mcp import build_server

        async def prompts():
            async with self.client(build_server()) as c:
                listed = await c.list_prompts()
                got = await c.get_prompt("noul-research", {"question": "Does creatine improve memory?"})
                return listed, got

        listed, got = run(prompts)
        self.assertEqual([p.name for p in listed.prompts], ["noul-research", "noul-review", "noul-write"])
        text = got.messages[0].content.text
        self.assertIn("Does creatine improve memory?", text)
        self.assertIn("evidence_meter", text)

    def test_prompts_follow_the_profile_and_require_their_arguments(self):
        from noul.server.mcp import build_server

        async def go():
            async with self.client(build_server(profile="research")) as c:
                names = [p.name for p in (await c.list_prompts()).prompts]
                try:
                    await c.get_prompt("noul-research", {})
                    missing_ok = True
                except Exception:
                    missing_ok = False
                return names, missing_ok

        names, missing_ok = run(go)
        self.assertEqual(names, ["noul-research"])
        self.assertFalse(missing_ok)

    def test_a_result_that_would_not_match_the_output_schema_becomes_an_error(self):
        from noul.schemas.tool_outputs import BibtexSanitizeReport
        from noul.server.mcp import _text_result

        result = _text_result({"entries_total": "many"}, output=BibtexSanitizeReport)
        # A tool with an outputSchema must send conforming structuredContent; is_error is the conforming fallback.
        self.assertTrue(result.is_error)
        self.assertIsNone(result.structured_content)
        ok = _text_result({"entries_total": 2}, output=BibtexSanitizeReport)
        self.assertEqual(ok.structured_content, {"entries_total": 2})

    def test_dossier_resources(self):
        from noul import api
        from noul.server.mcp import build_server

        api.save_research_dossier("Creatine and memory", "# Synthesis\n\nSome text.", [], None)

        async def resources():
            async with self.client(build_server()) as c:
                listed = await c.list_resources()
                uri = str(listed.resources[0].uri)
                return uri, await c.read_resource(uri)

        uri, read = run(resources)
        self.assertTrue(uri.startswith("noul://dossier/"))
        self.assertIn("Some text.", read.contents[0].text)

    def test_search_reports_progress_per_source(self):
        from noul.server.mcp import build_server

        progress = []

        async def search():
            async def on_progress(value, total, message):
                progress.append((value, total, message))

            async with self.client(build_server()) as c:
                return await c.call_tool(
                    "search_papers",
                    {"query": "progress probe unique 7c1", "sources": ["openalex", "crossref"]},
                    progress_callback=on_progress,
                )

        with (
            mock.patch(_support.core_target("_search_openalex"), return_value=[]),
            mock.patch(_support.core_target("_search_crossref"), return_value=[]),
        ):
            result = run(search)
        self.assertFalse(result.is_error, result.content[0].text)
        self.assertEqual(sorted(p[0] for p in progress), [1.0, 2.0])
        self.assertTrue(all(p[1] == 2.0 for p in progress))
        self.assertEqual({p[2] for p in progress}, {"openalex done", "crossref done"})


class ElicitationTests(unittest.TestCase):
    """Contact e-mail elicitation: stdio only, once per server, saved only when accepted."""

    def setUp(self):
        from noul import config

        config.set_config(clear=True)
        self.addCleanup(config.set_config, clear=True)

    def call_twice(self, callback):
        from noul.server.mcp import build_server

        asked = []

        async def on_elicit(ctx, params):
            asked.append(params.message)
            return callback()

        async def go():
            server = build_server(elicit_email=True)
            kwargs = {"elicitation_callback": on_elicit} if callback else {}
            async with Client(InMemoryTransport(server), **kwargs) as c:
                await c.call_tool("get_fulltext", {"id": "doi:10.1000/none"})
                await c.call_tool("get_fulltext", {"id": "doi:10.1000/none"})

        anyio.run(go)
        return asked

    def test_accepted_email_is_saved_and_never_asked_again(self):
        from noul import config

        asked = self.call_twice(lambda: types.ElicitResult(action="accept", content={"email": "me@university.edu"}))
        self.assertEqual(len(asked), 1)
        self.assertEqual(config.get_config()["email"], "me@university.edu")

    def test_declined_email_is_not_saved_and_never_asked_again(self):
        from noul import config

        asked = self.call_twice(lambda: types.ElicitResult(action="decline"))
        self.assertEqual(len(asked), 1)
        self.assertIsNone(config.get_config()["email"])
        self.assertTrue(config.get_config()["email_prompt_declined"])
        self.assertEqual(self.call_twice(lambda: types.ElicitResult(action="accept", content={"email": "x@y.org"})), [])

    def test_offline_tools_never_ask(self):
        from noul.server.mcp import build_server

        asked = []

        async def on_elicit(ctx, params):
            asked.append(1)
            return types.ElicitResult(action="decline")

        async def go():
            async with Client(InMemoryTransport(build_server(elicit_email=True)), elicitation_callback=on_elicit) as c:
                await c.call_tool("suggest_boolean_queries", {"query": "LLM quantization"})
                await c.call_tool("evidence_meter", {"items": []})

        anyio.run(go)
        self.assertEqual(asked, [])

    def test_parallel_calls_open_a_single_prompt_on_both_protocol_generations(self):
        from noul.server.mcp import build_server

        for mode in ("legacy", "auto"):
            with self.subTest(mode=mode):
                from noul import config

                config.set_config(clear=True)
                asked = []

                async def slow(ctx, params, asked=asked):
                    asked.append(1)
                    await anyio.sleep(0.2)
                    return types.ElicitResult(action="accept", content={"email": "me@lab.org"})

                async def go(mode=mode):
                    server = build_server(elicit_email=True)
                    async with Client(InMemoryTransport(server), elicitation_callback=slow, mode=mode) as c:
                        async with anyio.create_task_group() as tg:
                            for _ in range(3):
                                tg.start_soon(c.call_tool, "get_fulltext", {"id": "doi:10.1000/none"})

                anyio.run(go)
                self.assertEqual(len(asked), 1)
                self.assertEqual(config.get_config()["email"], "me@lab.org")

    def test_existing_email_is_never_overwritten_by_elicitation(self):
        from noul import config

        config.set_config(email="owner@lab.org")
        asked = self.call_twice(lambda: types.ElicitResult(action="accept", content={"email": "other@x.org"}))
        self.assertEqual(asked, [])
        self.assertEqual(config.get_config()["email"], "owner@lab.org")

    def test_clients_without_elicitation_are_never_asked(self):
        self.assertEqual(self.call_twice(None), [])

    def test_legacy_protocol_uses_the_back_channel_over_stdio(self):
        """A 2025-06-18 client over real stdio receives elicitation/create and its answer is saved."""
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                    "clientInfo": {"name": "raw", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_fulltext", "arguments": {"id": "doi:10.1000/none"}},
            },
        ]
        proc = subprocess.Popen(
            [sys.executable, "-m", "noul", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=dict(os.environ),
        )
        try:
            for m in messages:
                proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            seen = []
            while True:
                msg = json.loads(proc.stdout.readline())
                seen.append(msg.get("method") or msg.get("id"))
                if msg.get("method") == "elicitation/create":
                    reply = {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "result": {"action": "accept", "content": {"email": "a@b.org"}},
                    }
                    proc.stdin.write(json.dumps(reply) + "\n")
                    proc.stdin.flush()
                if msg.get("id") == 2:
                    break
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
        self.assertIn("elicitation/create", seen)
        from noul import config

        self.assertEqual(config.get_config()["email"], "a@b.org")


if __name__ == "__main__":
    unittest.main()
