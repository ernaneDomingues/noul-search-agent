"""Outbound hardening: connect-time address checks (DNS rebinding), bounded decoding, a total deadline per
download, API keys only over https, and arXiv ids that cannot walk the Semantic Scholar API."""

import gzip
import socket
import unittest
from unittest import mock

import httpcore
import httpx

import _support


def setUpModule():
    _support.isolate_environment()


def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class ConnectTimeCheckTests(unittest.TestCase):
    """The address checked is the address connected to, whatever DNS answers the second time."""

    def test_the_backend_refuses_a_name_that_now_resolves_to_a_private_address(self):
        from noul.errors import HTTPError
        from noul.net import http

        backend = http._PublicOnlyBackend()
        token = http._PUBLIC_ONLY.set(True)
        self.addCleanup(http._PUBLIC_ONLY.reset, token)
        with (
            mock.patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")),
            mock.patch.object(httpcore.SyncBackend, "connect_tcp") as connect,
            self.assertRaises(HTTPError),
        ):
            backend.connect_tcp("rebind.example", 443)
        connect.assert_not_called()

    def test_the_backend_connects_to_the_address_it_checked(self):
        from noul.net import http

        backend = http._PublicOnlyBackend()
        token = http._PUBLIC_ONLY.set(True)
        self.addCleanup(http._PUBLIC_ONLY.reset, token)
        with (
            mock.patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")),
            mock.patch.object(httpcore.SyncBackend, "connect_tcp", return_value="stream") as connect,
        ):
            self.assertEqual(backend.connect_tcp("example.org", 443), "stream")
        self.assertEqual(connect.call_args.args[:2], ("93.184.216.34", 443))

    def test_other_requests_are_left_alone(self):
        from noul.net import http

        with mock.patch.object(httpcore.SyncBackend, "connect_tcp", return_value="stream") as connect:
            http._PublicOnlyBackend().connect_tcp("api.openalex.org", 443)  # _PUBLIC_ONLY is False by default
        self.assertEqual(connect.call_args.args[:2], ("api.openalex.org", 443))

    def test_the_shared_client_uses_the_checking_backend(self):
        from noul.net import http

        http._reset_client()
        self.addCleanup(http._reset_client)
        self.assertIsInstance(http._client()._transport._pool._network_backend, http._PublicOnlyBackend)

    def test_public_only_fetches_set_the_flag_and_reset_it(self):
        from noul.net import http

        seen = []

        def handler(request):
            seen.append(http._PUBLIC_ONLY.get())
            return httpx.Response(200, content=b"ok")

        http._reset_client(httpx.MockTransport(handler))
        self.addCleanup(http._reset_client)
        with mock.patch.object(http, "_validate_public_url", side_effect=lambda url: url):
            http._http_get("https://pdf.example/a.pdf", public_only=True)
            http._http_get("https://api.openalex.org/works")
        self.assertEqual(seen, [True, False])
        self.assertFalse(http._PUBLIC_ONLY.get())

    def test_urllib_probes_check_the_address_they_connect_to(self):
        from noul.errors import HTTPError
        from noul.net import http

        with (
            mock.patch("socket.getaddrinfo", return_value=_addrinfo("10.0.0.5")),
            mock.patch("socket.create_connection") as connect,
            self.assertRaises(HTTPError),
        ):
            http._public_create_connection(("rebind.example", 443), 5)
        connect.assert_not_called()
        with (
            mock.patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")),
            mock.patch("socket.create_connection", return_value="sock") as connect,
        ):
            self.assertEqual(http._public_create_connection(("github.com", 443), 5), "sock")
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))


class _Stream(httpx.SyncByteStream):
    def __init__(self, chunks, on_chunk=None):
        self.chunks, self.on_chunk = chunks, on_chunk

    def __iter__(self):
        for chunk in self.chunks:
            if self.on_chunk:
                self.on_chunk()
            yield chunk


class BodyTests(unittest.TestCase):
    def setUp(self):
        from noul.net import http

        self.http = http
        self.clock = [1000.0]
        for p in (
            mock.patch.object(http, "_monotonic", side_effect=lambda: self.clock[0]),
            mock.patch.object(http, "_sleep"),
            mock.patch.object(http, "_validate_public_url", side_effect=lambda url: url),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(http._reset_client)

    def serve(self, response):
        self.http._reset_client(httpx.MockTransport(lambda request: response))

    def test_stacked_compression_is_refused(self):
        from noul.errors import HTTPError

        body = gzip.compress(gzip.compress(b"\0" * 10_000_000))
        self.serve(httpx.Response(200, headers={"Content-Encoding": "gzip, gzip"}, stream=_Stream([body])))
        with self.assertRaises(HTTPError) as caught:
            self.http._http_get("https://pdf.example/a.pdf", public_only=True, binary=True)
        self.assertEqual(caught.exception.code, 502)

    def test_a_compressed_body_is_decoded_up_to_the_cap_only(self):
        from noul.errors import HTTPError

        body = gzip.compress(b"\0" * 50_000_000)  # about 50 KB on the wire
        self.serve(httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=_Stream([body])))
        with self.assertRaises(HTTPError) as caught:
            self.http._http_get("https://pdf.example/a.pdf", binary=True, max_bytes=1_000_000)
        self.assertEqual(caught.exception.code, 413)

    def test_a_small_compressed_body_still_works(self):
        body = gzip.compress(b'{"ok": true}')
        self.serve(httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=_Stream([body])))
        self.assertEqual(self.http._http_json("https://api.crossref.org/works"), {"ok": True})

    def test_a_slow_download_stops_at_the_deadline(self):
        from noul.errors import HTTPError

        def tick():
            self.clock[0] += 20  # each chunk arrives just inside the read timeout

        self.serve(httpx.Response(200, stream=_Stream([b"x"] * 100, on_chunk=tick)))
        with self.assertRaises(HTTPError) as caught:
            self.http._http_get("https://pdf.example/slow.pdf", public_only=True, binary=True)
        self.assertEqual(caught.exception.code, 504)
        self.assertLessEqual(self.clock[0] - 1000.0, self.http.FETCH_DEADLINE + 40)


class ApiKeyTests(unittest.TestCase):
    def test_keys_are_sent_only_over_https_to_the_default_port(self):
        from noul.net import http

        env = {"OPENALEX_API_KEY": "k1", "S2_API_KEY": "k2"}
        with mock.patch.dict("os.environ", env):
            for url in ("http://api.openalex.org/works", "https://api.openalex.org:8443/works"):
                headers = {}
                with self.subTest(url=url):
                    self.assertNotIn("api_key", http._apply_api_keys(url, headers))
            headers = {}
            http._apply_api_keys("http://api.semanticscholar.org:8080/p", headers)
            self.assertNotIn("x-api-key", headers)
            self.assertIn("api_key=k1", http._apply_api_keys("https://api.openalex.org/works", {}))


class ArxivIdTests(unittest.TestCase):
    def test_only_arxiv_shaped_ids_reach_semantic_scholar(self):
        from noul.errors import HTTPError
        from noul.providers import endpoints

        for good in ("2401.01234", "2401.01234v3", "hep-th/9901001", "math.GT/0309136v1"):
            self.assertIn(f"ARXIV:{good}?", endpoints.s2_paper(good, "title", prefix="ARXIV:"))
        for bad in ("../../../graph/v1/author/1/papers", "2401.01234/../x", "2401.01234?x=1"):
            with self.subTest(bad=bad), self.assertRaises(HTTPError):
                endpoints.s2_paper(bad, "title", prefix="ARXIV:")


if __name__ == "__main__":
    unittest.main()
