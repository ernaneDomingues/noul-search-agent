"""Outbound HTTP: User-Agent/mailto, SSRF guard (public addresses only), size-capped GETs with retries,
Retry-After, per-host rate limits and optional API keys (OPENALEX_API_KEY, S2_API_KEY, NCBI_API_KEY)."""

import email.utils
import http.cookiejar
import ipaddress
import json
import logging
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import httpx

from noul import __version__
from noul import config as _config
from noul import errors as _errors
from noul import log as _log

UA = "noul-search-agent/" + __version__ + " (+https://github.com/ernanedomingues/noul-search-agent)"


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _max_download_bytes():
    """Default response cap: NOUL_MAX_DOWNLOAD_MB (the public connector uses 15) or MAX_DOWNLOAD_BYTES."""
    try:
        mb = float(os.environ.get("NOUL_MAX_DOWNLOAD_MB") or 0)
    except ValueError:
        mb = 0
    return int(mb * 1024 * 1024) if mb > 0 else MAX_DOWNLOAD_BYTES


def _user_agent():
    email = _config._email()
    return UA[:-1] + f"; mailto:{email})" if email else UA


def _with_mailto(params):
    email = _config._email()
    if email:
        params["mailto"] = email
    return params


def _mailto_qs():
    email = _config._email()
    return "?" + urllib.parse.urlencode({"mailto": email}) if email else ""


def _ip_is_public(ip):
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _validate_public_url(url):
    """Reject non-http(s) URLs and hosts resolving to private/loopback/link-local/reserved IPs (SSRF guard)."""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise _errors.HTTPError(400, f"Blocked URL scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise _errors.HTTPError(400, "Blocked URL without host")
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as e:
            raise _errors.HTTPError(400, f"Cannot resolve host {host}: {e}") from e
        addrs = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
    if not addrs:
        raise _errors.HTTPError(400, f"Cannot resolve host {host}")
    for ip in addrs:
        if not _ip_is_public(ip):
            raise _errors.HTTPError(400, f"Blocked non-public address {ip} for host {host}")
    return url


# urllib opener kept for artifact_radar's HEAD/Range liveness probes; it re-validates every redirect target.
class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_OPENER = urllib.request.build_opener(_PublicRedirectHandler)


# ---------------------------------------------------------------------------------------------------------------
# Resilient client (httpx): retries with backoff + Retry-After, per-host rate limits, optional API keys.

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_CAP = 30.0  # seconds; a longer Retry-After is capped rather than honoured
PUBLIC_RETRY_AFTER_CAP = 5.0  # third-party (public_only) hosts cannot make us sleep long
# Total seconds one _http_get may sleep between retries: kept below the 18 s multi-source search deadline, so
# callers get the real cause ("HTTP Error 429: Too Many Requests") instead of a generic source timeout.
RETRY_BUDGET = 8.0
# Only transient transport failures are retried (not UnsupportedProtocol, LocalProtocolError, ...).
_TRANSIENT = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)
_RATE_LIMIT_HINTS = {
    "api.semanticscholar.org": " (rate limited; set S2_API_KEY for a dedicated quota)",
    "api.openalex.org": " (rate limited; set OPENALEX_API_KEY and a contact e-mail)",
    "eutils.ncbi.nlm.nih.gov": " (rate limited; set NCBI_API_KEY)",
}
_SENSITIVE_HEADERS = ("authorization", "cookie", "x-api-key")

# httpx/httpcore log full request URLs (with mailto e-mails and api_key params) at INFO/DEBUG; keep them quiet
# even when an embedding application turns root logging up. Noul's own logs show host + path only.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
BACKOFF_BASE = 0.5  # seconds: 0.5, 1, 2 ... plus jitter

# Minimum seconds between requests per host (polite defaults for anonymous use).
_HOST_INTERVALS = {
    "api.semanticscholar.org": 1.0,
    "eutils.ncbi.nlm.nih.gov": 1 / 3,
    "export.arxiv.org": 3.0,
}

_sleep = time.sleep  # patched in tests
_monotonic = time.monotonic
_client_lock = threading.Lock()
_client_instance: httpx.Client | None = None
_transport: httpx.BaseTransport | None = None  # tests inject an httpx.MockTransport


def _client() -> httpx.Client:
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            # No cookies: third-party hosts must not be able to track tool calls across a session.
            no_cookies = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
            _client_instance = httpx.Client(
                follow_redirects=False, transport=_transport, http2=False, cookies=no_cookies
            )
        return _client_instance


def _reset_client(transport: httpx.BaseTransport | None = None) -> None:
    """Drop the shared client (tests pass an httpx.MockTransport)."""
    global _client_instance, _transport
    with _client_lock:
        if _client_instance is not None:
            _client_instance.close()
        _client_instance, _transport = None, transport
    _limiter.reset()


