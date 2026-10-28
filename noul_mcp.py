#!/usr/bin/env python3
"""Minimal MCP server (stdio JSON-RPC 2.0) plus an optional HTTP server exposing MCP at `/mcp` and an
OpenAPI tool API (ChatGPT Actions, Cloud Run). No dependencies.

HTTP security model: loopback by default, Host-header check on loopback (DNS rebinding), CORS only for
origins in NOUL_CORS_ORIGINS (any other Origin is rejected), optional bearer token (NOUL_HTTP_TOKEN), and
write tools disabled unless NOUL_HTTP_ALLOW_WRITE=1 *and* a token is set."""
import argparse
import hmac
import ipaddress
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import noul_tools as t
from noul_core import __version__ as VERSION

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
PROTOCOL = SUPPORTED_PROTOCOLS[0]
WRITE_TOOLS = frozenset({"download_papers", "save_research_dossier", "export_obsidian_notes", "configure_noul"})
MAX_BODY_BYTES = 1024 * 1024
DEFAULT_PORT = 8765


def tools_list(exclude=()):
    return [
        {
            "name": n,
            "description": d,
            "inputSchema": {"type": "object", "properties": props, "required": req},
        }
        for n, (d, props, req, _) in t.TOOLS.items()
        if n not in exclude
    ]


def resources_list():
    try:
        dossiers = t.list_research_dossiers().get("dossiers") or []
    except Exception as e:
        print(f"noul: could not list research dossiers: {e}", file=sys.stderr)
        dossiers = []
    return [
        {
            "uri": f"noul://dossier/{d['slug']}",
            "name": f"Research Dossier: {d['topic']}",
            "description": f"Saved research synthesis and {d.get('paper_count', 0)} papers on '{d['topic']}'",
            "mimeType": "text/markdown",
        }
        for d in dossiers
    ]


def resources_read(uri):
    if not uri or not uri.startswith("noul://dossier/"):
        raise ValueError(f"Unknown resource URI: {uri}")
    slug = uri.split("noul://dossier/", 1)[1].strip("/")
    dossier = t.get_research_dossier(slug)
    return [
        {
            "uri": uri,
            "mimeType": "text/markdown",
            "text": dossier.get("markdown") or "",
        }
    ]


def openapi_spec(host=f"http://localhost:{DEFAULT_PORT}", exclude=()):
    paths = {}
    for name, (desc, props, req, _) in t.TOOLS.items():
        if name in exclude:
            continue
        paths[f"/tools/{name}"] = {
            "post": {
                "operationId": name,
                "summary": desc[:120],
                "description": desc,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": props, "required": req}
                        }
                    },
                },
                "responses": {"200": {"description": "Tool response"}},
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Noul Search Agent API",
            "version": VERSION,
            "description": "Scientific literature search, Evidence Meter synthesis support, PDF download, and research dossiers.",
        },
        "servers": [{"url": host}],
        "paths": paths,
    }


def _rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg, allow_write=True):
    """Handle one JSON-RPC message. Returns None for notifications. `allow_write=False` hides and blocks
    tools that write to disk or change configuration (used by the HTTP transport)."""
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "Invalid Request: expected a JSON-RPC object")
    m, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification
        return None
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(mid, -32602, "Invalid params: expected an object")
    try:
        if m == "initialize":
            requested = params.get("protocolVersion")
            res = {
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "noul", "version": VERSION},
            }
        elif m == "tools/list":
            res = {"tools": tools_list(exclude=() if allow_write else WRITE_TOOLS)}
        elif m == "tools/call":
            name = params.get("name")
            try:
                if not allow_write and name in WRITE_TOOLS:
                    raise PermissionError(
                        f"Tool '{name}' writes to disk and is disabled over HTTP "
                        "(set NOUL_HTTP_TOKEN and NOUL_HTTP_ALLOW_WRITE=1 to enable it)."
                    )
                out = t.call(name, params.get("arguments"))
                res = {
                    "content": [
                        {
                            "type": "text",
                            "text": out if isinstance(out, str) else json.dumps(out, ensure_ascii=False),
                        }
                    ]
                }
            except Exception as e:
                res = {"isError": True, "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}]}
        elif m == "resources/list":
            res = {"resources": resources_list()}
        elif m == "resources/read":
            try:
                res = {"contents": resources_read(params.get("uri"))}
            except Exception as e:
                return _rpc_error(mid, -32602, str(e))
        elif m == "ping":
            res = {}
        else:
            return _rpc_error(mid, -32601, f"Method not found: {m}")
    except Exception as e:
        return _rpc_error(mid, -32603, f"Internal error: {type(e).__name__}: {e}")
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def _is_loopback_host(host):
    if (host or "").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _env_list(name):
    return tuple(x.strip().rstrip("/") for x in os.environ.get(name, "").split(",") if x.strip())


class NoulHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler, token=None, cors_origins=(), allow_write=False):
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(server_address, handler)
        self.token = token or None
        self.cors_origins = frozenset(cors_origins or ())
        self.allow_write = bool(allow_write and self.token)
        self.loopback = _is_loopback_host(server_address[0])
        port = self.server_address[1]
        self.allowed_hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


