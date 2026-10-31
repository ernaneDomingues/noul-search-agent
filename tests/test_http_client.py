"""Phase 1c: resilient HTTP client — retries, Retry-After, rate limits, SSRF on redirects, byte caps, API keys."""

import os
import unittest
from unittest import mock

import httpx

import _support


def setUpModule():
    _support.isolate_environment()


class HttpClientTests(unittest.TestCase):
    def setUp(self):
        from noul.net import http

        self.http = http
        self.sleeps = []
        self.clock = [1000.0]
        patches = [
            mock.patch.object(http, "_sleep", side_effect=self._sleep),
            mock.patch.object(http, "_monotonic", side_effect=lambda: self.clock[0]),
            mock.patch.object(http.random, "uniform", return_value=0.0),
            mock.patch(
                "noul.net.http._validate_public_url",
                side_effect=lambda url: _support.public_url_or_raise(url),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(http._reset_client)

    def _sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.clock[0] += seconds

    def serve(self, *responses):
        """Install a MockTransport that answers with `responses` in order and records the requests."""
        seen, queue = [], list(responses)

        def handler(request):
            seen.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        self.http._reset_client(httpx.MockTransport(handler))
        return seen

    def test_retries_5xx_with_exponential_backoff_then_succeeds(self):
        seen = self.serve(httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True}))
        self.assertEqual(self.http._http_json("https://api.crossref.org/works?query=x"), {"ok": True})
        self.assertEqual(len(seen), 3)
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_honours_retry_after_and_caps_it(self):
        self.serve(
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "600"}),
            httpx.Response(200, text="done"),
        )
        self.assertEqual(self.http._http_get("https://api.crossref.org/works"), "done")
        self.assertEqual(self.sleeps, [2.0, self.http.RETRY_AFTER_CAP])

    def test_gives_up_after_max_attempts_with_status_code(self):
        seen = self.serve(*[httpx.Response(429)] * self.http.MAX_ATTEMPTS)
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://api.crossref.org/works")
        self.assertEqual(ctx.exception.code, 429)
        self.assertIn("HTTP Error 429", str(ctx.exception))
        self.assertEqual(len(seen), self.http.MAX_ATTEMPTS)

    def test_client_errors_are_not_retried(self):
        seen = self.serve(httpx.Response(404))
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://api.crossref.org/works/10.1/x")
        self.assertEqual((ctx.exception.code, len(seen), self.sleeps), (404, 1, []))

    def test_network_errors_are_retried_and_reported_as_502_or_504(self):
        self.serve(httpx.ConnectError("boom"), httpx.Response(200, text="ok"))
        self.assertEqual(self.http._http_get("https://api.crossref.org/works"), "ok")
        self.serve(*[httpx.ReadTimeout("slow")] * self.http.MAX_ATTEMPTS)
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://api.crossref.org/works?mailto=me@example.org")
        self.assertEqual(ctx.exception.code, 504)
        self.assertNotIn("me@example.org", str(ctx.exception))

    def test_rate_limiter_spaces_requests_per_host(self):
        self.serve(*[httpx.Response(200, text="x")] * 4)
        self.http._http_get("https://export.arxiv.org/api/query?a")
        self.http._http_get("https://export.arxiv.org/api/query?b")
        self.http._http_get("https://api.crossref.org/works")  # unlimited host: no wait
        self.assertEqual(self.sleeps, [3.0])
        with mock.patch.dict(os.environ, {"NCBI_API_KEY": "k"}):
            self.assertAlmostEqual(self.http._host_interval("eutils.ncbi.nlm.nih.gov"), 0.1)
        self.assertAlmostEqual(self.http._host_interval("eutils.ncbi.nlm.nih.gov"), 1 / 3)

    def test_api_keys_are_attached_only_to_their_hosts(self):
        seen = self.serve(*[httpx.Response(200, text="x")] * 4)
        env = {"OPENALEX_API_KEY": "oa-key", "S2_API_KEY": "s2-key", "NCBI_API_KEY": "ncbi-key"}
        with mock.patch.dict(os.environ, env):
            self.http._http_get("https://api.openalex.org/works?search=x")
            self.http._http_get("https://api.semanticscholar.org/graph/v1/paper/search?query=x")
            self.http._http_get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed")
            self.http._http_get("https://api.crossref.org/works")
        self.assertEqual(seen[0].url.params["api_key"], "oa-key")
        self.assertEqual(seen[1].headers["x-api-key"], "s2-key")
        self.assertEqual(seen[2].url.params["api_key"], "ncbi-key")
        self.assertNotIn("api_key", seen[3].url.params)
        self.assertNotIn("x-api-key", seen[3].headers)

    def test_redirects_are_followed_and_revalidated_when_public_only(self):
        seen = self.serve(
            httpx.Response(302, headers={"Location": "https://cdn.example.org/paper.pdf"}),
            httpx.Response(200, content=b"%PDF"),
        )
        self.assertEqual(self.http._http_get("https://example.org/p", binary=True, public_only=True), b"%PDF")
        self.assertEqual(str(seen[1].url), "https://cdn.example.org/paper.pdf")
        self.serve(httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}))
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://example.org/p", public_only=True)
        self.assertEqual(ctx.exception.code, 400)

    def test_byte_cap_applies_while_streaming(self):
        self.serve(httpx.Response(200, content=b"x" * 10))
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://example.org/big", max_bytes=6)
        self.assertEqual(ctx.exception.code, 413)

    def test_requests_ask_for_uncompressed_bodies(self):
        seen = self.serve(httpx.Response(200, text="x"))
        self.http._http_get("https://example.org/a")
        self.assertEqual(seen[0].headers["Accept-Encoding"], "identity")

    def test_sensitive_headers_and_keys_do_not_follow_cross_host_redirects(self):
        seen = self.serve(
            httpx.Response(302, headers={"Location": "https://other.example.net/x"}),
            httpx.Response(200, text="ok"),
        )
        with mock.patch.dict(os.environ, {"S2_API_KEY": "s2-key"}):
            self.http._http_get(
                "https://api.semanticscholar.org/graph/v1/paper/x", headers={"Authorization": "Bearer t"}
            )
        self.assertEqual(seen[0].headers["x-api-key"], "s2-key")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer t")
        self.assertNotIn("x-api-key", seen[1].headers)
        self.assertNotIn("Authorization", seen[1].headers)

    def test_cookies_are_never_stored_or_replayed(self):
        seen = self.serve(
            httpx.Response(200, headers={"Set-Cookie": "track=1; Path=/"}, text="a"),
            httpx.Response(200, text="b"),
        )
        self.http._http_get("https://example.org/a")
        self.http._http_get("https://example.org/b")
        self.assertNotIn("cookie", {k.lower() for k in seen[1].headers})

    def test_third_party_hosts_get_a_short_retry_after_cap(self):
        self.serve(httpx.Response(503, headers={"Retry-After": "30"}), httpx.Response(200, text="ok"))
        self.http._http_get("https://pdfs.example.org/p.pdf", public_only=True)
        self.assertEqual(self.sleeps, [self.http.PUBLIC_RETRY_AFTER_CAP])

    def test_malformed_responses_become_clean_502s(self):
        self.serve(httpx.Response(302, headers={"Location": "http://[127.0.0.1/x"}))
        with self.assertRaises(self.http._errors.HTTPError) as ctx:
            self.http._http_get("https://example.org/a?mailto=me@example.org")
        self.assertEqual(ctx.exception.code, 502)
        self.assertNotIn("me@example.org", str(ctx.exception))

    def test_httpx_internal_loggers_stay_quiet(self):
        import logging

        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("httpcore").level, logging.WARNING)

    def test_logs_never_contain_query_strings(self):
        import io
        import logging

        from noul import log

        self.serve(httpx.Response(503), httpx.Response(200, text="ok"))
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        log.logger.addHandler(handler)
        log.logger.setLevel("DEBUG")
        try:
            with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "secret-key"}):
                self.http._http_get("https://api.openalex.org/works?mailto=me@example.org")
        finally:
            log.logger.removeHandler(handler)
            log.logger.setLevel("WARNING")
        text = stream.getvalue()
        self.assertIn("http retry api.openalex.org/works status=503", text)
        self.assertNotIn("me@example.org", text)
        self.assertNotIn("secret-key", text)


if __name__ == "__main__":
    unittest.main()
