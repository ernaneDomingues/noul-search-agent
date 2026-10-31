"""Outbound HTTP: User-Agent/mailto, SSRF guard (public addresses only) and size-capped GETs."""

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from noul import __version__
from noul import config as _config
from noul import errors as _errors

UA = "noul-search-agent/" + __version__ + " (+https://github.com/ernanedomingues/noul-search-agent)"


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


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


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_OPENER = urllib.request.build_opener(_PublicRedirectHandler)


def _http_get(url, headers=None, timeout=15, binary=False, max_bytes=MAX_DOWNLOAD_BYTES, public_only=False):
    """GET with a byte cap. `public_only=True` applies the SSRF guard to the URL and every redirect
    (use it for URLs that come from third-party metadata, e.g. PDF/full-text links)."""
    hdrs = {"User-Agent": _user_agent(), "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    if public_only:
        _validate_public_url(url)
        opener = _PUBLIC_OPENER.open
    else:
        opener = urllib.request.urlopen
    req = urllib.request.Request(url, headers=hdrs)
    with opener(req, timeout=timeout) as resp:
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise _errors.HTTPError(413, f"Response too large ({length} bytes > {max_bytes})")
        buf = bytearray()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise _errors.HTTPError(413, f"Response exceeded {max_bytes} bytes")
        data = bytes(buf)
        return data if binary else data.decode("utf-8", errors="replace")


def _http_json(url, headers=None, timeout=15):
    txt = _http_get(url, headers=headers, timeout=timeout, binary=False)
    return json.loads(txt)
