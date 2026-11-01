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

        with tempfile.TemporaryDirectory() as bundle, tempfile.TemporaryDirectory() as app:
            (Path(bundle) / "skills/noul-search").mkdir(parents=True)
            (Path(bundle) / "skills/noul-search/SKILL.md").write_text("skill", encoding="utf-8")
            exe = Path(bundle) / "noul.exe"
            exe.write_text("", encoding="utf-8")
            with (
                mock.patch.object(install, "FROZEN", True),
                mock.patch.object(sys, "_MEIPASS", bundle, create=True),
                mock.patch.object(sys, "executable", str(exe)),
                mock.patch.object(install, "RUNNER", "python"),
                mock.patch.object(install, "app_dir", return_value=Path(app)),
            ):
                self.assertEqual(install._asset_root(), Path(bundle))
                target = install.frozen_target()
                self.assertEqual(target.parent, Path(app))
                self.assertEqual(install.mcp_command(), (str(target), ["mcp"]))
                self.assertIn(str(target), install.cli_command())

    def test_install_copies_to_a_stable_folder_and_never_references_the_bundle(self):
        import shutil

        from noul import install

        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        bundle, app = home / "_MEI123", home / "app"
        for name in install.APP_FILES:
            shutil.copytree(ROOT / name, bundle / name)
        exe = home / "Downloads/noul"
        exe.parent.mkdir()
        exe.write_bytes(b"#!/bin/sh\n")
        with (
            mock.patch.object(install, "FROZEN", True),
            mock.patch.object(install, "HOME", home),
            mock.patch.object(sys, "_MEIPASS", str(bundle), create=True),
            mock.patch.object(sys, "executable", str(exe)),
            mock.patch.object(install, "app_dir", return_value=app),
            mock.patch.dict("os.environ", {"NOUL_INSTALL_RUNNER": "python"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(install.main(["claude", "codex", "gemini"]), 0)
            target = install.frozen_target()
            self.assertTrue(target.is_file())
            self.assertTrue((app / "skills/noul-search/SKILL.md").is_file())
            written = [f for f in home.rglob("*") if f.is_file() and app not in f.parents and bundle not in f.parents]
            texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in written if f != exe}
            self.assertTrue(texts)
            for f, text in texts.items():
                self.assertNotIn(str(bundle), text, f)
                self.assertNotIn(str(exe), text, f)
            self.assertIn(str(target), (home / ".codex/config.toml").read_text(encoding="utf-8"))
            self.assertFalse([f for f in home.rglob("*.tmp")], "atomic writes leave no temp files")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(install.main(["--all", "--uninstall"]), 0)
            self.assertFalse(target.exists())
            self.assertFalse((app / "skills").exists())


class AtomicWriteTests(unittest.TestCase):
    def test_failed_write_keeps_the_original(self):
        from noul import install

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "mcp.json"
            cfg.write_text('{"mcpServers": {}}\n', encoding="utf-8")
            with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                install._atomic_write(cfg, "{broken")
            self.assertEqual(cfg.read_text(encoding="utf-8"), '{"mcpServers": {}}\n')
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["mcp.json"])


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

    def test_wizard_asks_again_instead_of_installing_everywhere(self):
        answers = iter(["", "9", "cursor"])
        with (
            mock.patch.object(self.install, "detect_targets", return_value=[]),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(self.install.choose_targets(ask=lambda prompt: next(answers)), ["cursor"])
            self.assertEqual(self.install.choose_targets(ask=lambda prompt: ""), [])
        self.assertIn("invalid choice: 9", out.getvalue())

    def test_double_clicked_executable_opens_the_wizard(self):
        from noul import cli

        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(self.install, "main", return_value=0) as wizard,
            mock.patch("builtins.input", return_value=""),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main([]), 0)
        wizard.assert_called_once_with([])

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
        uv = build_mcpb.manifest("uv")
        # Hosts may run `uv run <entry_point>` themselves: it must be a root-level launcher, not a module inside the
        # package (src/noul/server/mcp.py would shadow the `mcp` SDK on sys.path).
        self.assertNotIn("/", uv["server"]["entry_point"])
        for prompt in uv["prompts"]:
            self.assertNotRegex(prompt["text"], r"(?<!\$)\{[a-z_]+\}")
            for arg in prompt["arguments"]:
                self.assertIn("${arguments." + arg + "}", prompt["text"])
        self.assertTrue(uv["privacy_policies"])
        self.assertTrue(all(u.startswith("https://") for u in uv["privacy_policies"]))
        self.assertEqual(build_mcpb.manifest("binary", "linux")["compatibility"]["platforms"], ["linux"])

    def test_staged_bundles(self):
        import build_mcpb

        with tempfile.TemporaryDirectory() as tmp:
            staged = build_mcpb.stage("uv", tmp)
            for name in ("manifest.json", "pyproject.toml", "uv.lock", "icon.png", "src/noul/server/mcp.py"):
                self.assertTrue((staged / name).exists(), name)
            self.assertIn("from noul.server.mcp import main", (staged / build_mcpb.UV_ENTRY).read_text())
            self.assertFalse(list(staged.rglob("__pycache__")))
        # Allow-list: only files git tracks under the bundled folders.
        stray = ROOT / "src/noul/_untracked_secret_test.env"
        stray.write_text("TOKEN=x", encoding="utf-8")
        self.addCleanup(stray.unlink)
        self.assertNotIn("src/noul/_untracked_secret_test.env", build_mcpb.tracked_files())
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
        for asset in (
            "noul.mcpb",
            "noul-windows-x64.exe",
            "noul-macos-arm64.tar.gz",
            "noul-linux-x64.tar.gz",
            "SHA256SUMS",
        ):
            self.assertIn(f"/releases/latest/download/{asset}", live)
        self.assertIn("gh attestation verify", live)
        for key, bad in (("connector_url", "javascript:alert(1)"), ("release_base", "http://x"), ("repository", "")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_site.render_install_html({**base, key: bad})

    def test_committed_page_is_in_sync(self):
        import build_site

        expected = build_site.render_install_html(json.loads((ROOT / "site/config.json").read_text(encoding="utf-8")))
        self.assertEqual((ROOT / "site/install.html").read_text(encoding="utf-8"), expected)


class HostSettingsTests(unittest.TestCase):
    def test_unknown_profile_from_the_environment_falls_back(self):
        from noul.server import common

        with mock.patch.dict("os.environ", {"NOUL_TOOL_PROFILE": "reserch"}):
            with self.assertLogs("noul", "WARNING"):
                self.assertEqual(common.resolve_profile(None, "all"), "all")
            with self.assertRaises(SystemExit):
                common.resolve_profile("reserch", "all")
        with mock.patch.dict("os.environ", {"NOUL_TOOL_PROFILE": " Research "}):
            self.assertEqual(common.resolve_profile(None, "all"), "research")

    def test_workspace_from_the_host_wins_over_config_json(self):
        from noul import config

        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as other:
            with (
                mock.patch.object(config, "_load_config_file", return_value={"workspace": other}),
                mock.patch.dict("os.environ", {"NOUL_WORKSPACE": ws}),
            ):
                self.assertEqual(config.get_config()["workspace"], str(Path(ws).resolve()))
            with (
                mock.patch.object(config, "_load_config_file", return_value={}),
                mock.patch.dict("os.environ", {"NOUL_WORKSPACE": ""}),
                mock.patch("os.getcwd", return_value=str(Path("/").resolve())),
            ):
                self.assertTrue(config.get_config()["workspace"].endswith(str(Path("Documents/Noul"))))


if __name__ == "__main__":
    unittest.main()
