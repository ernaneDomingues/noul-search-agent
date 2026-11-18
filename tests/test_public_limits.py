"""Public connector limits: a per-client tool budget below the instance budget, malformed calls charged nothing,
request bodies read within a deadline, a cache bounded in bytes, and search warnings without internal detail."""

import os
import unittest
from unittest import mock

import _support
from test_public_connector import MCP_HEADERS, FakeClock, public_client


def setUpModule():
    _support.isolate_environment()


def _tool_call(name, arguments, rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


class ClientBudgetTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def test_one_client_cannot_use_up_the_instance_budget(self):
        from noul.server.limits import RateLimiter

        per_client = RateLimiter(rate_per_minute=60, burst=2, clock=FakeClock())
        instance = RateLimiter(rate_per_minute=600, burst=50, max_keys=1, clock=FakeClock())
        body = {"items": [{"stance": "supportive"}]}
        with public_client(client_tool_limiter=per_client, tool_limiter=instance, trusted_proxy_hops=1) as c:
            first = {"X-Forwarded-For": "203.0.113.1"}
            codes = [c.post("/tools/evidence_meter", json=body, headers=first).status_code for _ in range(3)]
            self.assertEqual(codes, [200, 200, 429])
            mcp = c.post("/mcp", json=_tool_call("evidence_meter", body), headers={**MCP_HEADERS, **first})
            self.assertEqual(mcp.status_code, 429)
            other = {"X-Forwarded-For": "198.51.100.7"}
            self.assertEqual(c.post("/tools/evidence_meter", json=body, headers=other).status_code, 200)
            # Listing tools is not a tool call.
            self.assertEqual(c.get("/tools", headers=first).status_code, 200)

    def test_malformed_calls_do_not_spend_the_instance_budget(self):
        from noul.server.limits import RateLimiter

        instance = RateLimiter(rate_per_minute=60, burst=1, max_keys=1, clock=FakeClock())
        with public_client(tool_limiter=instance) as c:
            for _ in range(5):
                self.assertEqual(c.post("/tools/evidence_meter", json={"items": "x"}).status_code, 400)
            ok = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]})
            self.assertEqual(ok.status_code, 200)


class BodyDeadlineTests(unittest.TestCase):
    def test_a_trickled_body_is_cut_off(self):
        import anyio

        from noul.server import http

        async def run():
            async def receive():
                await anyio.sleep(3600)  # never sends its body

            with mock.patch.object(http, "BODY_TIMEOUT", 0.05):
                return await http.Guard._buffered_body(receive)

        self.assertIs(anyio.run(run), http._TOO_SLOW)


class CacheBudgetTests(unittest.TestCase):
    def test_the_cache_is_bounded_in_bytes(self):
        from noul.net import cache

        with mock.patch.dict(os.environ, {"NOUL_CACHE_MAX_BYTES": "50000"}):
            for i in range(20):
                cache.cache_set(f"ft:budget-{i}", {"text": "x" * 10_000})
            kept = [k for k in (f"ft:budget-{i}" for i in range(20)) if cache.cache_get(k) is not None]
        self.assertLessEqual(len(kept), 5)
        self.assertIn("ft:budget-19", kept)  # the newest entry always stays


class SearchWarningTests(unittest.TestCase):
    def test_unexpected_source_errors_show_their_type_only(self):
        from noul.search import engine

        boom = OSError("[Errno 28] No space left on device: '/srv/noul/cache.db'")
        with (
            mock.patch("noul.providers.pubmed._search_pubmed", side_effect=boom),
            mock.patch("noul.providers.openalex._search_openalex", return_value=[]),
        ):
            out = engine.search("unique query for warnings", sources=["pubmed", "openalex"])
        self.assertIn("pubmed: OSError", out["warnings"])
        self.assertNotIn("/srv/noul", " ".join(out["warnings"]))


if __name__ == "__main__":
    unittest.main()
