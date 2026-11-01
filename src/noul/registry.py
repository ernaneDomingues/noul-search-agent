"""Single tool registry: every MCP/HTTP/CLI tool is declared once with `@tool(...)` next to its function.

The registry is the source of truth for tool names, descriptions, argument models (`noul.schemas.tool_inputs`),
write/network flags and profiles. MCP `tools/list`, the OpenAPI document, the CLI (`noul tool <name>`), the site
catalog and the SKILL tool list are all derived from it."""

from __future__ import annotations

import sys
import time
import types
import typing
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from noul import errors as _errors
from noul import log as _log

_JSON_TYPES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input: type[BaseModel]
    fn: Callable[..., Any]
    writes: bool = False
    network: bool = True
    profiles: tuple[str, ...] = ("all",)
    # Arguments that make an otherwise read-only call write to disk (e.g. `output_path`).
    write_args: tuple[str, ...] = ()
    # Pydantic model the tool's result is a dump of (advertised as MCP outputSchema).
    output: type[BaseModel] | None = None
    # Overwrites or deletes user data (MCP destructiveHint).
    destructive: bool = False
    title: str | None = None
    # Same arguments -> same end state (MCP idempotentHint); None: derived (read-only tools are idempotent).
    idempotent: bool | None = None


REGISTRY: dict[str, Tool] = {}

# Tool profiles: `research` is the core every profile includes; `all` is every tool.
PROFILES = ("research", "review", "write", "all")


def in_profile(tool: Tool, profile: str | None) -> bool:
    if profile in (None, "all"):
        return True
    if profile not in PROFILES:
        raise ValueError(f"Unknown tool profile {profile!r}; use one of: {', '.join(PROFILES)}")
    return "research" in tool.profiles or profile in tool.profiles


def tool_title(tool: Tool) -> str:
    return tool.title or tool.name.replace("_", " ").capitalize()


def tool_annotations(tool: Tool) -> dict[str, Any]:
    """MCP tool annotations derived from the registry flags (hints for clients, not security boundaries)."""
    read_only = not tool.writes and not tool.write_args
    return {
        "title": tool_title(tool),
        "readOnlyHint": read_only,
        "destructiveHint": tool.destructive,
        "idempotentHint": read_only if tool.idempotent is None else tool.idempotent,
        "openWorldHint": tool.network,
    }


def output_schema(name: str) -> dict[str, Any] | None:
    model = _loaded()[name].output
    return model.model_json_schema(mode="serialization") if model is not None else None


def _loaded() -> dict[str, Tool]:
    """The registry with every tool module imported (tools register themselves when `noul.api` is imported)."""
    import noul.api  # noqa: F401  (lazy: noul.api imports this module)

    return REGISTRY


def tool(
    name: str,
    *,
    description: str,
    input: type[BaseModel],
    writes: bool = False,
    network: bool = True,
    profiles: tuple[str, ...] = ("all",),
    write_args: tuple[str, ...] = (),
    output: type[BaseModel] | None = None,
    destructive: bool = False,
    title: str | None = None,
    idempotent: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register the decorated function as tool `name` (the function itself is returned unchanged)."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in REGISTRY:
            raise ValueError(f"tool {name!r} registered twice")
        REGISTRY[name] = Tool(
            name,
            description,
            input,
            fn,
            writes,
            network,
            tuple(profiles),
            tuple(write_args),
            output,
            destructive,
            title,
            idempotent,
        )
        return fn

    return register


# ---------------------------------------------------------------------------------------------------------------
# JSON schema (MCP inputSchema) derived from the input models, in the compact v1 format clients already know.


def _unwrap_optional(ann: Any) -> Any:
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return ann


def _type_schema(ann: Any) -> dict[str, Any]:
    ann = _unwrap_optional(ann)
    origin = typing.get_origin(ann)
    if origin is typing.Literal:
        values = list(typing.get_args(ann))
        return {"type": _JSON_TYPES[type(values[0])], "enum": values}
    if origin is list:
        (item,) = typing.get_args(ann)
        return {"type": "array", "items": _type_schema(item)}
    if origin is dict:
        return {"type": "object"}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return _object_schema(ann)
    return {"type": _JSON_TYPES[ann]}


def _field_schema(field: Any, owner: type[BaseModel]) -> dict[str, Any]:
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    schema: dict[str, Any]
    if extra.get("schema_items"):
        # A list validated loosely (by the tool itself) whose items are advertised with a documentation model,
        # named in the same module as the owning input model.
        item_model = getattr(sys.modules[owner.__module__], str(extra["schema_items"]))
        schema = {"type": "array", "items": _object_schema(item_model)}
    else:
        schema = _type_schema(field.annotation)
    # `Literal[...] | None` is cached by value set, so optional enums carry their declared order explicitly.
    order = extra.get("enum_order")
    if order:
        target = schema["items"] if schema.get("type") == "array" else schema
        if set(order) != set(target.get("enum", ())):
            raise ValueError(f"enum_order {order} does not match the Literal values {target.get('enum')}")
        target["enum"] = list(order)
    if field.description:
        schema["description"] = field.description
    if not field.is_required() and field.default is not None:
        schema["default"] = field.default
    for meta in field.metadata:
        for attr, key in (("ge", "minimum"), ("le", "maximum")):
            if getattr(meta, attr, None) is not None:
                schema[key] = getattr(meta, attr)
    return schema


def _object_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: _field_schema(f, model) for name, f in model.model_fields.items()},
        "required": [name for name, f in model.model_fields.items() if f.is_required()],
    }


