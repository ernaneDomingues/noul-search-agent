"""The CLI's global flags and the CLI commands the site advertises for each tool."""

import contextlib
import importlib.util
import io
import json
import unittest

import _support


def setUpModule():
    global cli, dossiers, registry, store
    _support.isolate_environment()
    from noul import cli, registry
    from noul.api import dossiers
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
                self.assertEqual(
                    dossiers._slugify(title), store.slugify(title or "research", fallback="research-session")
                )
        self.assertEqual(dossiers._slugify("Café com leite"), "cafe-com-leite")
        self.assertEqual(dossiers._slugify("???"), "research-session")

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
                with (
                    self.assertRaises(SystemExit) as caught,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    cli.main([*words, "--help"])
                self.assertEqual(caught.exception.code, 0, f"`{command}` is not a CLI command")


def _build_site():
    spec = importlib.util.spec_from_file_location("build_site", _support.ROOT / "scripts" / "build_site.py")
    build_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_site)
    return build_site


class GeneratedContentTests(unittest.TestCase):
    def test_everything_generated_is_up_to_date(self):
        build_site = _build_site()
        docs = build_site.render_docs(build_site.build_tools_payload())
        stale = [
            path.relative_to(_support.ROOT).as_posix()
            for path, (current, expected) in docs.items()
            if current != expected
        ]
        self.assertEqual(stale, [], "run `python scripts/build_site.py`")

    def test_manifests_share_one_description_and_the_package_version(self):
        import json

        import noul

        build_site = _build_site()
        description = build_site.DESCRIPTION.format(tool_count=len(registry.REGISTRY))
        for path in build_site.MANIFESTS:
            with self.subTest(manifest=path.name):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((manifest["version"], manifest["description"]), (noul.__version__, description))

    def test_the_project_workflow_is_the_servers_paragraph(self):
        from noul.server import common, mcp

        build_site = _build_site()
        rendered = build_site._project_workflow(build_site.build_tools_payload())
        self.assertEqual(rendered.replace("`", ""), common.PROJECT_WORKFLOW)
        self.assertIn("`search_papers(project=slug)`", rendered)
        self.assertIn(common.PROJECT_WORKFLOW, mcp.INSTRUCTIONS)
        for name in ("agents/claude-noul-researcher.md", "skills/noul-search/references/search.md"):
            self.assertIn(rendered, (_support.ROOT / name).read_text(encoding="utf-8"), name)


class HandWrittenNumbersTests(unittest.TestCase):
    """Counts and the version that prose repeats by hand must follow the registry and `noul.__version__`."""

    def test_site_badge_shows_the_package_version(self):
        import noul

        html = (_support.ROOT / "site/index.html").read_text(encoding="utf-8")
        self.assertIn(f'<span class="badge">v{noul.__version__}</span>', html)

    def test_tool_counts_in_the_docs_match_the_registry(self):
        from noul.server import common

        total = len(registry.REGISTRY)
        research = len(registry.catalog("research"))
        public = len(common.visible_tools("research", allow_write=False, public=True))
        expected = {
            "README.md": [
                f"{total} typed tools",
                f"all {total})",
                f"the {total} tool functions",
                f"downloads: {research} tools)",
                f"{public} tools from the `research` profile",
            ],
            "docs/deploy/cloud-run.md": [f"{public} tools from the `research` profile"],
            "CLAUDE.md": [f"the {total} tool functions"],
            "src/noul/api/__init__.py": [f"the {total} tool functions"],
        }
        for name, phrases in expected.items():
            text = (_support.ROOT / name).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(file=name, phrase=phrase):
                    self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
