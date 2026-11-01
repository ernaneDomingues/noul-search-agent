"""Hardened HTTP server: Streamable HTTP MCP at /mcp plus REST/OpenAPI routes, on Starlette + uvicorn.

Same protections as v1 for every route: Host validation when bound to loopback (DNS rebinding), CORS only for
NOUL_CORS_ORIGINS (any other Origin is rejected), optional Bearer token (NOUL_HTTP_TOKEN; /healthz is open),
a 1 MB request-body cap, and write tools hidden/blocked unless NOUL_HTTP_ALLOW_WRITE=1 *and* a token is set."""

from __future__ import annotations

import hmac
import json
import os
import sys
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from noul import __version__
from noul import registry as _registry
from noul.server import common as _common
from noul.server import limits as _limits
from noul.server import mcp as _mcp

_PREFLIGHT_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
    "Access-Control-Max-Age": "600",
}


def _json(status: int, payload: Any, headers: dict[str, str] | None = None) -> list[Any]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    hdrs = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return [
        {"type": "http.response.start", "status": status, "headers": hdrs},
        {"type": "http.response.body", "body": body},
    ]


class Guard:
    """ASGI middleware applying Host / Origin / Bearer / body-size checks to every HTTP route."""

    def __init__(
        self,
        app: Any,
        token: str | None,
        cors_origins: tuple[str, ...],
        allowed_hosts: frozenset[str] | None,
        rate_limiter: _limits.RateLimiter | None = None,
        trusted_proxy_hops: int = 0,
        public: bool = False,
    ):
        self.app = app
        self.token = token or None
        self.cors_origins = frozenset(o.rstrip("/") for o in cors_origins)
        self.allowed_hosts = allowed_hosts  # None: not bound to loopback (e.g. Cloud Run behind a proxy)
        self.rate_limiter = rate_limiter
        self.trusted_proxy_hops = trusted_proxy_hops
        self.public = public

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # no websocket routes: refuse rather than pass them through unchecked
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        # Repeated X-Forwarded-For headers are one list in order (a dict would keep only the last one).
        xff = [v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"x-forwarded-for"]
        if xff:
            headers["x-forwarded-for"] = ",".join(xff)
        path = scope.get("path", "")
        origin = (headers.get("origin") or "").rstrip("/")
        if self.public and origin:
            # Open, credential-less server: any origin may call it (browser inspectors, web clients).
            cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "Mcp-Session-Id"}
        elif origin in self.cors_origins:
            cors = {
                "Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
                "Access-Control-Expose-Headers": "Mcp-Session-Id",
            }
        else:
            cors = {}

        async def reply(status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
            for message in _json(status, payload, {**cors, **(extra or {})}):
                await send(message)

        if self.allowed_hosts is not None and headers.get("host", "").lower() not in self.allowed_hosts:
            return await reply(403, {"error": "Forbidden: invalid Host header"})
        if origin and not self.public and origin not in self.cors_origins:
            return await reply(403, {"error": "Forbidden: origin not allowed (see NOUL_CORS_ORIGINS)"})
        if scope.get("method") == "OPTIONS":
            hdrs = [(k.lower().encode(), v.encode()) for k, v in {**cors, **_PREFLIGHT_HEADERS}.items()]
            await send({"type": "http.response.start", "status": 204, "headers": hdrs})
            await send({"type": "http.response.body", "body": b""})
            return
        if self.public and path == "/mcp" and scope.get("method") in ("GET", "DELETE"):
            # Stateless JSON transport: no server-to-client SSE stream and no sessions to delete.
            return await reply(405, {"error": "Method not allowed: POST JSON-RPC messages to /mcp"}, {"Allow": "POST"})
        if self.rate_limiter is not None and path != "/healthz" and not path.startswith("/.well-known/"):
            # Per-client flood guard only: hosted clients (Claude.ai, ChatGPT) share their vendor's egress IPs, so
            # the real cost control is the per-instance tool-call limit and concurrency cap.
            wait = self.rate_limiter.check(
                _limits.client_key(_limits.client_ip(scope, headers, self.trusted_proxy_hops))
            )
            if wait > 0:
                retry = str(max(1, int(wait + 0.999)))
                return await reply(429, {"error": "Too many requests; slow down."}, {"Retry-After": retry})
        if self.token and path != "/healthz":
            scheme, _, supplied = headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode("utf-8"), self.token.encode("utf-8")
            ):
                return await reply(401, {"error": "Unauthorized"}, {"WWW-Authenticate": "Bearer"})
        length = headers.get("content-length")
        if length and (not length.isdigit() or int(length) > _common.MAX_BODY_BYTES):
            return await reply(413, {"error": f"Request body exceeds {_common.MAX_BODY_BYTES} bytes"})

        method = scope.get("method", "GET")
        if not length and method in ("POST", "PUT", "PATCH", "DELETE"):
            # No Content-Length (chunked): read up to the cap before the app runs, then replay it.
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    return  # client went away
                chunks.append(message.get("body", b""))
                size += len(chunks[-1])
                if size > _common.MAX_BODY_BYTES:
                    return await reply(413, {"error": f"Request body exceeds {_common.MAX_BODY_BYTES} bytes"})
                if not message.get("more_body"):
                    break
            buffered: list[Any] = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

            async def replay() -> Any:
                return buffered.pop() if buffered else await receive()

            app_receive: Any = replay
        else:
            app_receive = receive

        async def send_with_cors(message: Any) -> None:
            if message["type"] == "http.response.start" and cors:
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    (k.lower().encode(), v.encode()) for k, v in cors.items()
                ]
            await send(message)

        await self.app(scope, app_receive, send_with_cors)


