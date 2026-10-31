"""Phase 1a layout: deprecated root shims still expose the old names, and the installer works in-process."""

import io
import json
import os
import subprocess
import sys
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support

ROOT = _support.ROOT


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class ShimTests(unittest.TestCase):
    def test_shims_reexport_the_package_and_warn(self):
        for shim in ("noul_core", "noul_tools", "noul_mcp", "noul_cli", "install"):
            sys.modules.pop(shim, None)
            with self.subTest(shim=shim), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                module = __import__(shim)
                self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
                self.assertTrue(module.__version__)

    def test_old_public_names_point_to_the_new_modules(self):
        import noul_core
        import noul_tools

        import noul.api
        import noul.net.http
        import noul.search.engine

        self.assertIs(noul_core.search, noul.search.engine.search)
        self.assertIs(noul_core._validate_public_url, noul.net.http._validate_public_url)
        self.assertIs(noul_tools.call, noul.api.call)
        self.assertEqual(set(noul_tools.TOOLS), set(noul.api.TOOLS))
        for name, home in _support.CORE_HOMES.items():
            with self.subTest(name=name):
                self.assertTrue(hasattr(sys.modules.get(home) or __import__(home, fromlist=["x"]), name))

    def test_script_shim_without_dependencies_prints_guidance(self):
        # `-S` hides site-packages, so pydantic is missing exactly as in a bare checkout run by system Python.
        result = subprocess.run(
            [sys.executable, "-S", str(ROOT / "noul_mcp.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=ROOT,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        if result.returncode == 0:
            self.skipTest("pydantic is importable without site-packages in this interpreter")
        self.assertIn("missing dependency", result.stderr)
        self.assertIn("uv", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_python_dash_m_noul_runs_the_cli(self):
        result = subprocess.run(
            [sys.executable, "-m", "noul", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=ROOT,
            env=dict(os.environ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("noul-search-agent", result.stdout)


class InstallerInProcessTests(unittest.TestCase):
    def run_installer(self, *args):
        from noul import install

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(install, "HOME", Path(ENV["HOME"])):
            code = install.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.run_installer("--all", "--dry-run")
        self.assertEqual(code or 0, 0)
        self.assertTrue(out.strip())
        self.assertFalse((Path(ENV["HOME"]) / ".claude" / "skills").exists())

    def test_install_points_agents_at_python_dash_m_noul_and_uninstalls_cleanly(self):
        code, _, err = self.run_installer("claude", "--runner", "python")
        self.assertEqual(code or 0, 0, err)
        home = Path(ENV["HOME"])
        skill = (home / ".claude/skills/noul-search/SKILL.md").read_text(encoding="utf-8")
        from noul import install

        cli = install.cli_command()
        self.assertIn(cli, skill)
        # Never `python -m noul`: agents run servers inside untrusted project folders (sys.path[0] = cwd).
        self.assertNotIn("-m noul", skill)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", skill)
        command, args = install.mcp_command()
        self.assertTrue(Path(command).is_absolute(), command)
        self.assertNotIn("-m", args)
        mcp_files = [p for p in (home / ".claude.json", home / ".claude/settings.json") if p.exists()]
        servers = {}
        for path in mcp_files:
            servers.update(json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}))
        if "noul" in servers:
            self.assertEqual([servers["noul"]["command"], *servers["noul"]["args"]], [command, *args])
        code, _, err = self.run_installer("claude", "--uninstall")
        self.assertEqual(code or 0, 0, err)
        self.assertFalse((home / ".claude/skills/noul-search/SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
