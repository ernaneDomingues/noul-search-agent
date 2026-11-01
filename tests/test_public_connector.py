"""Phase 2b-1: the public read-only connector (no token, writes off, request caps, rate and concurrency limits)."""

import dataclasses
import json
import time
import unittest
from unittest import mock

import anyio
from mcp import Client
from mcp.client._memory import InMemoryTransport
from starlette.testclient import TestClient

import _support

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def setUpModule():
    _support.isolate_environment()


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def public_client(**kwargs):
    from noul.server.http import build_app

    defaults = {"profile": "research", "public": True, "cors_origins": ()}
    return TestClient(build_app(**{**defaults, **kwargs}))


def mcp_payloads(response):
    if response.headers.get("content-type", "").startswith("application/json"):
        return [response.json()]
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


class RateLimiterTests(unittest.TestCase):
    def test_token_bucket_allows_bursts_then_refills(self):
        from noul.server.limits import RateLimiter

        clock = FakeClock()
        limiter = RateLimiter(rate_per_minute=60, burst=3, clock=clock)
        self.assertEqual([limiter.check("a") for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(limiter.check("a"), 1.0)  # one token per second
        self.assertEqual(limiter.check("b"), 0.0)  # other clients are independent
        clock.now += 1.0
        self.assertEqual(limiter.check("a"), 0.0)

    def test_bucket_table_is_bounded(self):
        from noul.server.limits import RateLimiter

        limiter = RateLimiter(60, 1, max_keys=100)
        for i in range(500):
            limiter.check(f"10.0.{i // 256}.{i % 256}")
        self.assertLessEqual(len(limiter._buckets), 100)

    def test_client_ip_trusts_only_the_configured_proxy_hops(self):
        from noul.server.limits import client_ip

        scope = {"client": ("10.1.2.3", 5555)}
        forged = {"x-forwarded-for": "1.1.1.1, 203.0.113.9"}  # client-supplied entry, then the proxy's
        self.assertEqual(client_ip(scope, forged, 0), "10.1.2.3")  # header ignored without a trusted proxy
        self.assertEqual(client_ip(scope, forged, 1), "203.0.113.9")  # rightmost entry = added by the proxy
        self.assertEqual(client_ip(scope, {"x-forwarded-for": "not-an-ip"}, 1), "10.1.2.3")
        self.assertEqual(client_ip(scope, {}, 1), "10.1.2.3")

    def test_environment_defaults(self):
        import os

        from noul.server import limits

        keys = ("NOUL_RATE_LIMIT", "NOUL_TOOL_RATE_LIMIT", "NOUL_MAX_CONCURRENCY", "NOUL_TRUSTED_PROXY")
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in keys:
                os.environ.pop(key, None)
            flood = limits.env_rate_limiter(public=True)
            self.assertEqual((flood.rate * 60, flood.burst), (600.0, 120))
            tools = limits.env_tool_rate_limiter(public=True)
            self.assertEqual((tools.rate * 60, tools.burst), (300.0, 60))
            self.assertIsNone(limits.env_rate_limiter(public=False))
            self.assertIsNone(limits.env_tool_rate_limiter(public=False))
            self.assertEqual(limits.env_max_concurrency(public=True), 8)
            self.assertEqual(limits.env_trusted_proxy_hops(), 0)


class PublicAppTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_token_needed_and_writes_are_impossible(self):
        with public_client(token="ignored-in-public", allow_write=True) as c:
            names = {t["name"] for t in c.get("/tools").json()["tools"]}
            self.assertEqual(len(names), 12)  # research minus write tools minus server-dossier tools
            self.assertFalse(names & {"download_papers", "save_research_dossier", "configure_noul"})
            self.assertFalse(names & {"list_research_dossiers", "get_research_dossier"})
            self.assertEqual(c.post("/tools/list_research_dossiers", json={}).status_code, 404)
            args = {"bibtex_input": "@misc{a,title={T}}", "output_path": "x.bib"}
            self.assertEqual(c.post("/tools/sanitize_bibtex", json=args).status_code, 404)  # not in research
            r = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]})
            self.assertEqual(r.json()["total_papers"], 1)

    def test_server_dossiers_are_never_exposed(self):
        from noul import api

        api.save_research_dossier("Private topic", "# Secret synthesis", [], None)
        with public_client() as c:
            init = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
            result = mcp_payloads(init)[0]["result"]
            self.assertIn("public, read-only Noul connector", result["instructions"])
            self.assertNotIn("resources", result["capabilities"])
            self.assertNotIn("noul-review", result["instructions"])
            headers = {**MCP_HEADERS, "MCP-Protocol-Version": result["protocolVersion"]}
            listed = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
            names = {t["name"] for t in mcp_payloads(listed)[0]["result"]["tools"]}
            self.assertFalse(names & {"list_research_dossiers", "get_research_dossier"})
            call = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_research_dossiers", "arguments": {}},
            }
            out = mcp_payloads(c.post("/mcp", json=call, headers=headers))[0]["result"]
            self.assertTrue(out["isError"])
            self.assertNotIn("Private topic", json.dumps(out))
            self.assertEqual(c.post("/tools/get_research_dossier", json={"slug": "private-topic"}).status_code, 404)

    def test_stateless_transport_any_origin_and_never_401(self):
        with public_client(token="configured-but-ignored") as c:
            init = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Origin": "https://claude.ai"})
            self.assertEqual(init.status_code, 200)
            self.assertTrue(init.headers["content-type"].startswith("application/json"))
            self.assertNotIn("mcp-session-id", init.headers)
            self.assertEqual(init.headers["access-control-allow-origin"], "*")
            pre = c.options("/mcp", headers={"Origin": "http://localhost:6274"})
            self.assertEqual(pre.status_code, 204)
            self.assertEqual(c.get("/mcp").status_code, 405)
            self.assertEqual(c.delete("/mcp").status_code, 405)
            self.assertEqual(c.get("/.well-known/oauth-protected-resource").status_code, 404)

    def test_flood_guard_answers_429_but_not_for_discovery_or_health(self):
        from noul.server.limits import RateLimiter

        limiter = RateLimiter(rate_per_minute=60, burst=2, clock=FakeClock())
        with public_client(rate_limiter=limiter) as c:
            self.assertEqual([c.get("/tools").status_code for _ in range(2)], [200, 200])
            blocked = c.get("/tools")
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers["retry-after"], "1")
            self.assertEqual(c.get("/healthz").status_code, 200)
            self.assertEqual(c.get("/.well-known/oauth-authorization-server").status_code, 404)

    def test_tool_call_limit_is_readable_by_the_model(self):
        from noul.server.limits import RateLimiter

        limiter = RateLimiter(rate_per_minute=60, burst=1, max_keys=1, clock=FakeClock())
        with public_client(tool_limiter=limiter) as c:
            headers = {**MCP_HEADERS, "MCP-Protocol-Version": "2025-06-18"}
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "evidence_meter", "arguments": {"items": [{"stance": "supportive"}]}},
            }
            first = mcp_payloads(c.post("/mcp", json=call, headers=headers))[0]["result"]
            second = mcp_payloads(c.post("/mcp", json=call, headers=headers))[0]["result"]
            self.assertFalse(first.get("isError"))
            self.assertTrue(second["isError"])
            self.assertTrue(second["content"][0]["text"].startswith("RateLimited:"))
            rest = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]})
            self.assertEqual(rest.status_code, 429)

    def test_caps_lower_numbers_with_a_notice_and_refuse_long_lists(self):
        from noul import registry
        from noul.errors import HTTPError
        from noul.server.common import public_caps

        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return {"papers": []}

        tool = registry.REGISTRY["search_papers"]
        with mock.patch.dict(registry.REGISTRY, {"search_papers": dataclasses.replace(tool, fn=fake)}):
            for raw in (1000, "1000", 1000.0, "1_000"):
                with self.subTest(limit=raw):
                    out = registry.call(
                        "search_papers", {"query": "q", "limit": raw}, caps=public_caps("search_papers")
                    )
                    self.assertEqual(seen["limit"], 25)
                    self.assertIn("lowered from 1000 to 25", out["notice"])
        with self.assertRaises(HTTPError) as ctx:
            registry.call(
                "evidence_meter", {"items": [{"stance": "supportive"}] * 101}, caps=public_caps("evidence_meter")
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("split the call", str(ctx.exception))
        self.assertIsNone(public_caps("get_paper"))

    def test_public_schema_advertises_the_caps(self):
        with public_client() as c:
            tools = {t["name"]: t for t in c.get("/tools").json()["tools"]}
        self.assertEqual(tools["search_papers"]["inputSchema"]["properties"]["limit"]["maximum"], 25)
        self.assertEqual(tools["query_fulltexts"]["inputSchema"]["properties"]["ids"]["maxItems"], 5)
        self.assertEqual(tools["get_paper_references"]["inputSchema"]["properties"]["limit"]["maximum"], 25)

    def test_ipv6_clients_share_a_bucket_per_64_and_xff_headers_are_joined(self):
        from noul.server.limits import RateLimiter, client_key

        self.assertEqual(client_key("2001:db8::1"), client_key("2001:db8::ffff"))
        self.assertNotEqual(client_key("2001:db8::1"), client_key("2001:db8:0:1::1"))
        self.assertEqual(client_key("::ffff:203.0.113.5"), "203.0.113.5")
        limiter = RateLimiter(rate_per_minute=60, burst=2, clock=FakeClock())
        with public_client(rate_limiter=limiter, trusted_proxy_hops=1) as c:
            codes = [c.get("/tools", headers={"X-Forwarded-For": f"2001:db8::{i:x}"}).status_code for i in range(4)]
            self.assertEqual(codes, [200, 200, 429, 429])
            # Two X-Forwarded-For headers: the rightmost entry overall is the proxy's, the forged first one ignored.
            forged = [("x-forwarded-for", "9.9.9.9"), ("x-forwarded-for", "2001:db8::7")]
            self.assertEqual(c.get("/tools", headers=forged).status_code, 429)

    def test_cache_is_bounded_when_configured(self):
        import os

        from noul.net import cache

        with mock.patch.dict(os.environ, {"NOUL_CACHE_MAX_ROWS": "5"}):
            for i in range(20):
                cache.cache_set(f"bound:{i}", {"i": i})
            with cache._conn() as conn:
                self.assertLessEqual(conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0], 5)
            self.assertEqual(cache.cache_get("bound:19"), {"i": 19})
            self.assertIsNone(cache.cache_get("bound:0"))


class ConcurrencyTests(unittest.TestCase):
    def test_extra_parallel_calls_get_a_busy_error(self):
        from noul.server.mcp import build_server

        def slow_call(name, args, caps=None):
            time.sleep(0.3)
            return {"ok": True}

        async def go():
            results = []
            server = build_server(profile="research", public=True, max_concurrency=1)
            async with Client(InMemoryTransport(server)) as c:

                async def one():
                    results.append(await c.call_tool("evidence_meter", {"items": []}))

                async with anyio.create_task_group() as tg:
                    tg.start_soon(one)
                    await anyio.sleep(0.05)
                    tg.start_soon(one)
            return results

        with mock.patch("noul.registry.call", side_effect=slow_call):
            results = anyio.run(go)
        texts = sorted(r.content[0].text for r in results)
        self.assertTrue(any(t.startswith("Busy:") for t in texts), texts)
        self.assertTrue(any(not r.is_error for r in results))


if __name__ == "__main__":
    unittest.main()