def build_app(
    profile: str = "research",
    token: str | None = None,
    cors_origins: tuple[str, ...] = (),
    allow_write: bool = False,
    allowed_hosts: frozenset[str] | None = None,
    public: bool = False,
    rate_limiter: _limits.RateLimiter | None = None,
    trusted_proxy_hops: int = 0,
    max_concurrency: int | None = None,
    tool_limiter: _limits.RateLimiter | None = None,
) -> Any:
    """Starlette app (wrapped in Guard). Writes are only possible with a token *and* allow_write, never in `public`
    mode (the hosted read-only connector: no token, request-size caps, no server dossiers)."""
    allow_write = bool(allow_write and token) and not public
    # One concurrency budget shared by MCP and REST tool calls.
    concurrency = _limits.Concurrency(max_concurrency) if max_concurrency else None
    server = _mcp.build_server(
        profile=profile,
        allow_write=allow_write,
        elicit_email=False,
        public=public,
        max_concurrency=concurrency,
        tool_limiter=tool_limiter,
    )

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    async def openapi(request: Request) -> Response:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
        host = request.headers.get("host") or request.url.netloc
        return JSONResponse(
            _common.openapi_spec(f"{proto}://{host}", profile=profile, allow_write=allow_write, public=public)
        )

    async def list_tools(request: Request) -> Response:
        tools = [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in _common.visible_tools(profile, allow_write, public)
        ]
        return JSONResponse({"tools": tools})

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        raw = await request.body()  # Guard has already capped the body at MAX_BODY_BYTES
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
        if name not in {t["name"] for t in _common.visible_tools(profile, allow_write=True, public=public)}:
            return JSONResponse({"error": f"Unknown tool {name}"}, status_code=404)
        if not allow_write and _registry.call_writes(name, body):
            return JSONResponse(
                {
                    "error": f"Tool '{name}' writes to disk with these arguments and is disabled over HTTP "
                    "(set NOUL_HTTP_TOKEN and NOUL_HTTP_ALLOW_WRITE=1 to enable it)."
                },
                status_code=403,
            )
        caps = _common.public_caps(name) if public else None
        if tool_limiter is not None and (wait := tool_limiter.check("tools")) > 0:
            return JSONResponse(
                {"error": f"RateLimited: tool-call limit reached; retry in {int(wait + 0.999)} s."},
                status_code=429,
                headers={"Retry-After": str(max(1, int(wait + 0.999)))},
            )
        if concurrency is not None and not concurrency.try_acquire():
            return JSONResponse(
                {"error": "Busy: too many requests in progress; retry shortly."},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        try:
            result = await run_in_threadpool(_registry.call, name, body, caps)
        except Exception as e:
            code = getattr(e, "code", None)
            status = code if isinstance(code, int) and 400 <= code < 600 else 500
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=status)
        finally:
            if concurrency is not None:
                concurrency.release()
        payload = result if isinstance(result, dict) else {"result": result}
        return Response(json.dumps(payload, ensure_ascii=False, default=str), media_type="application/json")

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/openapi.json", openapi, methods=["GET"]),
        Route("/tools", list_tools, methods=["GET"]),
        Route("/tools/{name}", call_tool, methods=["POST"]),
    ]
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        # Guard enforces Host/Origin for every route (stricter than the SDK default, which allows any localhost
        # origin), so the SDK's own DNS-rebinding check is not layered on top.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=routes,
        max_request_body_size=_common.MAX_BODY_BYTES,
        # Public connector: stateless JSON responses - no long-lived SSE streams per client (slot exhaustion)
        # and no per-instance session state (Cloud Run has no session affinity across instances).
        stateless_http=public,
        json_response=public,
    )
    # The public connector is open by design: a configured token would only lock its intended users out.
    return Guard(app, None if public else token, cors_origins, allowed_hosts, rate_limiter, trusted_proxy_hops, public)


