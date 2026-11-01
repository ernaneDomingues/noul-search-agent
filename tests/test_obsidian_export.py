"""Etapa 4 (#06): export_obsidian_notes — atomic notes, YAML frontmatter, wikilinks, BibTeX, workspace confinement."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support

try:
    import yaml
except ImportError:  # optional: a minimal parser is used otherwise
    yaml = None


def setUpModule():
    global c, t, cli, m, WS
    env = _support.isolate_environment()
    WS = Path(env["NOUL_WORKSPACE"]).resolve()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t

    from noul.server import legacy as m  # v1 server tests; SDK server: test_mcp_server.py


def frontmatter(text):
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "missing YAML frontmatter"
    if yaml is not None:
        return yaml.safe_load(match.group(1))
    data, key = {}, None
    for line in match.group(1).splitlines():
        if line.startswith("  - "):
            data[key].append(json.loads(line[4:]))
        else:
            key, _, value = line.partition(": ")
            key = key.rstrip(":")
            data[key] = [] if not value else json.loads(value)
    return data


class ObsidianExportTests(unittest.TestCase):
    def setUp(self):
        self.ids = _support.seed_phase2_papers(c, ["llm_a", "rct", "mice"])
        patcher = mock.patch(_support.core_target("_http_json"), side_effect=_support.no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_notes_frontmatter_wikilinks_and_bibtex(self):
        out = t.call("export_obsidian_notes", {"paper_ids": self.ids, "vault_dir": "vault"})
        self.assertEqual((out["vault_dir"], out["exported_count"]), (str(WS / "vault"), 3))
        names = [Path(n["filepath"]).name for n in out["notes"]]
        self.assertEqual(
            names,
            [
                "2023 - Smith - Efficient 4-bit Quantization for LLM Fine-Tuning.md",
                "2021 - Garcia - Aerobic Exercise and Serum BDNF in Older Adults.md",
                "2024 - Tanaka - Exercise Induces Hippocampal BDNF Expression in Mice.md",
            ],
        )
        self.assertEqual(
            out["notes"][0]["wikilink"], "[[2023 - Smith - Efficient 4-bit Quantization for LLM Fine-Tuning]]"
        )

        text = Path(out["notes"][0]["filepath"]).read_text(encoding="utf-8")
        fm = frontmatter(text)
        self.assertEqual(fm["title"], "Efficient 4-bit Quantization for LLM Fine-Tuning")
        self.assertEqual((fm["paper_id"], fm["doi"], fm["year"]), ("doi:10.1000/qlora", "10.1000/qlora", 2023))
        self.assertEqual(fm["authors"], ["Alice Smith", "Bob Jones", "Carol White"])
        self.assertEqual(
            (fm["venue"], fm["domain"], fm["evidence_level"]), ("NeurIPS", "computer_science", "empirical")
        )
        self.assertEqual(
            (fm["citations"], fm["open_access"], fm["has_code"], fm["has_weights"]), (420, True, True, False)
        )
        self.assertEqual(fm["tags"], ["noul/paper", "domain/computer_science", "evidence/empirical"])

        for heading in (
            "# Efficient 4-bit Quantization",
            "## TL;DR",
            "## Key Findings & Methodology",
            "## Open Artifacts",
            "## Related Notes (Wikilinks)",
            "## BibTeX",
        ):
            self.assertIn(heading, text)
        self.assertIn("- **Code:** <https://github.com/example/qlora> — not verified", text)
        self.assertIn("- [[2021 - Garcia - Aerobic Exercise and Serum BDNF in Older Adults]]", text)
        self.assertIn("- [[2024 - Tanaka - Exercise Induces Hippocampal BDNF Expression in Mice]]", text)
        self.assertNotIn("[[2023 - Smith", text)  # no self-link
        bib = re.search(r"```bibtex\n(@article\{smith2023_1,.*?\n\})\n```", text, re.S)
        self.assertTrue(bib, text)
        self.assertIn("doi = {10.1000/qlora}", bib.group(1))

        mice = Path(out["notes"][2]["filepath"]).read_text(encoding="utf-8")
        self.assertIn("Evasive availability statement", mice)

    def test_filename_sanitized_and_wikilinks_optional(self):
        c.cache_set(
            "paper:oa:W7",
            {"id": "oa:W7", "title": 'What? A/B: "tests" <x> | y*', "authors": [], "abstract": "", "year": None},
        )
        out = t.export_obsidian_notes(["oa:W7", "oa:W7 "], vault_dir="v2", include_related_wikilinks=False)
        self.assertEqual(out["exported_count"], 1)
        name = Path(out["notes"][0]["filepath"]).name
        self.assertEqual(name, "n.d. - Anon - What A B tests x y.md")
        self.assertFalse(set(name) & set('\\/:*?"<>|'))
        text = Path(out["notes"][0]["filepath"]).read_text(encoding="utf-8")
        self.assertNotIn("## Related Notes", text)
        self.assertEqual(frontmatter(text)["year"], None)

    def test_workspace_confinement_and_symlinks(self):
        outside = tempfile.mkdtemp(prefix="noul-outside-")
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        cases = [outside, "../escape"]
        if hasattr(os, "symlink"):
            (WS / "real").mkdir(exist_ok=True)
            os.symlink(outside, WS / "out-link")
            os.symlink(WS / "real", WS / "in-link")
            cases += ["out-link", "in-link/vault"]
        for vault in cases:
            with self.subTest(vault=vault), self.assertRaises(c.HTTPError) as ctx:
                t.export_obsidian_notes(self.ids[:1], vault_dir=vault)
            self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(os.listdir(outside), [])
        if hasattr(os, "symlink"):  # a planted note symlink is never followed
            (WS / "v3").mkdir()
            target = Path(outside) / "victim.md"
            os.symlink(target, WS / "v3" / "2023 - Smith - Efficient 4-bit Quantization for LLM Fine-Tuning.md")
            with self.assertRaises(c.HTTPError) as ctx:
                t.export_obsidian_notes(self.ids[:1], vault_dir="v3")
            self.assertEqual((ctx.exception.code, target.exists()), (403, False))

    def test_is_write_tool_for_http_mcp(self):
        self.assertIn("export_obsidian_notes", m.WRITE_TOOLS)
        res = m.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "export_obsidian_notes",
                    "arguments": {"paper_ids": self.ids, "vault_dir": "vault-http"},
                },
            },
            allow_write=False,
        )
        self.assertTrue(res["result"]["isError"])
        self.assertFalse((WS / "vault-http").exists())

    def _cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_cli_export_and_search_flags(self):
        code, out = self._cli("export", ",".join(self.ids[:2]), "--vault-dir", "./vault-cli")
        self.assertEqual((code, out["exported_count"], out["vault_dir"]), (0, 2, str(WS / "vault-cli")))
        code, out = self._cli("export", self.ids[2], "--format", "obsidian")
        self.assertEqual((code, out["vault_dir"]), (0, str(WS / "obsidian")))

        papers = [dict(_support.PHASE2_PAPERS[k]) for k in ("llm_a", "mice")]
        with mock.patch(_support.core_target("search"), return_value={"papers": papers, "sources": {}, "warnings": []}):
            code, out = self._cli("search", "exercise bdnf", "--export-obsidian", "./vault-search")
        exp = out["obsidian_export"]
        self.assertEqual((code, exp["exported_count"], exp["vault_dir"]), (0, 2, str(WS / "vault-search")))
        self.assertEqual(
            sorted(p.name for p in (WS / "vault-search").iterdir()),
            sorted(Path(n["filepath"]).name for n in exp["notes"]),
        )


if __name__ == "__main__":
    unittest.main()
