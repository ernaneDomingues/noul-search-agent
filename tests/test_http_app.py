"""Phase 2a: the Starlette HTTP app keeps the v1 protections on every route (Host, Origin, Bearer, body cap, writes)."""

import json
import os
import unittest
from unittest import mock

from starlette.testclient import TestClient

import _support

TOKEN = "s3cret-token"


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


def client(**kwargs):
    from noul.server.http import build_app

    defaults = {"profile": "research", "token": TOKEN, "cors_origins": ("https://chatgpt.com",)}
    return TestClient(build_app(**{**defaults, **kwargs}), base_url="http://testserver")


AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def mcp_payloads(response):
    """JSON-RPC messages from a JSON or SSE Streamable HTTP response."""
    if response.headers.get("content-type", "").startswith("application/json"):
        return [response.json()]
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


class HttpAppTests(unittest.TestCase):
    def setUp(self):
        for name in ("_http_json", "_http_get"):
            patcher = mock.patch(_support.core_target(name), side_effect=_support.no_network)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_healthz_is_open_and_everything_else_needs_the_token(self):
        with client() as c:
            self.assertEqual(c.get("/healthz").json()["status"], "ok")
            for method, path in (("get", "/openapi.json"), ("get", "/tools"), ("post", "/tools/evidence_meter")):
                with self.subTest(path=path):
                    r = getattr(c, method)(path)
                    self.assertEqual(r.status_code, 401)
                    self.assertEqual(r.headers["www-authenticate"], "Bearer")
            self.assertEqual(c.post("/mcp", json=INIT, headers={"Accept": MCP_HEADERS["Accept"]}).status_code, 401)
            wrong = {"Authorization": "Bearer nope"}
            self.assertEqual(c.get("/tools", headers=wrong).status_code, 401)

    def test_host_header_is_validated_when_bound_to_loopback(self):
        from noul.server.http import loopback_hosts

        with client(allowed_hosts=loopback_hosts(8765)) as c:
            self.assertEqual(c.get("/healthz", headers={"Host": "evil.example:8765"}).status_code, 403)
            self.assertEqual(c.get("/healthz", headers={"Host": "127.0.0.1:8765"}).status_code, 200)

    def test_cors_allows_only_configured_origins(self):
        with client() as c:
            bad = c.get("/healthz", headers={"Origin": "https://evil.example"})
            self.assertEqual(bad.status_code, 403)
            good = c.get("/healthz", headers={"Origin": "https://chatgpt.com"})
            self.assertEqual(good.headers["access-control-allow-origin"], "https://chatgpt.com")
            pre = c.options("/mcp", headers={"Origin": "https://chatgpt.com"})
            self.assertEqual(pre.status_code, 204)
            self.assertIn("Mcp-Session-Id", pre.headers["access-control-allow-headers"])
            self.assertEqual(pre.content, b"")
            self.assertNotEqual(pre.headers.get("content-length"), "2")

    def test_body_size_is_capped(self):
        from noul.server.common import MAX_BODY_BYTES

        with client() as c:
            big = "x" * (MAX_BODY_BYTES + 1)
            r = c.post("/tools/evidence_meter", content=big, headers={**AUTH, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 413)

    def test_chunked_bodies_over_the_cap_get_413(self):
        from noul.server.common import MAX_BODY_BYTES

        def chunks():
            for _ in range(MAX_BODY_BYTES // 65536 + 2):
                yield b"x" * 65536

        with client() as c:
            for path in ("/tools/evidence_meter", "/mcp"):
                with self.subTest(path=path):
                    r = c.post(path, content=chunks(), headers={**MCP_HEADERS})
                    self.assertEqual(r.status_code, 413, r.text[:200])

    def test_websocket_scopes_are_refused(self):
        from starlette.websockets import WebSocketDisconnect

        with client() as c, self.assertRaises(WebSocketDisconnect):
            with c.websocket_connect("/mcp", headers=AUTH):
                pass

    def test_clients_cannot_inject_an_unrequested_email_answer(self):
        from noul import config

        config.set_config(clear=True)
        self.addCleanup(config.set_config, clear=True)
        forged = {"noul_contact_email": {"action": "accept", "content": {"email": "attacker@evil.example"}}}
        with client(profile="all") as c:
            init = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
            headers = {**MCP_HEADERS, "MCP-Protocol-Version": "2025-06-18"}
            if init.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "evidence_meter", "arguments": {"items": []}, "inputResponses": forged},
            }
            c.post("/mcp", json=call, headers=headers)
        self.assertIsNone(config.get_config()["email"])

    def test_rest_tools_and_openapi_follow_the_profile(self):
        with client() as c:
            names = {t["name"] for t in c.get("/tools", headers=AUTH).json()["tools"]}
            self.assertEqual(len(names), 14)  # research profile (15) minus configure_noul (a write tool)
            spec = c.get("/openapi.json", headers=AUTH).json()
            self.assertEqual({p.rsplit("/", 1)[1] for p in spec["paths"]}, names)
            ok = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]}, headers=AUTH)
            self.assertEqual(ok.json()["total_papers"], 1)
            self.assertEqual(c.post("/tools/nope", json={}, headers=AUTH).status_code, 404)
            self.assertEqual(c.post("/tools/search_papers", json={"query": 3}, headers=AUTH).status_code, 400)
        with client(profile="all") as c:
            self.assertEqual(len(c.get("/tools", headers=AUTH).json()["tools"]), 37)  # 41 minus 4 write tools

    def test_writes_need_token_and_explicit_opt_in(self):
        args = {"bibtex_input": "@misc{a, title={T}}", "output_path": "out.bib"}
        with client(profile="all") as c:
            r = c.post("/tools/sanitize_bibtex", json=args, headers=AUTH)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(c.post("/tools/configure_noul", json={}, headers=AUTH).status_code, 403)
        with client(profile="all", token=None, allow_write=True) as c:  # opt-in without token is ignored
            self.assertEqual(c.post("/tools/sanitize_bibtex", json=args).status_code, 403)
        self.assertFalse(os.path.exists(os.path.join(ENV["NOUL_WORKSPACE"], "out.bib")))
        with client(profile="all", allow_write=True) as c:
            r = c.post("/tools/sanitize_bibtex", json=args, headers=AUTH)
            self.assertEqual(r.status_code, 200, r.text)
            names = {t["name"] for t in c.get("/tools", headers=AUTH).json()["tools"]}
            self.assertIn("download_papers", names)

    def test_streamable_http_mcp_endpoint(self):
        with client() as c:
            init = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
            self.assertEqual(init.status_code, 200, init.text)
            result = mcp_payloads(init)[0]["result"]
            self.assertEqual(result["serverInfo"]["name"], "noul")
            headers = dict(MCP_HEADERS)
            if init.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
            headers["MCP-Protocol-Version"] = result["protocolVersion"]
            c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
            listed = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
            tools = mcp_payloads(listed)[0]["result"]["tools"]
            self.assertEqual(len(tools), 14)
            self.assertNotIn("configure_noul", {t["name"] for t in tools})


if __name__ == "__main__":
    unittest.main()
