"""The workspace, and what tools may touch inside it, stay out of reach of a model steered by text in a paper."""

import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

import _support


def setUpModule():
    _support.isolate_environment()


class Base(unittest.TestCase):
    def setUp(self):
        from noul import config

        self.addCleanup(lambda: config.set_config(clear=True))
        self.workspace = Path(config.get_config()["workspace"])
        self.workspace.mkdir(parents=True, exist_ok=True)


class ToolTests(Base):
    def test_a_tool_call_cannot_set_the_workspace(self):
        from noul import config, registry
        from noul.errors import HTTPError

        for value in ("/proc/self", str(Path.home()), ""):
            with self.assertRaises(HTTPError):
                registry.call("configure_noul", {"workspace": value})
        self.assertNotIn("workspace", registry.input_schema("configure_noul")["properties"])
        self.assertNotIn("workspace", config._load_config_file())

    def test_the_tool_clear_keeps_the_workspace_and_the_cli_clear_does_not(self):
        from noul import cli, config, registry

        config.set_config(workspace=str(self.workspace), research_dir="elsewhere")
        registry.call("configure_noul", {"clear": True})
        self.assertEqual(
            config._load_config_file(),
            {"workspace": str(self.workspace.resolve()), "workspace_confirmed": str(self.workspace.resolve())},
        )
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(["config", "--clear"]), 0)
        self.assertFalse(config._config_file().exists())

    def test_hidden_files_are_refused_to_readers(self):
        from noul import registry
        from noul.errors import HTTPError

        (self.workspace / ".env").write_text("TOKEN=secret-value", encoding="utf-8")
        (self.workspace / ".ssh").mkdir(exist_ok=True)
        (self.workspace / ".ssh" / "notes.md").write_text("secret-value", encoding="utf-8")
        for path in (".env", ".ssh/notes.md", str(self.workspace / ".env")):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                registry.call("recommend_citations", {"draft_text": path})
            self.assertEqual(caught.exception.code, 403)

    def test_readers_refuse_paths_outside_the_workspace_and_symlinks_with_403(self):
        from noul.errors import HTTPError
        from noul.tools import authoring_tools

        outside = _support.tmp_path("outside.md")
        outside.write_text("secret", encoding="utf-8")
        cases = [str(outside)]
        link = self.workspace / "link.md"
        try:
            link.symlink_to(outside)
            cases.append("link.md")
        except (OSError, NotImplementedError):
            pass  # symlinks unavailable (Windows without the privilege)
        for path in cases:
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                authoring_tools._validate_workspace_path(path)
            self.assertEqual(caught.exception.code, 403)

    def test_ordinary_files_still_work(self):
        from noul.tools import authoring_tools

        (self.workspace / "draft.md").write_text("A draft.", encoding="utf-8")
        self.assertEqual(authoring_tools._validate_workspace_path("draft.md").name, "draft.md")

    def test_sanitize_bibtex_writes_only_bib_files(self):
        from noul import registry
        from noul.errors import HTTPError

        bib = "@misc{a, title={T}}"
        target = self.workspace / "notes.txt"
        target.write_text("keep me", encoding="utf-8")
        for name, code in (("notes.txt", 400), (".bashrc", 403), (".hidden.bib", 403), ("x.bib.sh", 400)):
            with self.subTest(name=name), self.assertRaises(HTTPError) as caught:
                registry.call("sanitize_bibtex", {"bibtex_input": bib, "output_path": name})
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
        self.assertFalse((self.workspace / ".bashrc").exists())
        registry.call("sanitize_bibtex", {"bibtex_input": bib, "output_path": "out.BIB"})
        self.assertTrue((self.workspace / "out.BIB").exists())

    def test_output_folders_cannot_be_hidden_or_the_config_file(self):
        from noul import config, registry
        from noul.errors import HTTPError

        args = {"topic": "A topic", "synthesis_markdown": "Text."}
        for folder in (".claude/commands", ".git/hooks"):
            with self.subTest(folder=folder), self.assertRaises(HTTPError) as caught:
                registry.call("save_research_dossier", {**args, "output_dir": folder})
            self.assertEqual(caught.exception.code, 403)
            self.assertFalse((self.workspace / folder).exists())
        home = config._config_file().parent
        with mock.patch.dict(os.environ, {"NOUL_WORKSPACE": str(home)}):
            with self.assertRaises(HTTPError) as caught:
                registry.call("save_research_dossier", {**args, "output_dir": "config.json"})
        self.assertEqual(caught.exception.code, 403)
        self.assertFalse(config._config_file().is_dir())


