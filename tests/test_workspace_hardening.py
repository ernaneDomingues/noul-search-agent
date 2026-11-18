"""Filesystem hardening: network paths refused before any filesystem call, reading tools limited to documents,
dossiers never replace another folder, dossier files never override cached papers, broad workspaces refused, and
private permissions on Noul's own files."""

import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

import _support


def setUpModule():
    global ENV
    ENV = _support.isolate_environment()


class PathTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)
        self.ws = Path(ENV["NOUL_WORKSPACE"])

    def test_network_paths_are_refused_without_touching_the_filesystem(self):
        from noul import workspace
        from noul.errors import HTTPError

        for raw in ("\\\\evil.example\\share\\paper.pdf", "//evil.example/share/x.md", "\\\\?\\C:\\x.md"):
            with self.subTest(raw=raw), mock.patch("os.stat") as stat_call, self.assertRaises(HTTPError) as caught:
                workspace.validate_path(raw)
            self.assertEqual(caught.exception.code, 403)
            stat_call.assert_not_called()
            self.assertTrue(workspace.looks_like_filepath(raw))
        with self.assertRaises(HTTPError):
            workspace.safe_output_dir("\\\\evil.example\\share")

    def test_reading_tools_read_documents_only(self):
        from noul import registry
        from noul.errors import HTTPError

        (self.ws / "prod-secrets.yaml").write_text("db_password: hunter2\n", encoding="utf-8")
        (self.ws / "draft.md").write_text("Transformers improve translation quality.\n", encoding="utf-8")
        with self.assertRaises(HTTPError) as caught:
            registry.call("recommend_citations", {"draft_text": "prod-secrets.yaml"})
        self.assertEqual(caught.exception.code, 403)
        self.assertNotIn("hunter2", str(caught.exception))
        with self.assertRaises(HTTPError):
            registry.call("audit_submission_readiness", {"draft_path": "prod-secrets.yaml"})
        from noul import workspace

        self.assertEqual(workspace.validate_path("draft.md", readable=True).name, "draft.md")


class DossierTests(unittest.TestCase):
    def setUp(self):
        _support.block_http(self)
        self.ws = Path(ENV["NOUL_WORKSPACE"])

    def test_a_dossier_never_replaces_another_folder(self):
        from noul import registry
        from noul.errors import HTTPError

        other = self.ws / "packages" / "core"
        other.mkdir(parents=True)
        (other / "README.md").write_text("# The user's own package\n", encoding="utf-8")
        with self.assertRaises(HTTPError) as caught:
            registry.call(
                "save_research_dossier", {"topic": "core", "synthesis_markdown": "x", "output_dir": "packages"}
            )
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual((other / "README.md").read_text(encoding="utf-8"), "# The user's own package\n")
        saved = registry.call("save_research_dossier", {"topic": "Sleep and memory", "synthesis_markdown": "v1"})
        again = registry.call("save_research_dossier", {"topic": "Sleep and memory", "synthesis_markdown": "v2"})
        self.assertEqual(saved["slug"], again["slug"])  # its own dossier is updated in place

    def test_a_planted_papers_json_does_not_override_cached_papers(self):
        from noul import registry
        from noul.net import cache

        cache.put_paper("doi:10.1000/real", {"id": "doi:10.1000/real", "title": "Real", "retraction": {"x": 1}})
        folder = self.ws / "research" / "planted"
        folder.mkdir(parents=True)
        (folder / "README.md").write_text("# Planted\n", encoding="utf-8")
        fake = {"slug": "planted", "papers": [{"id": "doi:10.1000/real", "title": "Fake, not retracted"}]}
        (folder / "papers.json").write_text(json.dumps(fake), encoding="utf-8")
        registry.call("get_research_dossier", {"slug": "planted"})
        self.assertEqual(cache.get_paper("doi:10.1000/real")["title"], "Real")

    def test_windows_device_names_are_not_slugs(self):
        from noul.library import store

        self.assertEqual(store.slugify("CON"), "con-topic")
        self.assertEqual(store.slugify("Com1"), "com1-topic")
        self.assertEqual(store.slugify("Console games"), "console-games")


class ConfigTests(unittest.TestCase):
    def test_broad_folders_are_never_the_workspace(self):
        from noul import config
        from noul.errors import HTTPError

        home = Path.home()
        for broad in (home, Path(home.anchor)):
            with self.subTest(folder=str(broad)), self.assertRaises(HTTPError):
                config.set_config(workspace=str(broad))
            with self.subTest(env=str(broad)), mock.patch.dict(os.environ, {"NOUL_WORKSPACE": str(broad)}):
                self.assertNotEqual(Path(config.get_config()["workspace"]), broad.resolve())
        with mock.patch("os.getcwd", return_value=str(home.parent)):
            self.assertEqual(Path(config._default_workspace()), home / "Documents" / "Noul")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_the_cache_is_private(self):
        from noul.net import cache

        cache.cache_set("k", {"v": 1})
        mode = stat.S_IMODE(os.stat(ENV["NOUL_DB_PATH"]).st_mode)
        self.assertEqual(mode & 0o077, 0)


class DownloadNameTests(unittest.TestCase):
    def test_odd_authors_do_not_break_the_file_name(self):
        from noul.library import downloads

        for authors in ([""], ["   "], [None], ["X" * 600]):
            with self.subTest(authors=authors):
                name = downloads._safe_filename({"id": "doi:1", "title": "T", "authors": authors, "year": "2" * 50})
                self.assertTrue(name.endswith(".pdf"))
                self.assertLess(len(name), 160)


class InstallerTests(unittest.TestCase):
    def test_a_user_table_for_noul_in_codex_is_left_alone(self):
        from noul import install

        home = Path(_support.tmp_path("codex-home"))
        (home / ".codex").mkdir(parents=True)
        config = home / ".codex/config.toml"
        config.write_text('[mcp_servers.noul]\ncommand = "mine"\n', encoding="utf-8")
        self.assertTrue(install._declares_noul_table_outside_markers(config))
        with mock.patch.object(install, "HOME", home), mock.patch("sys.stdout"):
            self.assertEqual(install.main(["codex", "--runner", "python"]), 1)
        self.assertEqual(config.read_text(encoding="utf-8"), '[mcp_servers.noul]\ncommand = "mine"\n')

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_a_symlinked_config_is_updated_where_it_points(self):
        from noul import install

        home = Path(_support.tmp_path("link-home"))
        home.mkdir(parents=True)
        real = home / "dotfiles-claude.json"
        real.write_text("{}", encoding="utf-8")
        (home / ".claude.json").symlink_to(real)
        with mock.patch.object(install, "HOME", home), mock.patch("sys.stdout"):
            install.main(["claude", "--runner", "python"])
        self.assertTrue((home / ".claude.json").is_symlink())
        self.assertIn("noul", json.loads(real.read_text(encoding="utf-8"))["mcpServers"])


if __name__ == "__main__":
    unittest.main()
