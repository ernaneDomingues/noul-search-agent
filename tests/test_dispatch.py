"""One admission path for MCP and REST: `server.common.dispatch` decides, the two transports only render."""

import unittest
from unittest import mock

from starlette.testclient import TestClient

import _support


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class RefusalTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def refusal(self, name, args=None, **policy):
        from noul.server import common

        return common.refusal_for(name, args or {}, common.Policy(**policy))

    def test_each_reason_has_its_label_and_status(self):
        bib = {"bibtex_input": "@misc{a,title={T}}"}
        cases = [
            ("nope", {}, {}, "NotFound", 404),
            ("sanitize_bibtex", bib, {"profile": "research"}, "NotAvailable", 404),
            ("project_list", {}, {"profile": "research", "public": True}, "NotAvailable", 404),
            ("download_papers", {"ids": ["x"]}, {"allow_write": False}, "NotAvailable", 403),
            ("sanitize_bibtex", {**bib, "output_path": "x.bib"}, {"allow_write": False}, "PermissionError", 403),
            ("recommend_citations", {"draft_text": "notes.md"}, {"allow_write": False}, "PermissionError", 403),
        ]
        for name, args, policy, label, status in cases:
            with self.subTest(name=name, policy=policy):
                refusal = self.refusal(name, args, **policy)
                self.assertEqual((refusal.label, refusal.status), (label, status))
                self.assertTrue(refusal.text.startswith(f"{label}: "))

    def test_allowed_calls_are_not_refused(self):
        self.assertIsNone(self.refusal("download_papers", {"ids": ["x"]}))
        self.assertIsNone(self.refusal("sanitize_bibtex", {"bibtex_input": "@misc{a,title={T}}"}, allow_write=False))

    def test_the_public_connector_says_why_it_hides_server_data(self):
        refusal = self.refusal("project_list", {}, profile="research", public=True)
        self.assertIn("public connector does not expose", refusal.text)

    def test_build_server_takes_a_policy_or_the_separate_arguments(self):
        from noul.server import common
        from noul.server.mcp import build_server

        with self.assertRaises(TypeError):
            build_server(profile="research", policy=common.Policy())
        build_server(policy=common.Policy(profile="research"))

    def test_a_public_policy_is_read_only_whatever_was_asked(self):
        from noul.server import common

        self.assertFalse(common.Policy(public=True, allow_write=True).allow_write)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)

    def test_permission_is_checked_before_any_budget_is_spent(self):
        from noul.server import common, limits

        limiter = limits.RateLimiter(rate_per_minute=60, burst=1, max_keys=1, clock=lambda: 0.0)
        policy = common.Policy(allow_write=False, tool_limiter=limiter, concurrency=limits.Concurrency(1))
        self.assertEqual(common.dispatch("download_papers", {"ids": ["x"]}, policy).status, 403)
        self.assertIsInstance(common.dispatch("evidence_meter", {"items": []}, policy), common.Call)

    def test_rate_limit_then_capacity_with_retry_hints(self):
        from noul.server import common, limits

        limiter = limits.RateLimiter(rate_per_minute=60, burst=2, max_keys=1, clock=lambda: 0.0)
        policy = common.Policy(tool_limiter=limiter, concurrency=limits.Concurrency(1))
        held = common.dispatch("evidence_meter", {"items": []}, policy)
        busy = common.dispatch("evidence_meter", {"items": []}, policy)
        limited = common.dispatch("evidence_meter", {"items": []}, policy)
        self.assertIsInstance(held, common.Call)
        self.assertEqual((busy.label, busy.status, busy.retry_after), ("Busy", 503, 5))
        self.assertEqual((limited.label, limited.status, limited.retry_after), ("RateLimited", 429, 1))

    def test_the_slot_is_released_when_the_tool_raises(self):
        from noul.server import common, limits

        policy = common.Policy(concurrency=limits.Concurrency(1))
        call = common.dispatch("evidence_meter", {"items": "not-a-list"}, policy)
        with self.assertRaises(Exception) as ctx:
            call.run()
        self.assertEqual(common.error_status(ctx.exception), 400)
        self.assertTrue(common.error_text(ctx.exception).startswith("InvalidArguments: "))
        self.assertIsInstance(common.dispatch("evidence_meter", {"items": []}, policy), common.Call)

    def test_release_is_idempotent(self):
        from noul.server import common, limits

        concurrency = limits.Concurrency(2)
        policy = common.Policy(concurrency=concurrency)
        first = common.dispatch("evidence_meter", {"items": []}, policy)
        second = common.dispatch("evidence_meter", {"items": []}, policy)
        first.release()
        first.release()
        self.assertEqual(concurrency._active, 1)
        second.release()
        self.assertEqual(concurrency._active, 0)

    def test_a_call_cancelled_before_its_thread_starts_frees_its_slot(self):
        import anyio

        from noul.server import common, limits
        from noul.server.mcp import build_server

        concurrency = limits.Concurrency(1)
        policy = common.Policy(profile="research", allow_write=False, concurrency=concurrency)
        server = build_server(policy=policy)

        async def blocked_thread(*args, **kwargs):
            await anyio.sleep_forever()  # the worker thread never starts

        async def calls():
            async with _support.mcp_client(server) as c:
                with mock.patch("anyio.to_thread.run_sync", blocked_thread):
                    with anyio.move_on_after(0.5):
                        await c.call_tool("evidence_meter", {"items": [{"stance": "supportive"}]})
                    for _ in range(50):  # the server side sees the cancellation shortly after the client
                        if concurrency._active == 0:
                            break
                        await anyio.sleep(0.02)

        anyio.run(calls)
        self.assertEqual(concurrency._active, 0)

    def test_public_calls_carry_the_public_caps(self):
        from noul.server import common

        call = common.dispatch("search_papers", {"query": "q"}, common.Policy(profile="research", public=True))
        self.assertEqual(call.caps, common.public_caps("search_papers"))
        self.assertIsNone(common.dispatch("search_papers", {"query": "q"}, common.Policy()).caps)

    def test_error_status_falls_back_to_500(self):
        from noul.server import common

        self.assertEqual(common.error_status(RuntimeError("boom")), 500)
        self.assertEqual(common.error_text(RuntimeError("boom")), "RuntimeError: boom")


