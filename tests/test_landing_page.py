"""Unit tests for Phase 6 Issue #28 (GitHub Pages Landing Page, Docs Portal & CI Workflow)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import noul_tools  # noqa: E402


class LandingPageAndDocsPortalTests(unittest.TestCase):
    def test_site_assets_and_workflow_exist(self):
        required_files = [
            ROOT / "site" / "index.html",
            ROOT / "site" / "styles.css",
            ROOT / "site" / "app.js",
            ROOT / "site" / "favicon.svg",
            ROOT / "site" / "og-card.svg",
            ROOT / "site" / "tools-reference.json",
            ROOT / "scripts" / "build_site.py",
            ROOT / ".github" / "workflows" / "deploy-pages.yml",
        ]
        for path in required_files:
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file(), f"Missing required file: {path}")
                self.assertGreater(path.stat().st_size, 100)

    def test_index_html_hero_personas_quickstart_and_seo(self):
        html_text = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        css_text = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
        js_text = (ROOT / "site" / "app.js").read_text(encoding="utf-8")

        # Hero & SEO Open Graph meta tags
        self.assertIn("Noul Search Agent", html_text)
        self.assertIn("Deterministic Multi-Model Search Agent &amp; Autonomous Scientific Research Assistant", html_text)
        self.assertIn('property="og:title"', html_text)
        self.assertIn('property="og:description"', html_text)
        self.assertIn('property="og:image" content="og-card.svg"', html_text)
        self.assertIn('href="favicon.svg"', html_text)

        # Terminal simulator commands
        for cmd in ("noul compare", "noul review", "noul scaffold", "noul gatekeeper audit"):
            self.assertIn(cmd, html_text)
        self.assertIn("TERMINAL_SCENARIOS", js_text)

        # 4 Persona showcase cards
        for keyword in (
            "PyTorch",
            "FP16, INT8 e INT4",
            "CONSORT, STROBE e PRISMA",
            "DiD, IV, RDD",
            "statsmodels",
            "fixest",
            "noul cite",
            "noul related-work",
            "noul bibtex sanitize",
            "noul rebuttal parse",
        ):
            self.assertIn(keyword, html_text)

        # 1-Click setup & MCP configs
        self.assertIn("uvx noul-search-agent", html_text)
        self.assertIn("npx noul-search-agent", html_text)
        self.assertIn("claude_desktop_config.json", html_text)
        self.assertIn(".cursor/mcp.json", html_text)
        self.assertIn("antigravity/mcp_config.json", html_text)
        self.assertIn("The AI Research Journal", html_text)

        # Light/Dark mode support
        self.assertIn('[data-theme="dark"]', css_text)
        self.assertIn('[data-theme="light"]', css_text)
        self.assertIn("theme-toggle", html_text)

    def test_build_site_check_and_tools_catalog_sync(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_site.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        ref = json.loads((ROOT / "site" / "tools-reference.json").read_text(encoding="utf-8"))
        self.assertEqual(ref["tool_count"], len(noul_tools.TOOLS))
        documented_names = {t["name"] for t in ref["tools"]}
        self.assertEqual(documented_names, set(noul_tools.TOOLS.keys()))

        wf_text = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
        self.assertIn("actions/upload-pages-artifact", wf_text)
        self.assertIn("actions/deploy-pages", wf_text)
        self.assertIn("scripts/build_site.py --check", wf_text)


if __name__ == "__main__":
    unittest.main()
