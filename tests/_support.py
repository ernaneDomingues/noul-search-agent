"""Shared offline test helpers (isolated cache/workspace, no network)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def isolate_environment():
    """Call from setUpModule: points cache, config and workspace to a throw-away directory."""
    temp = tempfile.TemporaryDirectory(prefix="noul-tests-")
    unittest.addModuleCleanup(temp.cleanup)
    base = Path(temp.name)
    for folder in ("home", "cache", "workspace"):
        (base / folder).mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("NOUL_") and k not in ("DB_PATH", "UNPAYWALL_EMAIL")}
    env.update({
        "HOME": str(base / "home"),
        "USERPROFILE": str(base / "home"),
        "NOUL_HOME": str(base / "cache"),
        "NOUL_DB_PATH": str(base / "cache/cache.db"),
        "NOUL_WORKSPACE": str(base / "workspace"),
    })
    patcher = mock.patch.dict(os.environ, env, clear=True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    return env


def no_network(*args, **kwargs):
    raise AssertionError(f"unexpected network access: {args[:1]}")