class _RateLimiter:
    """Spaces requests to the same host by a minimum interval (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._next.clear()

    def wait(self, host: str) -> None:
        interval = _host_interval(host)
        if not interval:
            return
        with self._lock:
            now = _monotonic()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + interval
        if start > now:
            _sleep(start - now)


_limiter = _RateLimiter()


def _host_interval(host: str) -> float:
    if host == "eutils.ncbi.nlm.nih.gov" and os.environ.get("NCBI_API_KEY"):
        return 1 / 10
    if host == "api.semanticscholar.org" and os.environ.get("S2_API_KEY"):
        return 0.0  # keyed S2 has its own per-key quota
    return _HOST_INTERVALS.get(host, 0.0)


def _apply_api_keys(url: str, headers: dict[str, str]) -> str:
    """Attach optional API keys (OPENALEX_API_KEY, S2_API_KEY, NCBI_API_KEY) to requests for their own hosts."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    param = None
    if host == "api.openalex.org" and os.environ.get("OPENALEX_API_KEY"):
        param = ("api_key", os.environ["OPENALEX_API_KEY"])
    elif host == "eutils.ncbi.nlm.nih.gov" and os.environ.get("NCBI_API_KEY"):
        param = ("api_key", os.environ["NCBI_API_KEY"])
    elif host == "api.semanticscholar.org" and os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    if param:
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if k != param[0]] + [param]
        url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    return url


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = when.timestamp() - time.time()
    return max(0.0, min(seconds, RETRY_AFTER_CAP))


def _backoff(attempt: int) -> float:
    return BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, BACKOFF_BASE / 2)


def _where(url: str) -> str:
    """Host + path for logs: never the query string (it carries mailto e-mails and API keys)."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.hostname}{parts.path}"


def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise _errors.HTTPError(413, f"Response too large ({length} bytes > {max_bytes})")
    buf = bytearray()
    for chunk in response.iter_bytes(65536):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise _errors.HTTPError(413, f"Response exceeded {max_bytes} bytes")
    return bytes(buf)


def _fetch_once(url, headers, timeout, max_bytes, public_only):
    """One logical GET following up to MAX_REDIRECTS redirects (each re-validated when `public_only`)."""
    for _ in range(MAX_REDIRECTS + 1):
        if public_only:
            _validate_public_url(url)
        _limiter.wait((urllib.parse.urlsplit(url).hostname or "").lower())
        request_headers = dict(headers)
        request_url = _apply_api_keys(url, request_headers)
        response = _client().send(
            _client().build_request("GET", request_url, headers=request_headers, timeout=timeout), stream=True
        )
        try:
            if response.is_redirect and response.headers.get("Location"):
                new_url = urllib.parse.urljoin(url, response.headers["Location"])
                if urllib.parse.urlsplit(new_url).hostname != urllib.parse.urlsplit(url).hostname:
                    headers = {k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADERS}
                url = new_url
                continue
            if response.status_code >= 400:
                wait = _retry_after(response)
                if wait is not None and public_only:
                    wait = min(wait, PUBLIC_RETRY_AFTER_CAP)
                return response.status_code, response.reason_phrase, wait, None
            return response.status_code, response.reason_phrase, None, _read_capped(response, max_bytes)
        finally:
            response.close()
    raise _errors.HTTPError(502, f"Too many redirects (> {MAX_REDIRECTS})")


def _http_get(url, headers=None, timeout=15, binary=False, max_bytes=None, public_only=False):
    """GET with a byte cap, retries (429/5xx/transient network errors, honouring Retry-After within RETRY_BUDGET)
    and per-host rate limits. `timeout` applies per phase (connect/read/write), like urllib's socket timeout.

    `public_only=True` applies the SSRF guard to the URL and every redirect (use it for URLs that come from
    third-party metadata, e.g. PDF/full-text links). Errors raise `noul.errors.HTTPError` with `.code`."""
    if max_bytes is None:
        max_bytes = _max_download_bytes()
    # identity: never let a server send a compression bomb that expands past the byte cap before we count it.
    hdrs = {"User-Agent": _user_agent(), "Accept": "*/*", "Accept-Encoding": "identity"}
    if headers:
        hdrs.update(headers)
    slept = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status, reason, retry_after, data = _fetch_once(url, hdrs, timeout, max_bytes, public_only)
        except _TRANSIENT as exc:
            wait = _backoff(attempt)
            if attempt == MAX_ATTEMPTS or slept + wait > RETRY_BUDGET:
                code = 504 if isinstance(exc, httpx.TimeoutException) else 502
                raise _errors.HTTPError(code, f"Network error ({type(exc).__name__}) for {_where(url)}") from exc
            _log.logger.debug(
                "http retry %s error=%s attempt=%d wait=%.2f", _where(url), type(exc).__name__, attempt, wait
            )
            _sleep(wait)
            slept += wait
            continue
        except (httpx.HTTPError, ValueError) as exc:
            # Corrupt encodings, invalid redirect URLs, unsupported schemes...: fail closed with a clean error.
            raise _errors.HTTPError(502, f"Invalid response ({type(exc).__name__}) from {_where(url)}") from exc
        if data is not None:
            return data if binary else data.decode("utf-8", errors="replace")
        wait = retry_after if retry_after is not None else _backoff(attempt)
        if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS and slept + wait <= RETRY_BUDGET:
            _log.logger.debug("http retry %s status=%d attempt=%d wait=%.2f", _where(url), status, attempt, wait)
            _sleep(wait)
            slept += wait
            continue
        reason = reason or httpx.codes.get_reason_phrase(status)
        hint = _RATE_LIMIT_HINTS.get((urllib.parse.urlsplit(url).hostname or "").lower(), "") if status == 429 else ""
        raise _errors.HTTPError(status, f"HTTP Error {status}: {reason}{hint}")
    raise AssertionError("unreachable")


def _http_json(url, headers=None, timeout=15):
    txt = _http_get(url, headers=headers, timeout=timeout, binary=False)
    return json.loads(txt)