def loopback_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


def serve(
    port: int = _common.DEFAULT_PORT, host: str = "127.0.0.1", profile: str = "research", public: bool = False
) -> None:
    import uvicorn

    token = os.environ.get("NOUL_HTTP_TOKEN") or None
    want_write = os.environ.get("NOUL_HTTP_ALLOW_WRITE") == "1"
    loopback = _common.is_loopback_host(host)
    if public:  # hosted defaults: bounded cache (instance RAM) and smaller downloads (memory per call)
        os.environ.setdefault("NOUL_CACHE_MAX_ROWS", "2000")
        os.environ.setdefault("NOUL_MAX_DOWNLOAD_MB", "15")
    app = build_app(
        profile=profile,
        token=token,
        cors_origins=_common.env_list("NOUL_CORS_ORIGINS"),
        allow_write=want_write,
        allowed_hosts=loopback_hosts(port) if loopback else None,
        public=public,
        rate_limiter=_limits.env_rate_limiter(public),
        trusted_proxy_hops=_limits.env_trusted_proxy_hops(),
        max_concurrency=_limits.env_max_concurrency(public),
        tool_limiter=_limits.env_tool_rate_limiter(public),
    )
    shown = f"[{host}]" if ":" in host else host
    print(
        f"Noul HTTP server on http://{shown}:{port} (MCP: /mcp, OpenAPI: /openapi.json, profile: {profile})",
        file=sys.stderr,
    )
    if public:
        print(
            "Public read-only connector: no token required, writes disabled, rate limits and request caps on.",
            file=sys.stderr,
        )
        if want_write:
            print("WARNING: NOUL_HTTP_ALLOW_WRITE=1 ignored in public mode.", file=sys.stderr)
    elif not loopback and not token:
        print(
            "WARNING: bound to a non-loopback address without NOUL_HTTP_TOKEN; anyone who can reach this port "
            "can use the server.",
            file=sys.stderr,
        )
    if want_write and not token:
        print("WARNING: NOUL_HTTP_ALLOW_WRITE=1 ignored because NOUL_HTTP_TOKEN is not set.", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("NOUL_HTTP_LOG_LEVEL", "warning"))
