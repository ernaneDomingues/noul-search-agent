"""Pydantic v2 compatibility layer.

Uses `pydantic>=2` when installed (``pip install noul-search-agent[pro]`` / ``uvx``). On a bare system
Python without pydantic, a lightweight stdlib-only ``BaseModel`` with the subset of the v2 API used by
Noul is provided instead, so the package never fails with ``ModuleNotFoundError``.
Set ``NOUL_FORCE_PYDANTIC_FALLBACK=1`` to force the fallback (used by the test-suite)."""
import copy
import datetime as _dt
import json
import os
import sys
import types
import typing
from enum import Enum

_MISSING = object()


class FallbackValidationError(ValueError):
    def __init__(self, errors, model_name="Model"):
        self._errors = list(errors)
        lines = [f"{len(self._errors)} validation error(s) for {model_name}"]
        lines += [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in self._errors]
        super().__init__("\n".join(lines))

    def errors(self):
        return list(self._errors)


class FallbackHttpUrl(str):
    """`str` subclass accepting only absolute http(s) URLs."""

    def __new__(cls, value):
        if not isinstance(value, str):
            raise ValueError("URL must be a string")
        v = value.strip()
        low = v.lower()
        if not (low.startswith("http://") or low.startswith("https://")) or len(v.split("://", 1)[1]) == 0:
            raise ValueError(f"Invalid HTTP URL: {value!r} (must start with http:// or https://)")
        return super().__new__(cls, v)


class FieldInfo:
    __slots__ = ("default", "default_factory", "ge", "le", "gt", "lt", "description")

    def __init__(self, default=_MISSING, default_factory=None, ge=None, le=None, gt=None, lt=None, description=None):
        if default is Ellipsis:
            default = _MISSING
        if default is not _MISSING and default_factory is not None:
            raise TypeError("cannot specify both default and default_factory")
        self.default = default
        self.default_factory = default_factory
        self.ge, self.le, self.gt, self.lt = ge, le, gt, lt
        self.description = description

    def get_default(self):
        if self.default_factory is not None:
            return self.default_factory()
        if self.default is _MISSING:
            return _MISSING
        return copy.deepcopy(self.default)


def FallbackField(default=_MISSING, *, default_factory=None, ge=None, le=None, gt=None, lt=None,
                  description=None, **_ignored):
    return FieldInfo(default, default_factory, ge, le, gt, lt, description)


_UNION_TYPES = (typing.Union,) + ((types.UnionType,) if hasattr(types, "UnionType") else ())
_TRUE = {"true", "1", "yes", "y", "on"}
_FALSE = {"false", "0", "no", "n", "off"}


def _type_name(tp):
    return getattr(tp, "__name__", None) or str(tp)


def _coerce(tp, value):
    """Validate/coerce `value` against annotation `tp` (lax mode, close to pydantic v2 semantics)."""
    if tp is typing.Any or tp is object:
        return value
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in _UNION_TYPES:
        if value is None and type(None) in args:
            return None
        errors = []
        for arg in args:
            if arg is type(None):
                continue
            try:
                return _coerce(arg, value)
            except (TypeError, ValueError) as e:
                errors.append(str(e))
        raise ValueError("; ".join(errors) or f"value {value!r} does not match {tp}")
    if origin in (list, tuple, set, frozenset):
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError(f"expected a list, got {type(value).__name__}")
        item_tp = args[0] if args else typing.Any
        items = [_coerce(item_tp, v) for v in value]
        return items if origin is list else origin(items)
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"expected a dict, got {type(value).__name__}")
        k_tp, v_tp = args if len(args) == 2 else (typing.Any, typing.Any)
        return {_coerce(k_tp, k): _coerce(v_tp, v) for k, v in value.items()}
    if origin is typing.Literal:
        if value in args:
            return value
        raise ValueError(f"expected one of {args!r}")
    if value is None:
        raise ValueError(f"none is not an allowed value for {_type_name(tp)}")
    if not isinstance(tp, type):
        return value
    if issubclass(tp, FallbackBaseModel):
        if isinstance(value, tp):
            return value
        if isinstance(value, dict):
            return tp.model_validate(value)
        raise ValueError(f"expected {tp.__name__} or dict")
    if issubclass(tp, Enum):
        if isinstance(value, tp):
            return value
        try:
            return tp(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a valid {tp.__name__}; expected one of "
                             f"{[m.value for m in tp]}") from None
    if issubclass(tp, FallbackHttpUrl):
        return FallbackHttpUrl(value)
    if tp is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise ValueError(f"expected a boolean, got {value!r}")
    if tp is int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
            return int(value.strip())
        raise ValueError(f"expected an integer, got {value!r}")
    if tp is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        raise ValueError(f"expected a number, got {value!r}")
    if tp is str:
        if isinstance(value, str):
            return value.value if isinstance(value, Enum) else value
        raise ValueError(f"expected a string, got {type(value).__name__}")
    if tp is _dt.date:
        if isinstance(value, _dt.datetime):
            return value.date()
        if isinstance(value, _dt.date):
            return value
        if isinstance(value, str):
            try:
                return _dt.date.fromisoformat(value[:10])
            except ValueError:
                pass
        raise ValueError(f"expected an ISO date, got {value!r}")
    if isinstance(value, tp):
        return value
    raise ValueError(f"expected {tp.__name__}, got {type(value).__name__}")


