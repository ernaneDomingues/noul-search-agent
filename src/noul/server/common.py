"""Pieces shared by the MCP server (`noul.server.mcp`) and its HTTP transport (`noul.server.http`)."""

from __future__ import annotations

import copy
import ipaddress
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from noul import __version__
from noul import api as _api
from noul import errors as _errors
from noul import log as _log
from noul import registry as _registry
from noul.server import limits as _limits

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1024 * 1024
# The research-project workflow, also generated into the agent template and the skill (scripts/build_site.py).
PROJECT_WORKFLOW = (
    "For a review that spans several steps or sessions, keep a research project: project_list to resume one, else "
    "project_create; search with search_papers(project=slug) so each search is logged and its results become "
    "candidates (project_add_papers for papers found another way), and include or exclude each with a reason "
    "(project_set_status); save_research_dossier exports it. project_archive hides a finished project; "
    "project_delete only when the user asks for it."
)

DOSSIER_PREFIX = "noul://dossier/"
PROJECT_PREFIX = "noul://project/"


def resources_list():
    """MCP resources: one Markdown resource per research project and per saved research dossier."""
    out = []
    try:
        projects = _api.project_list().get("projects") or []
    except Exception as e:  # noqa: BLE001 - resources/list must answer even if the project store is broken
        print(f"noul: could not list research projects: {type(e).__name__}", file=sys.stderr)
        projects = []
    for p in projects:
        c = p["counts"]
        out.append(
            {
                "uri": f"{PROJECT_PREFIX}{p['slug']}",
                "name": f"Research project: {p['title']}",
                "description": f"{c['included']} included, {c['candidate']} candidate and {c['excluded']} excluded "
                f"papers, {p['note_count']} notes",
                "mimeType": "text/markdown",
            }
        )
    try:
        dossiers = _api.list_research_dossiers().get("dossiers") or []
    except Exception as e:  # noqa: BLE001 - resources/list must answer even if a dossier is broken
        print(f"noul: could not list research dossiers: {e}", file=sys.stderr)
        dossiers = []
    out += [
        {
            "uri": f"{DOSSIER_PREFIX}{d['slug']}",
            "name": f"Research Dossier: {d['topic']}",
            "description": f"Saved research synthesis and {d.get('paper_count', 0)} papers on '{d['topic']}'",
            "mimeType": "text/markdown",
        }
        for d in dossiers
    ]
    return out


def resources_read(uri):
    uri = str(uri or "")
    if uri.startswith(PROJECT_PREFIX):
        slug = uri.split(PROJECT_PREFIX, 1)[1].strip("/")
        return [{"uri": uri, "mimeType": "text/markdown", "text": _api.project_markdown(slug)}]
    if not uri.startswith(DOSSIER_PREFIX):
        raise ValueError(f"Unknown resource URI: {uri}")
    slug = uri.split(DOSSIER_PREFIX, 1)[1].strip("/")
    dossier = _api.get_research_dossier(slug)
    return [{"uri": uri, "mimeType": "text/markdown", "text": dossier.get("markdown") or ""}]


def visible_tools(profile=None, allow_write=True, public=False):
    """Catalog entries a client may see: the profile's tools, minus write tools when writes are not allowed and
    minus the server-dossier tools on the public connector."""
    tools = [
        t
        for t in _registry.catalog(profile)
        if (allow_write or not t["writes"]) and not (public and t["name"] in PUBLIC_HIDDEN_TOOLS)
    ]
    if not allow_write:
        tools = [_without_write_args(t) for t in tools]
    return [_with_public_bounds(t) for t in tools] if public else tools


