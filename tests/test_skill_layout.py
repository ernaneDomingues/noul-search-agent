"""Phase 3c-1: the layered noul-search skill (router + one reference per journey) and its eval cases."""

import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support

SKILL = _support.ROOT / "skills" / "noul-search"
EVALS = SKILL / "evals"
JOURNEYS = ("search", "review", "writing", "submission")
PROMPT_KEYS = {"tags", "max_turns", "timeout_seconds", "allowed_tools"}


def setUpModule():
    _support.isolate_environment()


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return None, text
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line), m.group(2)


class SkillLayoutTests(unittest.TestCase):
    def test_router_links_every_journey_reference(self):
        router = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        meta, _ = _frontmatter(router)
        self.assertEqual(meta["name"].strip(), "noul-search")
        self.assertLessEqual(len(meta["description"].strip()), 1024)
        links = re.findall(r"\]\((references/[a-z]+\.md)\)", router)
        self.assertEqual(sorted(Path(x).stem for x in links), sorted(JOURNEYS))
        for link in links:
            self.assertTrue((SKILL / link).is_file(), link)
        self.assertLess(len(router.splitlines()), 120)  # a router, not the whole manual

    def test_the_skill_only_names_real_tools(self):
        from noul import registry

        names = set(registry.TOOLS)
        for path in [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]:
            for called in set(re.findall(r"`([a-z][a-z_]{3,})\(", path.read_text(encoding="utf-8"))):
                with self.subTest(file=path.name, tool=called):
                    self.assertIn(called, names)

    def test_installer_copies_the_references(self):
        from noul import install

        home = _support.tmp_path("home")
        home.mkdir()
        with mock.patch.object(install, "HOME", home), redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["claude"]), 0)
        installed = home / ".claude/skills/noul-search"
        self.assertTrue((installed / "SKILL.md").is_file())
        self.assertEqual(sorted(p.stem for p in (installed / "references").glob("*.md")), sorted(JOURNEYS))


class EvalCaseTests(unittest.TestCase):
    def test_twelve_cases_three_per_journey(self):
        cases = sorted(p for p in EVALS.iterdir() if p.is_dir())
        self.assertEqual(len(cases), 12)
        per_journey = {j: [c for c in cases if c.name.startswith(j + "-")] for j in JOURNEYS}
        self.assertEqual({j: len(v) for j, v in per_journey.items()}, dict.fromkeys(JOURNEYS, 3))

    def test_each_case_loads(self):
        for case in sorted(p for p in EVALS.iterdir() if p.is_dir()):
            with self.subTest(case=case.name):
                self.assertRegex(case.name, r"^[a-z][a-z0-9-]*$")
                meta, prompt = _frontmatter((case / "prompt.md").read_text(encoding="utf-8"))
                self.assertIsNotNone(meta)
                self.assertLessEqual(set(meta), PROMPT_KEYS)
                self.assertTrue(prompt.strip())
                yaml = (case / "case.yaml").read_text(encoding="utf-8")
                self.assertIn('schema_version: "1.1"', yaml)
                self.assertIn(f"name: {case.name}\n", yaml)
                self.assertIn("expected_outcome: |", yaml)
                graders = sorted((case / "graders").glob("*.md"))
                self.assertGreaterEqual(len(graders), 3)
                for g in graders:
                    head, body = _frontmatter(g.read_text(encoding="utf-8"))
                    self.assertEqual(
                        {k: v.strip() for k, v in head.items()}, {"type": "llm", "focus": "trace", "weight": "1"}
                    )
                    self.assertTrue(body.strip())
                if (case / "files").is_dir():
                    self.assertIn("scaffold_script: place-files.sh", yaml)
                    self.assertTrue((case / "place-files.sh").is_file())
                    named = re.search(r"Input files: (.+)$", prompt.strip(), re.M).group(1).split(", ")
                    self.assertEqual(sorted(named), sorted(p.name for p in (case / "files").iterdir()))


if __name__ == "__main__":
    unittest.main()