def _check_bounds(info, value):
    if value is None or info is None:
        return
    if info.ge is not None and value < info.ge:
        raise ValueError(f"must be >= {info.ge}")
    if info.le is not None and value > info.le:
        raise ValueError(f"must be <= {info.le}")
    if info.gt is not None and value <= info.gt:
        raise ValueError(f"must be > {info.gt}")
    if info.lt is not None and value >= info.lt:
        raise ValueError(f"must be < {info.lt}")


def _dump(value, mode):
    if isinstance(value, FallbackBaseModel):
        return value.model_dump(mode=mode)
    if isinstance(value, list):
        return [_dump(v, mode) for v in value]
    if isinstance(value, (tuple, set, frozenset)):
        items = [_dump(v, mode) for v in value]
        return items if mode == "json" else type(value)(items)
    if isinstance(value, dict):
        return {(_dump(k, mode) if mode == "json" else k): _dump(v, mode) for k, v in value.items()}
    if mode == "json":
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, FallbackHttpUrl):
            return str(value)
        if isinstance(value, (_dt.date, _dt.datetime)):
            return value.isoformat()
    return value


class FallbackBaseModel:
    """Minimal stdlib implementation of the pydantic v2 `BaseModel` API used by Noul."""

    @classmethod
    def _noul_fields(cls):
        cached = cls.__dict__.get("__noul_fields__")
        if cached is not None:
            return cached
        module = sys.modules.get(cls.__module__)
        hints = typing.get_type_hints(cls, globalns=getattr(module, "__dict__", None))
        fields = {}
        for name, tp in hints.items():
            if name.startswith("_") or typing.get_origin(tp) is typing.ClassVar:
                continue
            raw = _MISSING
            for klass in cls.__mro__:
                if name in klass.__dict__:
                    raw = klass.__dict__[name]
                    break
            info = raw if isinstance(raw, FieldInfo) else FieldInfo(default=raw)
            fields[name] = (tp, info)
        cls.__noul_fields__ = fields
        return fields

    @classmethod
    def model_fields(cls):
        return dict(cls._noul_fields())

    def __init__(self, **data):
        errors = []
        for name, (tp, info) in self._noul_fields().items():
            if name in data:
                value = data[name]
            else:
                value = info.get_default()
                if value is _MISSING:
                    errors.append({"loc": (name,), "msg": "Field required", "type": "missing"})
                    continue
                object.__setattr__(self, name, value)
                continue
            try:
                value = _coerce(tp, value)
                _check_bounds(info, value)
            except FallbackValidationError as e:
                errors.extend({**err, "loc": (name,) + tuple(err["loc"])} for err in e.errors())
                continue
            except (TypeError, ValueError) as e:
                errors.append({"loc": (name,), "msg": str(e), "type": "value_error"})
                continue
            object.__setattr__(self, name, value)
        if errors:
            raise FallbackValidationError(errors, type(self).__name__)

    @classmethod
    def model_validate(cls, data):
        if isinstance(data, cls):
            return data
        if isinstance(data, FallbackBaseModel):
            data = data.model_dump()
        if not isinstance(data, dict):
            raise FallbackValidationError(
                [{"loc": (), "msg": f"Input should be a dict or {cls.__name__}", "type": "model_type"}], cls.__name__
            )
        return cls(**data)

    def model_dump(self, mode="python", exclude_none=False, **_ignored):
        out = {}
        for name in self._noul_fields():
            value = getattr(self, name)
            if exclude_none and value is None:
                continue
            out[name] = _dump(value, mode)
        return out

    def model_dump_json(self, indent=None, exclude_none=False, **_ignored):
        return json.dumps(self.model_dump(mode="json", exclude_none=exclude_none), ensure_ascii=False, indent=indent)

    def dict(self, **kwargs):
        return self.model_dump(**kwargs)

    def model_copy(self, update=None, deep=False):
        data = self.model_dump()
        if deep:
            data = copy.deepcopy(data)
        data.update(update or {})
        return type(self).model_validate(data)

    def __eq__(self, other):
        return type(self) is type(other) and self.model_dump() == other.model_dump()

    def __repr__(self):
        args = ", ".join(f"{k}={getattr(self, k)!r}" for k in self._noul_fields())
        return f"{type(self).__name__}({args})"


def _load_pydantic():
    if os.environ.get("NOUL_FORCE_PYDANTIC_FALLBACK") == "1":
        return None
    try:
        import pydantic
    except ImportError:
        return None
    if not hasattr(pydantic.BaseModel, "model_dump"):  # pydantic v1 lacks the v2 API we rely on
        return None
    return pydantic


_pydantic = _load_pydantic()
PYDANTIC_AVAILABLE = _pydantic is not None

if PYDANTIC_AVAILABLE:
    BaseModel = _pydantic.BaseModel
    Field = _pydantic.Field
    HttpUrl = _pydantic.HttpUrl
    ValidationError = _pydantic.ValidationError
else:
    BaseModel = FallbackBaseModel
    Field = FallbackField
    HttpUrl = FallbackHttpUrl
    ValidationError = FallbackValidationError

__all__ = [
    "BaseModel", "Field", "HttpUrl", "ValidationError", "PYDANTIC_AVAILABLE",
    "FallbackBaseModel", "FallbackField", "FallbackHttpUrl", "FallbackValidationError", "FieldInfo",
]
