"""Package layout: no v1 root shims (removed in 2.0.0), `python -m noul`, and the installer works in-process."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support

ROOT = _support.ROOT


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class ShimTests(unittest.TestCase):
    def test_v1_root_shims_and_legacy_server_are_gone(self):
        # Removed in 2.0.0: the package `noul` is the only import path, `noul-mcp` the only server.
        for name in ("noul_core.py", "noul_tools.py", "noul_mcp.py", "noul_cli.py", "install.py"):
            with self.subTest(file=name):
                self.assertFalse((ROOT / name).exists())
        self.assertFalse((ROOT / "src/noul/server/legacy.py").exists())
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertFalse([f for f in package.get("files", []) if f.startswith(("noul_", "install.py"))])

    def test_core_symbols_live_in_their_new_modules(self):
        # `_support.core_target` patches these homes: every mapped name must exist there.
        for name, home in _support.CORE_HOMES.items():
            with self.subTest(name=name):
                self.assertTrue(hasattr(sys.modules.get(home) or __import__(home, fromlist=["x"]), name))

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


class LauncherTests(unittest.TestCase):
    """noul._bin launchers: what `noul-install` registers when no console script exists (source checkouts)."""

    def test_launchers_ignore_a_planted_package_in_the_project_folder(self):
        from noul import install

        with tempfile.TemporaryDirectory() as project:
            # A cloned repository with its own `noul` package and a stdlib-named module must never be imported.
            (Path(project) / "noul").mkdir()
            (Path(project) / "noul/__init__.py").write_text("raise SystemExit('planted noul imported')\n")
            (Path(project) / "json.py").write_text("raise SystemExit('planted json imported')\n")
            for launcher, name in (("noul_cli_launcher.py", "noul-search-agent"), ("noul_mcp_launcher.py", "noul-mcp")):
                with self.subTest(launcher=launcher):
                    result = subprocess.run(
                        [sys.executable, str(install._LAUNCHERS / launcher), "--version"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=project,
                        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"{name} 2.1.0", result.stdout)
                    self.assertNotIn("planted", result.stdout + result.stderr)

    def test_installer_falls_back_to_the_absolute_launchers(self):
        from noul import install

        with (
            mock.patch.object(install, "_console_script", return_value=None),
            mock.patch.object(install, "RUNNER", "python"),
            mock.patch.object(install, "FROZEN", False),
        ):
            command, args = install.mcp_command()
            self.assertEqual((command, args), (sys.executable, [str(install._LAUNCHERS / "noul_mcp_launcher.py")]))
            self.assertIn("noul_cli_launcher.py", install.cli_command())
        self.assertTrue(install._LAUNCHERS.is_absolute())


if __name__ == "__main__":
    unittest.main()
