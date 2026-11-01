"""Phase 2b-2: single-file executable support, installer wizard, VS Code/Cursor targets, .mcpb manifest, install page."""

import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support

ROOT = _support.ROOT
sys.path.insert(0, str(ROOT / "scripts"))


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class FrozenExecutableTests(unittest.TestCase):
    def test_templates_and_server_command_come_from_the_executable(self):
        from noul import install

        with tempfile.TemporaryDirectory() as bundle:
            (Path(bundle) / "skills/noul-search").mkdir(parents=True)
            (Path(bundle) / "skills/noul-search/SKILL.md").write_text("skill", encoding="utf-8")
            exe = Path(bundle) / "noul.exe"
            exe.write_text("", encoding="utf-8")
            with (
                mock.patch.object(install, "FROZEN", True),
                mock.patch.object(sys, "_MEIPASS", bundle, create=True),
                mock.patch.object(sys, "executable", str(exe)),
                mock.patch.object(install, "RUNNER", "python"),
            ):
                self.assertEqual(install._asset_root(), Path(bundle))
                self.assertEqual(install.mcp_command(), (str(exe.resolve()), ["mcp"]))
                self.assertIn(str(exe.resolve()), install.cli_command())


class InstallerWizardTests(unittest.TestCase):
    def setUp(self):
        from noul import install

        self.install = install
        self.home = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(install, "HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_detection_and_choice(self):
        (self.home / ".cursor").mkdir()
        (self.home / ".codex").mkdir()
        with mock.patch("shutil.which", return_value=None), mock.patch.dict("os.environ", {"APPDATA": ""}):
            detected = self.install.detect_targets()
            self.assertIn("cursor", detected)
            self.assertIn("codex", detected)
            self.assertNotIn("gemini", detected)
            out = io.StringIO()
            with redirect_stdout(out):
                default_pick = self.install.choose_targets(ask=lambda prompt: "")
                numbered = self.install.choose_targets(ask=lambda prompt: "1, 4")
                everything = self.install.choose_targets(ask=lambda prompt: "all")
                cancelled = self.install.choose_targets(ask=lambda prompt: "q")
        self.assertEqual(set(default_pick), {"cursor", "codex"})
        self.assertEqual(numbered, ["claude", "cursor"])
        self.assertEqual(everything, list(self.install.TARGETS))
        self.assertEqual(cancelled, [])
        self.assertIn("(detectado / detected)", out.getvalue())

    def test_interactive_main_installs_only_the_chosen_apps(self):
        with (
            mock.patch.object(self.install, "_interactive", return_value=True),
            mock.patch.object(self.install, "choose_targets", return_value=["cursor"]),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self.install.main([]), 0)
        cursor = json.loads((self.home / ".cursor/mcp.json").read_text(encoding="utf-8"))
        self.assertIn("noul", cursor["mcpServers"])
        self.assertFalse((self.home / ".claude").exists())

    def test_vscode_target_uses_the_servers_key(self):
        with mock.patch.object(self.install, "_app_config_dir", return_value=self.home / "Code"):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.install.main(["vscode"]), 0)
            cfg = json.loads((self.home / "Code/User/mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["servers"]["noul"]["type"], "stdio")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.install.main(["vscode", "--uninstall"]), 0)
            cfg = json.loads((self.home / "Code/User/mcp.json").read_text(encoding="utf-8"))
            self.assertNotIn("noul", cfg["servers"])


class McpbManifestTests(unittest.TestCase):
    def test_manifest_tracks_the_registry(self):
        import build_mcpb

        from noul import registry
        from noul.server.mcp import PROMPTS

        for variant, platform in (("uv", None), ("binary", "win32"), ("binary", "darwin")):
            with self.subTest(variant=variant, platform=platform):
                m = build_mcpb.manifest(variant, platform)
                self.assertEqual(m["manifest_version"], "0.4")
                self.assertEqual([t["name"] for t in m["tools"]], list(registry.TOOLS))
                self.assertEqual([p["name"] for p in m["prompts"]], list(PROMPTS))
                self.assertEqual(set(m["user_config"]), {"email", "workspace", "profile"})
                env = m["server"]["mcp_config"]["env"]
                self.assertEqual(env["NOUL_EMAIL"], "${user_config.email}")
                self.assertEqual(m["server"]["type"], variant)
        self.assertEqual(build_mcpb.manifest("binary", "win32")["server"]["entry_point"], "server/noul.exe")
        self.assertEqual(build_mcpb.manifest("binary", "linux")["compatibility"]["platforms"], ["linux"])

    def test_staged_bundles(self):
        import build_mcpb

        with tempfile.TemporaryDirectory() as tmp:
            staged = build_mcpb.stage("uv", tmp)
            for name in ("manifest.json", "pyproject.toml", "uv.lock", "icon.png", "src/noul/server/mcp.py"):
                self.assertTrue((staged / name).exists(), name)
            self.assertFalse(list(staged.rglob("__pycache__")))
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as exe_dir:
            exe = Path(exe_dir) / "noul"
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            staged = build_mcpb.stage("binary", tmp, exe=exe, platform="linux")
            self.assertTrue((staged / "server/noul").exists())
            self.assertEqual(json.loads((staged / "manifest.json").read_text())["icon"], "icon.png")
            self.assertTrue((staged / "icon.png").read_bytes().startswith(b"\x89PNG"))


class InstallPageTests(unittest.TestCase):
    def test_deeplinks_round_trip(self):
        import build_site

        url = "https://noul-connector-abc.run.app/mcp"
        links = build_site.deeplinks(url)
        vscode = json.loads(urllib.parse.unquote(links["vscode"].split("vscode:mcp/install?", 1)[1]))
        self.assertEqual(vscode, {"name": "noul", "type": "http", "url": url})
        query = urllib.parse.parse_qs(links["cursor"].split("?", 1)[1])
        self.assertEqual(query["name"], ["noul"])
        self.assertEqual(json.loads(base64.b64decode(query["config"][0])), {"url": url})

    def test_page_with_and_without_a_connector(self):
        import build_site

        base = json.loads((ROOT / "site/config.json").read_text(encoding="utf-8"))
        pending = build_site.render_install_html({**base, "connector_url": ""})
        self.assertIn("ainda não está no ar", pending)
        self.assertNotIn("vscode:mcp/install", pending)
        live = build_site.render_install_html({**base, "connector_url": "https://x.run.app/mcp"})
        self.assertIn("https://x.run.app/mcp", live)
        self.assertIn("vscode:mcp/install?", live)
        self.assertIn("cursor://anysphere.cursor-deeplink/mcp/install?name=noul", live)
        for asset in ("noul.mcpb", "noul-windows-x64.exe", "noul-macos-arm64", "noul-linux-x64", "SHA256SUMS"):
            self.assertIn(f"/releases/latest/download/{asset}", live)

    def test_committed_page_is_in_sync(self):
        import build_site

        expected = build_site.render_install_html(json.loads((ROOT / "site/config.json").read_text(encoding="utf-8")))
        self.assertEqual((ROOT / "site/install.html").read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