class TransportParityTests(unittest.TestCase):
    """MCP and REST report the same refusal text; REST adds the status and Retry-After."""

    def setUp(self):
        _support.block_http(self)

    def test_refusals_read_the_same_on_both_transports(self):
        from noul.server import common
        from noul.server.http import build_app
        from noul.server.mcp import build_server

        policy = common.Policy(profile="research", allow_write=False)
        calls = [
            ("nope", {}),
            ("generate_manuscript_blueprint", {"topic": "t"}),
            ("download_papers", {"ids": ["doi:10.1/x"]}),
            ("search_papers", {"query": "q", "project": "p"}),
        ]

        async def over_mcp():
            async with _support.mcp_client(build_server(policy=policy)) as c:
                return [(await c.call_tool(name, args)).content[0].text for name, args in calls]

        import anyio

        mcp_texts = anyio.run(over_mcp)
        with TestClient(build_app(profile="research"), base_url="http://testserver") as c:
            for (name, args), text in zip(calls, mcp_texts, strict=True):
                with self.subTest(name=name):
                    expected = common.refusal_for(name, args, policy)
                    answer = c.post(f"/tools/{name}", json=args)
                    self.assertEqual(text, expected.text)
                    self.assertEqual((answer.status_code, answer.json()), (expected.status, {"error": expected.text}))

    def test_rest_errors_use_the_same_labels_as_mcp(self):
        from noul.errors import HTTPError
        from noul.server.http import build_app

        with TestClient(build_app(profile="research"), base_url="http://testserver") as c:
            bad = c.post("/tools/search_papers", json={"query": 3})
            self.assertEqual(bad.status_code, 400)
            self.assertTrue(bad.json()["error"].startswith("InvalidArguments: "))
            with mock.patch("noul.registry.call", side_effect=HTTPError(502, "upstream down")):
                self.assertEqual(c.post("/tools/get_paper", json={"paper_id": "x"}).status_code, 502)

    def test_rest_busy_and_rate_limited_carry_retry_after(self):
        from noul.server import limits
        from noul.server.http import build_app

        limiter = limits.RateLimiter(rate_per_minute=60, burst=1, max_keys=1, clock=lambda: 0.0)
        with TestClient(build_app(profile="research", tool_limiter=limiter), base_url="http://testserver") as c:
            self.assertEqual(
                c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]}).status_code, 200
            )
            limited = c.post("/tools/evidence_meter", json={"items": [{"stance": "supportive"}]})
            self.assertEqual((limited.status_code, limited.headers["retry-after"]), (429, "1"))
            self.assertTrue(limited.json()["error"].startswith("RateLimited: "))


if __name__ == "__main__":
    unittest.main()