class StoredWorkspaceTests(Base):
    def test_a_value_saved_by_an_older_version_is_ignored_until_confirmed(self):
        from noul import cli, config

        config._config_file().write_text(json.dumps({"workspace": "/proc"}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"NOUL_WORKSPACE": ""}):
            shown = config.get_config()
            self.assertNotEqual(shown["workspace"], "/proc")
            self.assertEqual(shown["workspace_source"], "default")
            self.assertEqual(shown["workspace_ignored"], "/proc")
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["config", "--workspace", str(self.workspace)]), 0)
            shown = config.get_config()
            self.assertEqual(shown["workspace"], str(self.workspace.resolve()))
            self.assertEqual(shown["workspace_source"], "config")
            self.assertIsNone(shown["workspace_ignored"])
            config.set_config(workspace="")
            self.assertNotIn("workspace_confirmed", config._load_config_file())

    def test_an_older_version_rewriting_a_confirmed_value_loses_the_confirmation(self):
        from noul import config

        config.set_config(workspace=str(self.workspace))
        stored = config._load_config_file()
        stored["workspace"] = "/proc"  # what an older version does: it keeps the keys it does not know
        config._config_file().write_text(json.dumps(stored), encoding="utf-8")
        with mock.patch.dict(os.environ, {"NOUL_WORKSPACE": ""}):
            shown = config.get_config()
        self.assertNotEqual(shown["workspace"], "/proc")
        self.assertEqual(shown["workspace_ignored"], "/proc")

    def test_relocated_databases_are_refused(self):
        from noul.errors import HTTPError
        from noul.tools import authoring_tools

        db = self.workspace / "my.db"
        copy = self.workspace / "my.db.bak"
        for path in (db, copy):
            path.write_text("x", encoding="utf-8")
            self.addCleanup(path.unlink)
        with mock.patch.dict(os.environ, {"NOUL_PROJECTS_DB": str(db)}):
            for name in ("my.db", "my.db.bak", "my.db-wal"):
                with self.subTest(name=name), self.assertRaises(HTTPError) as caught:
                    authoring_tools._validate_workspace_path(name, must_exist=False)
                self.assertEqual(caught.exception.code, 403)

    def test_a_home_folder_cannot_be_found_in_a_container(self):
        from noul import config

        with (
            mock.patch("os.getcwd", return_value=str(self.workspace)),
            mock.patch.object(Path, "home", side_effect=RuntimeError),
        ):
            self.assertEqual(config._default_workspace(), str(self.workspace))

    def test_no_home_folder_at_the_filesystem_root_is_not_a_root_workspace(self):
        from noul import config

        root = Path(self.workspace.anchor)
        with mock.patch("os.getcwd", return_value=str(root)), mock.patch.object(Path, "home", side_effect=RuntimeError):
            self.assertNotEqual(Path(config._default_workspace()), root)

    def test_the_home_folder_is_not_a_default_workspace(self):
        from noul import config

        with mock.patch("os.getcwd", return_value=str(Path.home())):
            self.assertEqual(config._default_workspace(), str(Path.home() / "Documents" / "Noul"))

    def test_noul_home_inside_the_workspace_stays_out_of_reach(self):
        from noul import config, registry
        from noul.errors import HTTPError
        from noul.tools import authoring_tools

        home = self.workspace / "noul-home"
        home.mkdir()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        with mock.patch.dict(os.environ, {"NOUL_HOME": str(home)}):
            for name in (
                "noul-home/projects.db",
                "noul-home/config.json/x.bib",
                "noul-home/x.bib",
            ):
                with self.subTest(name=name), self.assertRaises(HTTPError) as caught:
                    authoring_tools._validate_workspace_path(name, must_exist=False)
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught:
                registry.call(
                    "save_research_dossier",
                    {"topic": "T", "synthesis_markdown": "x", "output_dir": "noul-home/config.json/x"},
                )
            self.assertEqual(caught.exception.code, 403)
            self.assertFalse(config._config_file().exists())

    def test_a_year_cannot_shape_the_download_path(self):
        from noul.library import downloads

        for year in ("../../.bashrc", ".x"):
            name = downloads._safe_filename({"year": year, "title": "T", "id": "i", "authors": ["A B"]})
            self.assertNotIn("/", name)
            self.assertFalse(name.startswith("."))