def input_schema(name: str) -> dict[str, Any]:
    """MCP `inputSchema` of a tool."""
    return _object_schema(_loaded()[name].input)


def catalog(profile: str | None = None) -> list[dict[str, Any]]:
    """Name, description, flags, annotations and schemas of every tool in `profile` (None/"all": every tool)."""
    return [
        {
            "name": t.name,
            "title": tool_title(t),
            "description": t.description,
            "inputSchema": input_schema(t.name),
            "outputSchema": output_schema(t.name),
            "annotations": tool_annotations(t),
            "writes": t.writes,
            "network": t.network,
            "profiles": list(t.profiles),
            "write_args": list(t.write_args),
        }
        for t in _loaded().values()
        if in_profile(t, profile)
    ]


def write_tools() -> frozenset[str]:
    """Tools that write to disk or change configuration (hidden and blocked over HTTP unless explicitly allowed)."""
    return frozenset(t.name for t in _loaded().values() if t.writes)


def call_writes(name: Any, args: Any) -> bool:
    """True when this call would write to disk: a write tool, or a write argument (e.g. `output_path`) is set.

    Servers use it to block writes over HTTP; unknown tools return False (the call then fails with 404)."""
    tool = _loaded().get(name) if isinstance(name, str) else None
    if tool is None:
        return False
    if tool.writes:
        return True
    return isinstance(args, dict) and any(args.get(a) not in (None, "") for a in tool.write_args)


class _ToolsView(Mapping[str, tuple[str, dict[str, Any], list[str], Callable[..., Any]]]):
    """Read-only v1-compatible view: name -> (description, properties, required, function)."""

    def __getitem__(self, name: str) -> tuple[str, dict[str, Any], list[str], Callable[..., Any]]:
        t = _loaded()[name]
        schema = input_schema(name)
        return t.description, schema["properties"], schema["required"], t.fn

    def __iter__(self) -> Iterator[str]:
        return iter(_loaded())

    def __len__(self) -> int:
        return len(_loaded())


TOOLS = _ToolsView()


# ---------------------------------------------------------------------------------------------------------------
# Calls


def _validation_message(name: str, exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "arguments"
        parts.append(f"'{loc}': {err['msg']}")
    return f"Invalid argument(s) for {name}: " + "; ".join(parts)


def _fold_enum_case(annotation: Any, value: Any) -> Any:
    """v1 accepted enum values in any case (e.g. `RCT`, `Citations`): fold strings to the lower-case member."""
    inner = _unwrap_optional(annotation)
    is_list = typing.get_origin(inner) is list
    literal = typing.get_args(inner)[0] if is_list else inner
    if typing.get_origin(literal) is not typing.Literal:
        return value
    members = {str(m).lower(): m for m in typing.get_args(literal) if isinstance(m, str)}

    def fold(v: Any) -> Any:
        return members.get(v.lower(), v) if isinstance(v, str) else v

    if is_list and isinstance(value, list):
        return [fold(v) for v in value]
    return fold(value)


def call(name: str, args: Any = None) -> Any:
    """Validate `args` against the tool's input model and run it. Errors are `HTTPError` (400/404)."""
    if name not in _loaded():
        raise _errors.HTTPError(404, f"Unknown tool {name}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise _errors.HTTPError(400, f"Arguments for {name} must be a JSON object")
    t = REGISTRY[name]
    fields = t.input.model_fields
    unknown = sorted(k for k in args if k not in fields)
    if unknown:
        raise _errors.HTTPError(400, f"Unknown argument(s) for {name}: {', '.join(unknown)}")
    missing = [k for k, f in fields.items() if f.is_required() and args.get(k) is None]
    if missing:
        raise _errors.HTTPError(400, f"Missing required argument(s) for {name}: {', '.join(missing)}")
    args = {k: _fold_enum_case(fields[k].annotation, v) for k, v in args.items()}
    try:
        model = t.input.model_validate(args)
    except ValidationError as exc:
        _log.logger.debug("tool rejected name=%s fields=%s", name, sorted({str(e["loc"][0]) for e in exc.errors()}))
        raise _errors.HTTPError(400, _validation_message(name, exc)) from None
    # Only the arguments the caller sent are passed, so the function's own defaults still apply. An explicit null
    # for a field with a non-null default means "use the default" (the value the schema advertises).
    kwargs = {
        k: getattr(model, k)
        for k, v in args.items()
        if not (v is None and not fields[k].is_required() and fields[k].default is not None)
    }
    for k, v in kwargs.items():
        if isinstance(v, list):
            kwargs[k] = [x.model_dump(exclude_unset=True) if isinstance(x, BaseModel) else x for x in v]
    trace_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    _log.logger.debug("tool start name=%s trace_id=%s args=%s", name, trace_id, sorted(kwargs))
    try:
        result = t.fn(**kwargs)
    except Exception as exc:
        # Only the exception type and status: messages can echo user input (e-mail, paths) and the caller sees them.
        _log.logger.info(
            "tool error name=%s trace_id=%s duration_ms=%.0f error=%s status=%s",
            name,
            trace_id,
            (time.perf_counter() - started) * 1000,
            type(exc).__name__,
            getattr(exc, "code", None),
        )
        raise
    _log.logger.debug(
        "tool done name=%s trace_id=%s duration_ms=%.0f", name, trace_id, (time.perf_counter() - started) * 1000
    )
    return result