def _without_write_args(tool):
    """A read tool as a server without write access offers it: the arguments that would make it write (e.g.
    `project` of search_papers, refused on such a server) leave the schema, and the hints say it only reads."""
    if not tool["write_args"]:
        return tool
    schema = copy.deepcopy(tool["inputSchema"])
    for key in tool["write_args"]:
        schema["properties"].pop(key, None)
    annotations = {**tool["annotations"], "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    description = _registry.REGISTRY[tool["name"]].description  # without the note on the write arguments
    return {**tool, "description": description, "inputSchema": schema, "annotations": annotations}


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
        if cap is None:  # not offered on the public connector
            del schema["properties"][key]
            continue
        if prop.get("type") == "array":
            prop["maxItems"] = min(cap, prop.get("maxItems", cap))  # a public cap only ever lowers the limit
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
    components: dict[Any, Any] = {}
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
            "description": "Scientific literature search, Evidence Meter synthesis support, full-text passages, PDF download and research projects.",
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
# A cap of None removes the argument: `project` reads the server-side store, like the hidden `project_get`.
PUBLIC_ARG_CAPS: dict[str, dict[str, int | None]] = {
    "search_papers": {"limit": 25, "exclude_ids": 200, "project": None},
    "related_papers": {"limit": 25},
    "get_paper_references": {"limit": 25},
    "query_fulltexts": {"ids": 5},
    "export_papers": {"ids": 25, "project": None, "status": None},
    "suggest_boolean_queries": {"paper_ids": 10},
    "evidence_meter": {"items": 100},
}


def public_caps(name):
    """Caps for `name` on the public connector (passed to registry.call, which applies them after validation)."""
    return PUBLIC_ARG_CAPS.get(name)


# Server-side dossiers are never exposed by the public connector.
PUBLIC_HIDDEN_TOOLS = frozenset({"list_research_dossiers", "get_research_dossier", "project_list", "project_get"})


# -----------------------------------------------------------------------------------------------------------------
# Tool dispatch: the one place that decides whether a call may run, for the MCP handler and the REST route alike.


@dataclass(frozen=True)
class Policy:
    """What a server instance lets its clients do; MCP and REST share one policy, hence one set of budgets."""

    profile: str = "all"
    allow_write: bool = True
    public: bool = False
    tool_limiter: _limits.RateLimiter | None = None  # per-instance ceiling on tool calls
    concurrency: _limits.Concurrency | None = None  # tool calls running at once

    def __post_init__(self) -> None:
        if self.public:  # the hosted connector is read-only, whatever else was asked for
            object.__setattr__(self, "allow_write", False)


@dataclass(frozen=True)
class Refusal:
    """A call that will not run. `text` is what the client reads; `status` and `retry_after` are for HTTP."""

    label: str
    message: str
    status: int
    retry_after: int | None = None

    @property
    def text(self) -> str:
        return f"{self.label}: {self.message}"


@dataclass
class Call:
    """An admitted call. It holds a concurrency slot until `release()`, which `run()` calls when the tool returns
    or raises. A transport that awaits `run()` in a worker thread must also call `release()` in a `finally`:
    a call cancelled before its thread starts never reaches `run()`. Releasing twice is harmless."""

    name: str
    args: Any
    caps: dict[str, int | None] | None
    concurrency: _limits.Concurrency | None
    _released: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def run(self) -> Any:
        """Execute the tool (blocking: call it from a worker thread)."""
        try:
            return _registry.call(self.name, self.args, caps=self.caps)
        finally:
            self.release()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        if self.concurrency is not None:
            self.concurrency.release()


def refusal_for(name: str, args: Any, policy: Policy) -> Refusal | None:
    """Why `policy` forbids this call (unknown or hidden tool, writes, file reads), or None when it may run."""
    if name not in _registry.TOOLS:
        return Refusal("NotFound", f"unknown tool '{name}'", 404)
    if policy.public and name in PUBLIC_HIDDEN_TOOLS:
        why = "the public connector does not expose the server's research projects and dossiers"
        return Refusal("NotAvailable", f"tool '{name}' is not available here: {why}.", 404)
    if name not in {t["name"] for t in visible_tools(policy.profile, allow_write=True, public=policy.public)}:
        why = f"it is not in the '{policy.profile}' tool profile (NOUL_TOOL_PROFILE / --profile)"
        return Refusal("NotAvailable", f"tool '{name}' is not available here: {why}.", 404)
    if policy.allow_write:
        return None
    if name in _registry.write_tools():
        why = "it writes (to disk or an external account) and writes are disabled on this HTTP server"
        return Refusal("NotAvailable", f"tool '{name}' is not available here: {why}.", 403)
    if _registry.call_writes(name, args):
        return Refusal(
            "PermissionError",
            f"Tool '{name}' writes (to disk or an external account) with these arguments and is disabled over HTTP "
            "(set NOUL_HTTP_TOKEN and NOUL_HTTP_ALLOW_WRITE=1 to enable it).",
            403,
        )
    if _registry.call_reads_file(name, args):
        return Refusal("PermissionError", f"Tool '{name}' cannot read files on this server: send the text itself.", 403)
    return None


def dispatch(name: str, args: Any, policy: Policy) -> Call | Refusal:
    """Admit one tool call under `policy`: permission first, then the tool-call rate limit, then a concurrency
    slot. Returns the `Call` to run, or the `Refusal` to report (nothing is held in that case)."""
    refusal = refusal_for(name, args, policy)
    if refusal is not None:
        return refusal
    if policy.tool_limiter is not None:
        try:
            _registry.check_args(name, args)  # malformed calls are refused before they use the shared budget
        except _errors.HTTPError as e:
            label = "InvalidArguments" if e.code == 400 else "NotFound"
            return Refusal(label, str(e), e.code)
    if policy.tool_limiter is not None and (wait := policy.tool_limiter.check("tools")) > 0:
        seconds = max(1, int(wait + 0.999))
        return Refusal(
            "RateLimited",
            f"the public Noul connector reached its tool-call limit. Wait about {seconds} s and retry this call "
            "once; if it fails again, tell the user the public server is busy and continue with the results you "
            "already have.",
            429,
            retry_after=seconds,
        )
    if policy.concurrency is not None and not policy.concurrency.try_acquire():
        return Refusal(
            "Busy",
            "the Noul server is at capacity. Wait about 5 s and retry this call once; if it fails again, tell the "
            "user the server is busy and continue with the results you already have.",
            503,
            retry_after=5,
        )
    return Call(name, args, public_caps(name) if policy.public else None, policy.concurrency)


def error_text(exc: Exception, public: bool = False) -> str:
    """Error text for the host model: argument/lookup problems are not network failures (do not retry them).

    On the public connector an error that is not an HTTPError (a bug, a broken disk) is reported by its type
    only: its message could show the server's paths to anyone."""
    code = getattr(exc, "code", None)
    if code == 400:
        return f"InvalidArguments: {exc}"
    if code == 404:
        return f"NotFound: {exc}"
    if public and not isinstance(exc, _errors.HTTPError):
        return f"{type(exc).__name__}: internal error"
    return f"{type(exc).__name__}: {exc}"


def error_status(exc: Exception) -> int:
    """HTTP status for a tool that raised: its own 4xx/5xx code when it has one, else 500."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) and 400 <= code < 600 else 500
