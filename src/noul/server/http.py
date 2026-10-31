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
        self, app: Any, token: str | None, cors_origins: tuple[str, ...], allowed_hosts: frozenset[str] | None
    ):
        self.app = app
        self.token = token or None
        self.cors_origins = frozenset(o.rstrip("/") for o in cors_origins)
        self.allowed_hosts = allowed_hosts  # None: not bound to loopback (e.g. Cloud Run behind a proxy)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        origin = (headers.get("origin") or "").rstrip("/")
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin in self.cors_origins else {}

        async def reply(status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
            for message in _json(status, payload, {**cors, **(extra or {})}):
                await send(message)

        if self.allowed_hosts is not None and headers.get("host", "").lower() not in self.allowed_hosts:
            return await reply(403, {"error": "Forbidden: invalid Host header"})
        if origin and origin not in self.cors_origins:
            return await reply(403, {"error": "Forbidden: origin not allowed (see NOUL_CORS_ORIGINS)"})
        if scope.get("method") == "OPTIONS":
            hdrs = [(k.lower().encode(), v.encode()) for k, v in {**cors, **_PREFLIGHT_HEADERS}.items()]
            await send({"type": "http.response.start", "status": 204, "headers": hdrs})
            await send({"type": "http.response.body", "body": b""})
            return
        if self.token and path != "/healthz":
            scheme, _, supplied = headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode("utf-8"), self.token.encode("utf-8")
            ):
                return await reply(401, {"error": "Unauthorized"}, {"WWW-Authenticate": "Bearer"})
        length = headers.get("content-length")
        if length and (not length.isdigit() or int(length) > _common.MAX_BODY_BYTES):
            return await reply(413, {"error": f"Request body exceeds {_common.MAX_BODY_BYTES} bytes"})

        received = 0

        async def capped_receive() -> Any:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > _common.MAX_BODY_BYTES:
                    raise ValueError("request body too large")
            return message

        async def send_with_cors(message: Any) -> None:
            if message["type"] == "http.response.start" and cors:
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    (k.lower().encode(), v.encode()) for k, v in cors.items()
                ]
            await send(message)

        await self.app(scope, capped_receive, send_with_cors)


def build_app(
    profile: str = "research",
    token: str | None = None,
    cors_origins: tuple[str, ...] = (),
    allow_write: bool = False,
    allowed_hosts: frozenset[str] | None = None,
) -> Any:
    """Starlette app (wrapped in Guard). Writes are only possible with a token *and* allow_write."""
    allow_write = bool(allow_write and token)
    server = _mcp.build_server(profile=profile, allow_write=allow_write, elicit_email=False)

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    async def openapi(request: Request) -> Response:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
        host = request.headers.get("host") or request.url.netloc
        return JSONResponse(_common.openapi_spec(f"{proto}://{host}", profile=profile, allow_write=allow_write))

    async def list_tools(request: Request) -> Response:
        tools = [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in _common.visible_tools(profile, allow_write)
        ]
        return JSONResponse({"tools": tools})

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        try:
            raw = await request.body()
            body = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
        if name not in {t["name"] for t in _common.visible_tools(profile, allow_write=True)}:
            return JSONResponse({"error": f"Unknown tool {name}"}, status_code=404)
        if not allow_write and _registry.call_writes(name, body):
            return JSONResponse(
                {
                    "error": f"Tool '{name}' writes to disk with these arguments and is disabled over HTTP "
                    "(set NOUL_HTTP_TOKEN and NOUL_HTTP_ALLOW_WRITE=1 to enable it)."
                },
                status_code=403,
            )
        try:
            result = await run_in_threadpool(_registry.call, name, body)
        except Exception as e:
            code = getattr(e, "code", None)
            status = code if isinstance(code, int) and 400 <= code < 600 else 500
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=status)
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
    )
    return Guard(app, token, cors_origins, allowed_hosts)


def loopback_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


def serve(port: int = _common.DEFAULT_PORT, host: str = "127.0.0.1", profile: str = "research") -> None:
    import uvicorn

    token = os.environ.get("NOUL_HTTP_TOKEN") or None
    want_write = os.environ.get("NOUL_HTTP_ALLOW_WRITE") == "1"
    loopback = _common.is_loopback_host(host)
    app = build_app(
        profile=profile,
        token=token,
        cors_origins=_common.env_list("NOUL_CORS_ORIGINS"),
        allow_write=want_write,
        allowed_hosts=loopback_hosts(port) if loopback else None,
    )
    shown = f"[{host}]" if ":" in host else host
    print(
        f"Noul HTTP server on http://{shown}:{port} (MCP: /mcp, OpenAPI: /openapi.json, profile: {profile})",
        file=sys.stderr,
    )
    if not loopback and not token:
        print(
            "WARNING: bound to a non-loopback address without NOUL_HTTP_TOKEN; anyone who can reach this port "
            "can use the server.",
            file=sys.stderr,
        )
    if want_write and not token:
        print("WARNING: NOUL_HTTP_ALLOW_WRITE=1 ignored because NOUL_HTTP_TOKEN is not set.", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("NOUL_HTTP_LOG_LEVEL", "warning"))