class _Handler(BaseHTTPRequestHandler):
    server_version = f"noul-search-agent/{VERSION}"

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") in self.server.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, code, payload=None, extra_headers=None):
        body = b""
        if payload is not None:
            body = (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _guard(self, require_auth=True):
        """Return True when the request may proceed; otherwise send the error response and return False."""
        srv = self.server
        if srv.loopback and (self.headers.get("Host") or "").lower() not in srv.allowed_hosts:
            self._send(403, {"error": "Forbidden: invalid Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") not in srv.cors_origins:
            self._send(403, {"error": "Forbidden: origin not allowed (see NOUL_CORS_ORIGINS)"})
            return False
        if require_auth and srv.token:
            auth = self.headers.get("Authorization") or ""
            scheme, _, supplied = auth.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode("utf-8"), srv.token.encode("utf-8")
            ):
                self._send(401, {"error": "Unauthorized"}, {"WWW-Authenticate": "Bearer"})
                return False
        return True

    def _hidden_tools(self):
        return () if self.server.allow_write else WRITE_TOOLS

    def do_OPTIONS(self):
        if not self._guard(require_auth=False):
            return
        self._send(204, None, {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version",
            "Access-Control-Max-Age": "600",
        })

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._guard(require_auth=path != "/healthz"):
            return
        if path == "/healthz":
            self._send(200, {"status": "ok", "version": VERSION})
        elif path == "/openapi.json":
            proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip() or "http"
            host = self.headers.get("Host") or f"localhost:{self.server.server_address[1]}"
            self._send(200, openapi_spec(f"{proto}://{host}", exclude=self._hidden_tools()))
        elif path == "/tools":
            self._send(200, {"tools": tools_list(exclude=self._hidden_tools())})
        elif path == "/mcp":
            self._send(405, {"error": "Use POST for MCP JSON-RPC messages"}, {"Allow": "POST"})
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._guard():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "Invalid Content-Length"})
        if length < 0:
            return self._send(400, {"error": "Invalid Content-Length"})
        if length > MAX_BODY_BYTES:
            return self._send(413, {"error": f"Request body exceeds {MAX_BODY_BYTES} bytes"})
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            return self._send(400, {"error": f"Invalid JSON: {e}"})

        if path == "/mcp":
            out = handle(body, allow_write=self.server.allow_write)
            if out is None:
                return self._send(202)
            return self._send(200, out)
        if path.startswith("/tools/"):
            tool_name = path.split("/tools/", 1)[1].strip("/")
            if tool_name in WRITE_TOOLS and not self.server.allow_write:
                return self._send(403, {
                    "error": f"Tool '{tool_name}' writes to disk and is disabled over HTTP "
                             "(set NOUL_HTTP_TOKEN and NOUL_HTTP_ALLOW_WRITE=1 to enable it)."
                })
            try:
                res = t.call(tool_name, body)
                return self._send(200, res if isinstance(res, dict) else {"result": res})
            except Exception as e:
                code = getattr(e, "code", None)
                status = code if isinstance(code, int) and 400 <= code < 600 else 500
                return self._send(status, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "Not found"})

    def log_message(self, fmt, *args):
        if os.environ.get("NOUL_HTTP_LOG") == "1":
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(host="127.0.0.1", port=DEFAULT_PORT, token=None, cors_origins=None, allow_write=None):
    """Build (but do not start) the HTTP server. Unset arguments come from NOUL_HTTP_TOKEN,
    NOUL_CORS_ORIGINS and NOUL_HTTP_ALLOW_WRITE. Port 0 picks a free port."""
    token = (os.environ.get("NOUL_HTTP_TOKEN") or None) if token is None else token
    cors = _env_list("NOUL_CORS_ORIGINS") if cors_origins is None else tuple(cors_origins)
    want_write = os.environ.get("NOUL_HTTP_ALLOW_WRITE") == "1" if allow_write is None else bool(allow_write)
    return NoulHTTPServer((host, int(port)), _Handler, token=token, cors_origins=cors, allow_write=want_write)


def serve_http(port=DEFAULT_PORT, host="127.0.0.1"):
    srv = make_server(host, port)
    bound = srv.server_address[1]
    shown = f"[{host}]" if ":" in host else host
    print(f"Noul HTTP server listening on http://{shown}:{bound} (MCP: /mcp, OpenAPI: /openapi.json)", file=sys.stderr)
    if not srv.loopback and not srv.token:
        print("WARNING: bound to a non-loopback address without NOUL_HTTP_TOKEN; anyone who can reach this port "
              "can use the API (write tools stay disabled).", file=sys.stderr)
    if os.environ.get("NOUL_HTTP_ALLOW_WRITE") == "1" and not srv.token:
        print("WARNING: NOUL_HTTP_ALLOW_WRITE=1 ignored because NOUL_HTTP_TOKEN is not set.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def run_stdio():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            out = _rpc_error(None, -32700, f"Parse error: {e}")
        else:
            out = handle(msg)
        if out:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="noul-mcp", description="Noul Search MCP server (stdio by default).")
    ap.add_argument("--http", nargs="?", const="", default=None, metavar="PORT",
                    help="Serve HTTP (MCP at /mcp + OpenAPI) instead of stdio. Port: PORT arg, $PORT, or 8765.")
    ap.add_argument("--host", help="HTTP bind address (default: $NOUL_HTTP_HOST, 0.0.0.0 on Cloud Run, else 127.0.0.1)")
    ap.add_argument("--version", action="version", version=f"noul-search-agent {VERSION}")
    a = ap.parse_args(argv)
    if a.http is not None:
        try:
            port = int(a.http) if a.http else int(os.environ.get("PORT") or DEFAULT_PORT)
        except ValueError:
            ap.error(f"invalid port: {a.http or os.environ.get('PORT')}")
        host = a.host or os.environ.get("NOUL_HTTP_HOST") or ("0.0.0.0" if os.environ.get("K_SERVICE") else "127.0.0.1")
        return serve_http(port, host)
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
