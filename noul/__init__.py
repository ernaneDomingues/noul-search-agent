"""Noul Search Agent modular package (schemas, providers, tools, prompts).

The legacy single-file modules (`noul_core`, `noul_tools`, `noul_mcp`, `noul_cli`) remain the public,
zero-dependency entry points; this package adds the Phase 1 capabilities on top of them. Submodules are
imported lazily by `noul_core`/`noul_tools`, so importing `noul` never has side effects."""

# Kept in sync with noul_core.__version__ (enforced by tests/test_foundation.py).
__version__ = "1.1.0"
