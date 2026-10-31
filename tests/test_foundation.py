"""Etapa 0: Phase 1 data contracts (Pydantic v2) and package foundation."""

import json
import unittest

from _support import ROOT

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


import noul_core
import pydantic

import noul
from noul.schemas.domain import ReviewVerdict, ScientificDomain
from noul.schemas.paper import ArtifactStatus, PaperReference, UnifiedPaper


class SchemaTests(unittest.TestCase):
    def test_enums(self):
        self.assertEqual(
            [d.value for d in ScientificDomain], ["computer_science", "biomedical", "economics", "general_stem"]
        )
        self.assertEqual(ReviewVerdict("borderline"), ReviewVerdict.BORDERLINE)

    def test_unified_paper_defaults_and_nested(self):
        p = UnifiedPaper(
            id="arxiv:1",
            title="T",
            artifacts={"has_code": True, "code_url": "https://github.com/a/b"},
            backward_citations=[{"paper_id": "doi:x", "title": "R", "citation_count": 3}],
        )
        self.assertEqual(p.domain, ScientificDomain.GENERAL_STEM)
        self.assertEqual(p.primary_category, "general")
        self.assertIsInstance(p.artifacts, ArtifactStatus)
        self.assertIsInstance(p.backward_citations[0], PaperReference)
        dumped = p.model_dump(mode="json")
        self.assertEqual(dumped["domain"], "general_stem")
        self.assertEqual(str(dumped["artifacts"]["code_url"]).rstrip("/"), "https://github.com/a/b")
        self.assertEqual(dumped["forward_citations"], [])
        with self.assertRaises(pydantic.ValidationError):
            ArtifactStatus(code_url="not-a-url")
        with self.assertRaises(pydantic.ValidationError):
            PaperReference(paper_id="x", title="t", citation_count=-1)


class PackagingTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_versions_and_manifests_in_sync(self):
        self.assertEqual(noul.__version__, noul_core.__version__)
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyproject["project"]["dynamic"], ["version"])
        setuptools_cfg = pyproject["tool"]["setuptools"]
        self.assertEqual(setuptools_cfg["dynamic"]["version"], {"attr": "noul.__version__"})
        self.assertEqual(setuptools_cfg["packages"]["find"]["where"], ["src"])
        self.assertTrue((ROOT / "src/noul/__init__.py").exists())
        self.assertIn("pydantic>=2.12.0", pyproject["project"]["dependencies"])
        self.assertIn("mcp>=2.3,<3", pyproject["project"]["dependencies"])
        for script, target in pyproject["project"]["scripts"].items():
            self.assertTrue(target.startswith("noul."), (script, target))
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], noul_core.__version__)
        for key in ("name", "displayName", "description", "suggestedPrompts"):
            self.assertTrue(plugin.get(key), key)

    def test_legacy_core_shim_reexports_the_package(self):
        import noul.config
        import noul.errors
        import noul.search.engine

        self.assertIs(noul.config.get_config, noul_core.get_config)
        self.assertIs(noul.search.engine.search, noul_core.search)
        self.assertIs(noul.errors.HTTPError, noul_core.HTTPError)


if __name__ == "__main__":
    unittest.main()
