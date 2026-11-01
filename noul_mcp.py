"""Deprecated compatibility shim: `noul_mcp` now lives in `noul.server.mcp` (entry point `noul-mcp`). Removed in 2.0.0."""

import importlib
import sys
import warnings
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if (_SRC / "noul").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))  # source checkout without an install

_HINT = (
    "noul-search-agent: missing dependency '{}'. Install uv (https://docs.astral.sh/uv/) and run "
    "`uv run --project <path-to-noul-search-agent> noul-install --all` again (or `pip install noul-search-agent`)."
)


def _load(*modules):
    loaded = []
    for name in modules:
        try:
            loaded.append(importlib.import_module(name))
        except ModuleNotFoundError as exc:
            if exc.name and not exc.name.startswith("noul"):
                sys.exit(_HINT.format(exc.name))
            raise
    return loaded


warnings.warn(
    "`noul_mcp` is deprecated; use `noul.server.mcp` (entry point `noul-mcp`).", DeprecationWarning, stacklevel=2
)

_MODULES = _load(
    "noul.server.mcp",
)
for _module in _MODULES:
    globals().update({k: v for k, v in vars(_module).items() if not k.startswith("__")})
globals()["__version__"] = importlib.import_module("noul").__version__

if __name__ == "__main__":
    sys.exit(_MODULES[0].main())
