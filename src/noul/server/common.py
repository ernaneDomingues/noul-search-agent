"""Pieces shared by the MCP servers (SDK-based `noul.server.mcp`/`noul.server.http` and the legacy server)."""

import copy
import ipaddress
import os
import sys

from noul import __version__
from noul import api as _api
from noul import log as _log
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


def visible_tools(profile=None, allow_write=True, public=False):
    """Catalog entries a client may see: the profile's tools, minus write tools when writes are not allowed and
    minus the server-dossier tools on the public connector."""
    tools = [
        t
        for t in _registry.catalog(profile)
        if (allow_write or not t["writes"]) and not (public and t["name"] in PUBLIC_HIDDEN_TOOLS)
    ]
    return [_with_public_bounds(t) for t in tools] if public else tools


def _with_public_bounds(tool):
    """Advertise the public caps in the input schema (maximum / maxItems) so the model knows them up front."""
    caps = PUBLIC_ARG_CAPS.get(tool["name"])
    if not caps:
        return tool
    schema = copy.deepcopy(tool["inputSchema"])
    for key, cap in caps.items():
        prop = schema["properties"].get(key)
        if prop is None:
            continue
        if prop.get("type") == "array":
            prop["maxItems"] = cap
        else:
            prop["maximum"] = min(cap, prop.get("maximum", cap))
    return {**tool, "inputSchema": schema}


def _hoist_defs(schema, components, prefix):
    """Move a JSON Schema's local `$defs` into OpenAPI `components/schemas` and point its `#/$defs/X` refs there
    (inside an OpenAPI document refs resolve against the document root, where `$defs` does not exist). A name
    already used by a different definition gets the tool name as prefix."""
    schema = copy.deepcopy(schema)
    renames = {}
    for name, definition in (schema.pop("$defs", None) or {}).items():
        target = name if components.get(name, definition) == definition else f"{prefix}_{name}"
        renames[name] = target
        components[target] = definition

    def fix(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                node["$ref"] = "#/components/schemas/" + renames.get(ref[8:], ref[8:])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    for name in renames.values():
        fix(components[name])
    return schema


def openapi_spec(host=f"http://localhost:{DEFAULT_PORT}", exclude=(), profile=None, allow_write=True, public=False):
    """OpenAPI 3.1 document for the REST tool endpoints (`POST /tools/<name>`), e.g. for ChatGPT Actions."""
    paths = {}
    components = {}
    for item in visible_tools(profile, allow_write, public):
        name = item["name"]
        if name in exclude:
            continue
        desc = item["description"]
        paths[f"/tools/{name}"] = {
            "post": {
                "operationId": name,
                "summary": desc[:120],
                "description": desc,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": _hoist_defs(item["inputSchema"], components, name)}},
                },
                "responses": {
                    "200": {"description": "Tool response"}
                    if not item["outputSchema"]
                    else {
                        "description": "Tool response",
                        "content": {
                            "application/json": {"schema": _hoist_defs(item["outputSchema"], components, name)}
                        },
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
        **({"components": {"schemas": components}} if components else {}),
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
    """Tool profile from --profile, then NOUL_TOOL_PROFILE, then the transport default.

    An unknown --profile is an error. An unknown NOUL_TOOL_PROFILE (often typed into an app's settings form, e.g.
    the Claude Desktop extension) only logs a warning and uses `default`: refusing to start would just show
    "server disconnected" with no way to see why."""
    choices = ", ".join(_registry.PROFILES)
    if cli_value:
        profile = cli_value.strip().lower()
        if profile not in _registry.PROFILES:
            raise SystemExit(f"noul-mcp: unknown tool profile {profile!r} (use one of: {choices})")
        return profile
    profile = (os.environ.get("NOUL_TOOL_PROFILE") or "").strip().lower()
    if not profile:
        return default
    if profile not in _registry.PROFILES:
        _log.logger.warning("unknown NOUL_TOOL_PROFILE %r (use one of: %s); using %r", profile, choices, default)
        return default
    return profile


# Per-call caps on the public connector (cost/abuse control): integers are lowered, lists are truncated.
PUBLIC_ARG_CAPS: dict[str, dict[str, int]] = {
    "search_papers": {"limit": 25, "exclude_ids": 200},
    "related_papers": {"limit": 25},
    "get_paper_references": {"limit": 25},
    "query_fulltexts": {"ids": 5},
    "export_papers": {"ids": 25},
    "suggest_boolean_queries": {"paper_ids": 10},
    "evidence_meter": {"items": 100},
}


def public_caps(name):
    """Caps for `name` on the public connector (passed to registry.call, which applies them after validation)."""
    return PUBLIC_ARG_CAPS.get(name)


# Server-side dossiers are never exposed by the public connector.
PUBLIC_HIDDEN_TOOLS = frozenset({"list_research_dossiers", "get_research_dossier"})
