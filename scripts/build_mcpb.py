#!/usr/bin/env python3
"""Build Claude Desktop extensions (.mcpb) for Noul.

Two variants:
  uv      one universal bundle with the Python sources; the host installs Python and the dependencies with uv
          (manifest 0.4 `server.type: uv`). No unsigned binary inside, which matters on macOS.
  binary  one bundle per OS embedding the single-file executable (packaging/pyinstaller); works on hosts
          without uv support.

The manifest (tools, prompts, user_config) is generated from the tool registry so it never drifts.

Usage:
  python scripts/build_mcpb.py manifest --variant uv                 # print the manifest
  python scripts/build_mcpb.py build --variant uv --out dist/noul.mcpb
  python scripts/build_mcpb.py build --variant binary --exe dist/noul --platform linux --out dist/noul-linux-x64.mcpb
Packing needs Node (npx @anthropic-ai/mcpb pack); `--no-pack` only stages the directory.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # dev script: run from a checkout without installing

import noul  # noqa: E402
from noul import registry  # noqa: E402
from noul.server import mcp as mcp_server  # noqa: E402

MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
PLATFORMS = ("darwin", "linux", "win32")
UV_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE")
UV_DIRS = ("src", "skills", "agents", "commands")  # skills/agents/commands are wheel data-files


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]


def manifest(variant, platform=None):
    """MCPB manifest (v0.4) for `variant` ('uv' or 'binary'); `platform` restricts a binary bundle."""
    env = {
        "NOUL_EMAIL": "${user_config.email}",
        "NOUL_WORKSPACE": "${user_config.workspace}",
        "NOUL_TOOL_PROFILE": "${user_config.profile}",
    }
    if variant == "uv":
        server = {
            "type": "uv",
            "entry_point": "src/noul/server/mcp.py",
            # Used by hosts that run mcp_config instead of managing uv themselves (needs uv on PATH).
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--project", "${__dirname}", "--quiet", "--frozen", "noul-mcp"],
                "env": env,
            },
        }
        compatibility = {"platforms": list(PLATFORMS), "runtimes": {"python": ">=3.10"}}
    elif variant == "binary":
        exe = "noul.exe" if platform == "win32" else "noul"
        server = {
            "type": "binary",
            "entry_point": f"server/{exe}",
            "mcp_config": {"command": f"${{__dirname}}/server/{exe}", "args": ["mcp"], "env": env},
        }
        compatibility = {"platforms": [platform] if platform else list(PLATFORMS)}
    else:
        raise ValueError(f"unknown variant {variant!r}")
    catalog = registry.catalog("all")
    return {
        "manifest_version": "0.4",
        "name": "noul",
        "display_name": "Noul Search",
        "version": noul.__version__,
        "description": (
            "Scientific research co-assistant: search the literature (OpenAlex, PubMed, Semantic Scholar, arXiv, "
            "Europe PMC, Crossref), weigh the evidence, read full texts, review papers and support writing."
        ),
        "long_description": (
            "Noul retrieves and structures scientific evidence; Claude judges it. It searches six scholarly "
            "sources, classifies study designs, computes an Evidence Meter from Claude's stance classifications, "
            "downloads open-access PDFs into your Noul folder, saves research dossiers and supports peer review "
            "and manuscript writing. No API keys are needed; a contact e-mail improves full-text coverage."
        ),
        "author": {"name": "Ernane Domingues", "url": "https://github.com/ernaneDomingues"},
        "homepage": "https://ernanedomingues.github.io/noul-search-agent/",
        "repository": {"type": "git", "url": "https://github.com/ernaneDomingues/noul-search-agent"},
        "support": "https://github.com/ernaneDomingues/noul-search-agent/issues",
        "license": "Apache-2.0",
        "keywords": ["research", "science", "literature", "evidence", "pubmed", "arxiv", "papers"],
        "server": server,
        "tools": [{"name": t["name"], "description": _first_sentence(t["description"])} for t in catalog],
        "prompts": [
            {
                "name": name,
                "description": spec["description"],
                "arguments": [a for a, _, _ in spec["arguments"]],
                "text": spec["template"],
            }
            for name, spec in mcp_server.PROMPTS.items()
        ],
        "user_config": {
            "email": {
                "type": "string",
                "title": "Contact e-mail (optional)",
                "description": "Lets Noul find more open-access full texts (Unpaywall) and use the scholarly APIs' "
                "polite pools. Only sent to those APIs.",
                "required": False,
                "default": "",
            },
            "workspace": {
                "type": "directory",
                "title": "Noul folder",
                "description": "Where downloaded PDFs and research dossiers are saved.",
                "required": False,
                "default": "${DOCUMENTS}/Noul",
            },
            "profile": {
                "type": "string",
                "title": "Tool profile",
                "description": "all (every tool), research (search and reading), review or write.",
                "required": False,
                "default": "all",
            },
        },
        "compatibility": compatibility,
        "privacy_policies": [],
    }


def stage(variant, target, exe=None, platform=None):
    target = Path(target)
    if variant == "uv":
        for name in UV_FILES:
            shutil.copy2(ROOT / name, target / name)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
        for name in UV_DIRS:
            shutil.copytree(ROOT / name, target / name, ignore=ignore)
    else:
        server = target / "server"
        server.mkdir()
        dest = server / ("noul.exe" if platform == "win32" else "noul")
        shutil.copy2(exe, dest)
        dest.chmod(0o755)
    shutil.copy2(ROOT / "packaging/mcpb/icon.png", target / "icon.png")  # MCPB icons must be PNG
    data = manifest(variant, platform)
    data["icon"] = "icon.png"
    (target / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _npx():
    return shutil.which("npx.cmd" if os.name == "nt" else "npx") or "npx"


def build(variant, out, exe=None, platform=None, pack=True):
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"noul-mcpb-{variant}-") as tmp:
        staged = stage(variant, tmp, exe=exe, platform=platform)
        subprocess.run([_npx(), "-y", MCPB_CLI, "validate", str(staged / "manifest.json")], check=True)
        if not pack:
            keep = out.with_suffix("")
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(staged, keep)
            return keep
        subprocess.run([_npx(), "-y", MCPB_CLI, "pack", str(staged), str(out)], check=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest", help="Print the generated manifest.json")
    m.add_argument("--variant", choices=("uv", "binary"), default="uv")
    m.add_argument("--platform", choices=PLATFORMS)
    b = sub.add_parser("build", help="Validate and pack a .mcpb bundle")
    b.add_argument("--variant", choices=("uv", "binary"), required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--exe", help="Single-file executable (binary variant)")
    b.add_argument("--platform", choices=PLATFORMS, help="Target platform (binary variant)")
    b.add_argument("--no-pack", action="store_true", help="Only stage and validate (no Node packing)")
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        print(json.dumps(manifest(a.variant, a.platform), indent=2, ensure_ascii=False))
        return 0
    if a.variant == "binary" and not (a.exe and a.platform):
        ap.error("--variant binary needs --exe and --platform")
    print(build(a.variant, a.out, exe=a.exe, platform=a.platform, pack=not a.no_pack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