class ReadOnlyServerTests(Base):
    def test_a_server_without_write_access_reads_no_local_file(self):
        from starlette.testclient import TestClient

        from noul.server.http import build_app

        (self.workspace / "notes.txt").write_text("private-notes", encoding="utf-8")
        cases = (
            ("recommend_citations", {"draft_text": "notes.txt"}),
            ("audit_manuscript_draft", {"draft_input": "notes.txt"}),
            ("sanitize_bibtex", {"bibtex_input": "notes.txt"}),
            ("parse_and_draft_rebuttal", {"reviews_input": "notes.txt"}),
            ("parse_and_draft_rebuttal", {"reviews_input": "Reviewer 2: a long comment.", "draft_input": "notes.txt"}),
            ("audit_submission_readiness", {"draft_path": "notes.txt"}),
        )
        client = TestClient(
            build_app(profile="all", token="t", allowed_hosts=frozenset({"testserver"})),
            base_url="http://testserver",
            headers={"Authorization": "Bearer t"},
        )
        for name, args in cases:
            with self.subTest(name=name, args=args):
                response = client.post(f"/tools/{name}", json=args)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("private-notes", response.text)

    def test_a_server_without_write_access_cannot_choose_the_dossier_folder(self):
        from noul import registry

        for name in ("list_research_dossiers", "get_research_dossier"):
            self.assertTrue(registry.call_reads_file(name, {"slug": "x", "output_dir": "."}))
            self.assertFalse(registry.call_reads_file(name, {"slug": "x"}))

    def test_inline_text_still_works_there(self):
        from noul import registry

        self.assertFalse(
            registry.call_reads_file("recommend_citations", {"draft_text": "A claim that needs a citation here."})
        )
        self.assertTrue(registry.call_reads_file("recommend_citations", {"draft_text": "notes.md"}))
        self.assertFalse(registry.call_reads_file("search_papers", {"query": "notes.md"}))


class CliTests(Base):
    def test_cli_warns_when_the_environment_wins_over_the_workspace(self):
        from noul import cli

        with (
            mock.patch.dict(os.environ, {"NOUL_WORKSPACE": str(self.workspace)}),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()) as err,
        ):
            self.assertEqual(cli.main(["config", "--workspace", str(self.workspace)]), 0)
        self.assertIn("NOUL_WORKSPACE", err.getvalue())

    def test_cli_sets_the_workspace(self):
        from noul import cli, config

        with mock.patch.dict(os.environ, {"NOUL_WORKSPACE": ""}), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(["config", "--workspace", str(self.workspace)]), 0)
            self.assertEqual(config.get_config()["workspace"], str(self.workspace.resolve()))

    def test_cli_clear_refuses_other_settings(self):
        from noul import cli, config

        config.set_config(research_dir="kept")
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main(["config", "--clear", "--email", "a@b.org"]), 1)
        self.assertEqual(config._load_config_file()["research_dir"], "kept")
