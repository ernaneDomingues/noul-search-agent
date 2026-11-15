"""The CLI's global flags and the CLI commands the site advertises for each tool."""

import contextlib
import importlib.util
import io
import json
import unittest

import _support


def setUpModule():
    global api, cli, registry, store
    _support.isolate_environment()
    from noul import api, cli, registry
    from noul.library import store


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class GlobalPrettyTests(unittest.TestCase):
    def test_pretty_works_before_and_after_the_subcommand(self):
        slug = registry.call("project_create", {"title": "Pretty flag"})["slug"]
        code_json, as_json = _run(["project", "show", slug])
        code_after, after = _run(["project", "show", slug, "--pretty"])
        code_before, before = _run(["--pretty", "project", "show", slug])
        self.assertEqual((code_json, code_after, code_before), (0, 0, 0))
        json.loads(as_json)
        self.assertEqual(before, after)
        self.assertNotEqual(before, as_json)


class SlugTests(unittest.TestCase):
    def test_a_dossier_and_its_project_share_one_slug(self):
        for title in ("Café com leite e saúde", "Sleep & memory: a review", "  "):
            with self.subTest(title=title):
                self.assertEqual(api._slugify(title), store.slugify(title or "research", fallback="research-session"))
        self.assertEqual(api._slugify("Café com leite"), "cafe-com-leite")
        self.assertEqual(api._slugify("???"), "research-session")

    def test_a_dossier_saved_under_the_old_slug_is_updated_in_place(self):
        from pathlib import Path

        from noul import config

        research = Path(config.get_config()["workspace"]) / "research"
        old = research / "caf-e-sa-de"  # what versions before 2.1.1 called "Café e saúde"
        old.mkdir(parents=True)
        (old / "papers.json").write_text("{}", encoding="utf-8")
        saved = registry.call("save_research_dossier", {"topic": "Café e saúde", "synthesis_markdown": "v2"})
        self.assertEqual(saved["slug"], "caf-e-sa-de")
        self.assertFalse((research / "cafe-e-saude").exists())
        fresh = registry.call("save_research_dossier", {"topic": "Açaí e sono", "synthesis_markdown": "v1"})
        self.assertEqual(fresh["slug"], "acai-e-sono")


class SiteCliMappingTests(unittest.TestCase):
    def test_every_advertised_command_exists(self):
        spec = importlib.util.spec_from_file_location("build_site", _support.ROOT / "scripts" / "build_site.py")
        build_site = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_site)
        for name in registry.REGISTRY:
            command = build_site.CLI_MAPPING.get(name)
            with self.subTest(tool=name):
                self.assertIsNotNone(command, "add the tool's CLI command to CLI_MAPPING in scripts/build_site.py")
                words = []
                for word in command.split()[1:]:
                    if not word.islower() or word[0] in "<'\"-":
                        break
                    words.append(word)
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
                    with contextlib.redirect_stderr(io.StringIO()):
                        cli.main([*words, "--help"])
                self.assertEqual(caught.exception.code, 0, f"`{command}` is not a CLI command")


if __name__ == "__main__":
    unittest.main()
