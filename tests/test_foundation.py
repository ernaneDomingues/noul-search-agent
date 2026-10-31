"""Etapa 0: pydantic compatibility layer, Phase 1 data contracts and package foundation."""

import json
import subprocess
import sys
import unittest
from datetime import date

from _support import ROOT

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


import noul
import noul_core
from noul.schemas import _compat
from noul.schemas._compat import (
    FallbackBaseModel,
    FallbackField,
    FallbackHttpUrl,
    FallbackValidationError,
)
from noul.schemas.domain import ReviewVerdict, ScientificDomain
from noul.schemas.paper import ArtifactStatus, PaperReference, UnifiedPaper


class _Color(str, __import__("enum").Enum):
    RED = "red"


class _Inner(FallbackBaseModel):
    url: FallbackHttpUrl | None = None


class _Model(FallbackBaseModel):
    name: str
    score: int = FallbackField(default=0, ge=0, le=10)
    tags: list[str] = FallbackField(default_factory=list)
    meta: dict[str, int] = FallbackField(default_factory=dict)
    color: _Color = _Color.RED
    inner: _Inner = FallbackField(default_factory=_Inner)
    when: date | None = None


class FallbackBaseModelTests(unittest.TestCase):
    def test_init_defaults_and_coercion(self):
        m = _Model(
            name="x",
            score="7",
            tags=["a"],
            meta={"k": "2"},
            color="red",
            inner={"url": "https://github.com/a/b"},
            when="2024-05-01",
        )
        self.assertEqual((m.score, m.meta, m.color), (7, {"k": 2}, _Color.RED))
        self.assertIsInstance(m.inner, _Inner)
        self.assertEqual(m.when, date(2024, 5, 1))
        self.assertEqual(_Model(name="y").tags, [])
        self.assertIsNot(_Model(name="y").tags, _Model(name="z").tags)

    def test_validation_errors(self):
        for kwargs in (
            {},
            {"name": "x", "score": 11},
            {"name": "x", "score": -1},
            {"name": "x", "color": "blue"},
            {"name": "x", "tags": "nope"},
            {"name": "x", "inner": {"url": "ftp://x"}},
            {"name": 3},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(FallbackValidationError):
                _Model(**kwargs)
        with self.assertRaises(ValueError):
            FallbackHttpUrl("github.com/a/b")
        self.assertEqual(FallbackHttpUrl("http://x.org"), "http://x.org")

    def test_dump_and_validate_roundtrip(self):
        m = _Model.model_validate({"name": "x", "inner": {"url": "https://x.org/a"}, "when": "2024-01-02"})
        dumped = m.model_dump(mode="json")
        self.assertEqual(dumped["color"], "red")
        self.assertEqual(dumped["when"], "2024-01-02")
        self.assertEqual(dumped["inner"], {"url": "https://x.org/a"})
        self.assertEqual(m.dict()["color"], _Color.RED)
        self.assertEqual(json.loads(m.model_dump_json()), dumped)
        self.assertEqual(_Model.model_validate(dumped), m)
        with self.assertRaises(FallbackValidationError):
            _Model.model_validate(["not", "a", "dict"])


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
        with self.assertRaises(_compat.ValidationError):
            ArtifactStatus(code_url="not-a-url")
        with self.assertRaises(_compat.ValidationError):
            PaperReference(paper_id="x", title="t", citation_count=-1)

    def test_schemas_work_without_pydantic(self):
        probe = (
            "import sys; sys.modules['pydantic'] = None\n"
            "from noul.schemas import PYDANTIC_AVAILABLE, UnifiedPaper\n"
            "from noul.schemas._compat import ValidationError\n"
            "p = UnifiedPaper(id='x', title='t', domain='biomedical', artifacts={'has_dataset': True})\n"
            "assert not PYDANTIC_AVAILABLE\n"
            "assert p.model_dump(mode='json')['domain'] == 'biomedical'\n"
            "assert p.artifacts.has_dataset is True\n"
            "try:\n    UnifiedPaper(id='x', title='t', domain='astrology')\nexcept ValidationError:\n    print('ok')\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


class PackagingTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_versions_and_manifests_in_sync(self):
        self.assertEqual(noul.__version__, noul_core.__version__)
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyproject["project"]["version"], noul_core.__version__)
        setuptools_cfg = pyproject["tool"]["setuptools"]
        self.assertIn("noul_core", setuptools_cfg["py-modules"])
        self.assertEqual(
            set(setuptools_cfg["packages"]), {"noul", "noul.schemas", "noul.providers", "noul.tools", "noul.prompts"}
        )
        for pkg in setuptools_cfg["packages"]:
            self.assertTrue((ROOT / pkg.replace(".", "/") / "__init__.py").exists(), pkg)
        self.assertEqual(pyproject["project"]["dependencies"], [])
        self.assertIn("pydantic>=2.7.0", pyproject["project"]["optional-dependencies"]["pro"])
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], noul_core.__version__)
        for key in ("name", "displayName", "description", "suggestedPrompts"):
            self.assertTrue(plugin.get(key), key)

    def test_config_and_core_facades(self):
        import noul.config
        import noul.core

        self.assertIs(noul.config.get_config, noul_core.get_config)
        self.assertIs(noul.core.search, noul_core.search)
        self.assertIs(noul.core.HTTPError, noul_core.HTTPError)


if __name__ == "__main__":
    unittest.main()
