"""Pieces shared by the MCP servers (SDK-based `noul.server.mcp`/`noul.server.http` and the legacy server)."""

import ipaddress
import os
import sys

from noul import __version__
from noul import api as _api
from noul import registry as _registry

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1024 * 1024
DOSSIER_PREFIX = "noul://dossier/"


def resources_list():
    """MCP resources: one Markdown resource per saved research dossier."""
    try:
        dossiers = _api.list_research_dossiers().get("dossiers") or []
    except Exception as e:
        print(f"noul: could not list research dossiers: {e}", file=sys.stderr)
        dossiers = []
    return [
        {
            "uri": f"{DOSSIER_PREFIX}{d['slug']}",
            "name": f"Research Dossier: {d['topic']}",
            "description": f"Saved research synthesis and {d.get('paper_count', 0)} papers on '{d['topic']}'",
            "mimeType": "text/markdown",
        }
        for d in dossiers
    ]


def resources_read(uri):
    if not uri or not str(uri).startswith(DOSSIER_PREFIX):
        raise ValueError(f"Unknown resource URI: {uri}")
    slug = str(uri).split(DOSSIER_PREFIX, 1)[1].strip("/")
    dossier = _api.get_research_dossier(slug)
    return [{"uri": str(uri), "mimeType": "text/markdown", "text": dossier.get("markdown") or ""}]


def visible_tools(profile=None, allow_write=True):
    """Catalog entries a client may see: the profile's tools, minus write tools when writes are not allowed."""
    return [t for t in _registry.catalog(profile) if allow_write or not t["writes"]]


def openapi_spec(host=f"http://localhost:{DEFAULT_PORT}", exclude=(), profile=None, allow_write=True):
    """OpenAPI 3.1 document for the REST tool endpoints (`POST /tools/<name>`), e.g. for ChatGPT Actions."""
    paths = {}
    for item in visible_tools(profile, allow_write):
        name = item["name"]
        if name in exclude:
            continue
        desc = item["description"]
        paths[f"/tools/{name}"] = {
            "post": {
                "operationId": name,
                "summary": desc[:120],
                "description": desc,
                "requestBody": {"required": True, "content": {"application/json": {"schema": item["inputSchema"]}}},
                "responses": {
                    "200": {"description": "Tool response"}
                    if not item["outputSchema"]
                    else {
                        "description": "Tool response",
                        "content": {"application/json": {"schema": item["outputSchema"]}},
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Noul Search Agent API",
            "version": __version__,
            "description": "Scientific literature search, Evidence Meter synthesis support, PDF download, and research dossiers.",
        },
        "servers": [{"url": host}],
        "paths": paths,
    }


def is_loopback_host(host):
    if (host or "").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def env_list(name):
    return tuple(x.strip().rstrip("/") for x in os.environ.get(name, "").split(",") if x.strip())


def resolve_profile(cli_value, default):
    """Tool profile from --profile, then NOUL_TOOL_PROFILE, then the transport default; validated."""
    profile = (cli_value or os.environ.get("NOUL_TOOL_PROFILE") or default).strip().lower()
    if profile not in _registry.PROFILES:
        raise SystemExit(f"noul-mcp: unknown tool profile {profile!r} (use one of: {', '.join(_registry.PROFILES)})")
    return profile
