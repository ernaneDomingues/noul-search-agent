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

        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("NOUL_RATE_LIMIT", "NOUL_MAX_CONCURRENCY", "NOUL_TRUSTED_PROXY"):
                os.environ.pop(key, None)
            self.assertIsNotNone(limits.env_rate_limiter(public=True))
            self.assertIsNone(limits.env_rate_limiter(public=False))
            self.assertEqual(limits.env_max_concurrency(public=True), 8)
            self.assertIsNone(limits.env_max_concurrency(public=False))
            self.assertEqual(limits.env_trusted_proxy_hops(), 0)
            os.environ["NOUL_RATE_LIMIT"] = "0"
            self.assertIsNone(limits.env_rate_limiter(public=True))


class PublicAppTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_token_needed_and_writes_are_impossible(self):
        with public_client(token="ignored-in-public", allow_write=True) as c:
            names = {t["name"] for t in c.get("/tools").json()["tools"]}
            self.assertEqual(len(names), 14)
            self.assertFalse(names & {"download_papers", "save_research_dossier", "configure_noul"})
            args = {"bibtex_input": "@misc{a,title={T}}", "output_path": "x.bib"}
            self.assertEqual(c.post("/tools/sanitize_bibtex", json=args).status_code, 404)  # not in research
            r = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]})
            self.assertEqual(r.json()["total_papers"], 1)

    def test_mcp_endpoint_instructions_and_no_server_dossiers(self):
        from noul import api

        api.save_research_dossier("Private topic", "# Secret synthesis", [], None)
        with public_client() as c:
            init = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
            result = mcp_payloads(init)[0]["result"]
            self.assertIn("public, read-only Noul connector", result["instructions"])
            headers = {**MCP_HEADERS, "MCP-Protocol-Version": result["protocolVersion"]}
            if init.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
            c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
            listed = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, headers=headers)
            self.assertEqual(mcp_payloads(listed)[0]["result"]["resources"], [])

    def test_rate_limit_answers_429_with_retry_after(self):
        from noul.server.limits import RateLimiter

        limiter = RateLimiter(rate_per_minute=60, burst=2, clock=FakeClock())
        with public_client(rate_limiter=limiter) as c:
            self.assertEqual([c.get("/tools").status_code for _ in range(2)], [200, 200])
            blocked = c.get("/tools")
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers["retry-after"], "1")
            self.assertEqual(c.get("/healthz").status_code, 200)  # health checks are never limited

    def test_request_sizes_are_capped(self):
        seen = {}

        def fake_search(query, **kwargs):
            seen.update(kwargs)
            return {"papers": [], "total_found": 0, "sources": [], "warnings": []}

        from noul import registry

        tool = registry.REGISTRY["search_papers"]
        replacement = dataclasses.replace(tool, fn=fake_search)
        with mock.patch.dict(registry.REGISTRY, {"search_papers": replacement}), public_client() as c:
            c.post("/tools/search_papers", json={"query": "q", "limit": 500})
        self.assertEqual(seen["limit"], 25)

    def test_caps_helper(self):
        from noul.server.common import apply_public_caps

        capped = apply_public_caps("query_fulltexts", {"ids": list("abcdefgh"), "question": "q"})
        self.assertEqual(capped["ids"], list("abcde"))
        self.assertEqual(apply_public_caps("search_papers", {"query": "q", "limit": "40"})["limit"], 25)
        self.assertEqual(apply_public_caps("get_paper", {"id": "x"}), {"id": "x"})


class ConcurrencyTests(unittest.TestCase):
    def test_extra_parallel_calls_get_a_busy_error(self):
        from noul.server.mcp import build_server

        def slow_call(name, args):
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
