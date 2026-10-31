#!/usr/bin/env python3
"""Install the Noul Search skill, subagent, /noul-search-config command and MCP server into coding & desktop agents.
Usage: noul-install [claude] [claude-desktop] [copilot] [codex] [gemini] [antigravity] [--all]
                    [--runner python|uvx|npx] [--dry-run] [--uninstall]
       (from a checkout: python install.py ...)
Everything is user-level (~/.claude, ~/.config/Claude, ~/.copilot, ~/.codex, ~/.gemini). Idempotent; an existing
config file gets a one-time `.bak` copy before it is first modified."""

import argparse
import json
import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Repository root of a source checkout (src/noul/install.py -> repo); wheels ship templates under share/.
REPO = HERE.parent.parent
HOME = Path.home()
PKG = "noul-search-agent"
MARK = ("<!-- noul-search-agent:start -->", "<!-- noul-search-agent:end -->")
TOML_MARK = ("# noul-search-agent:start", "# noul-search-agent:end")
RUNNERS = ("python", "uvx", "npx")
SKILL_REFS = ("__NOUL_DIR__/skills/noul-search/SKILL.md", "${CLAUDE_PLUGIN_ROOT}/skills/noul-search/SKILL.md")
CLI_REFS = (
    "uv run --project ${CLAUDE_PLUGIN_ROOT} noul",
    "python3 __NOUL_DIR__/noul_cli.py",
    "python3 ${CLAUDE_PLUGIN_ROOT}/noul_cli.py",
)

DRY = False
UNINSTALL = False
RUNNER = "python"
ASSETS = REPO
ERRORS: list[str] = []


def log(msg):
    print(("[dry-run] " if DRY else "") + msg)


def fail(msg):
    ERRORS.append(msg)
    print(f"ERROR: {msg}", file=sys.stderr)


def _asset_root():
    """Skill/agent/command templates live next to this file in a checkout, or under <prefix>/share/noul-search-agent
    when installed from a wheel."""
    candidates = [REPO, Path(sys.prefix) / "share" / PKG]
    for getter in (lambda: sysconfig.get_path("data"), lambda: __import__("site").getuserbase()):
        try:
            candidates.append(Path(getter()) / "share" / PKG)
        except Exception:
            pass
    for root in candidates:
        if (root / "skills/noul-search/SKILL.md").is_file():
            return root
    return REPO


def _default_runner():
    env = os.environ.get("NOUL_INSTALL_RUNNER", "").strip().lower()
    if env in RUNNERS:
        return env
    # `uvx` runs from a throw-away cache environment; agents must not point at that interpreter.
    if "archive-v" in Path(sys.prefix).as_posix():
        return "uvx"
    return "python"


def _q(s):
    s = str(s)
    return f'"{s}"' if any(ch.isspace() for ch in s) else s


def mcp_command():
    """(command, args) agents use to start the MCP server over stdio."""
    if RUNNER == "uvx":
        return "uvx", [PKG, "mcp"]
    if RUNNER == "npx":
        if os.name == "nt":
            return "cmd", ["/c", "npx", "-y", PKG, "mcp"]
        return "npx", ["-y", PKG, "mcp"]
    script = _console_script("noul-mcp")
    if script:
        return str(script), []
    return sys.executable, [str(REPO / "noul_mcp.py")]


def cli_command():
    if RUNNER == "uvx":
        return f"uvx {PKG}"
    if RUNNER == "npx":
        return f"npx -y {PKG}"
    script = _console_script("noul")
    if script:
        return _q(script)
    return f"{_q(sys.executable)} {_q(REPO / 'noul_cli.py')}"


def _console_script(name):
    """Absolute path of an installed console script next to this interpreter, or None.

    Agents start stdio MCP servers inside the user's project folder. `python -m noul` would put that folder
    first on sys.path, so a cloned repository containing `noul/` or a stdlib-named file (`json.py`, ...) could
    run its own code; a console script (or an absolute script path) keeps sys.path[0] on the install folder."""
    exe = name + (".exe" if os.name == "nt" else "")
    dirs = [Path(sys.executable).parent]
    for scheme in (None, f"{os.name}_user"):
        try:
            dirs.append(Path(sysconfig.get_path("scripts", scheme) if scheme else sysconfig.get_path("scripts")))
        except (KeyError, TypeError):
            pass
    for folder in dirs:
        candidate = folder / exe
        if candidate.is_file():
            return candidate.resolve()
    return None


def render(path, skill_path=None):
    text = path.read_text(encoding="utf-8")
    if skill_path:
        for ref in SKILL_REFS:
            text = text.replace(ref, str(skill_path))
    for ref in CLI_REFS:
        text = text.replace(ref, cli_command())
    root = str(ASSETS)
    return text.replace("__NOUL_DIR__", root).replace("${CLAUDE_PLUGIN_ROOT}", root)


def backup(path):
    bak = path.with_name(path.name + ".bak")
    if path.is_file() and not bak.exists():
        log(f"backup {path} -> {bak.name}")
        if not DRY:
            shutil.copy2(path, bak)


def write(dst, text, keep_backup=False):
    if dst.is_file() and dst.read_text(encoding="utf-8", errors="replace") == text:
        return log(f"unchanged {dst}")
    if keep_backup:
        backup(dst)
    log(f"write {dst}")
    if not DRY:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")


def remove(p):
    if p.exists():
        log(f"remove {p}")
        if not DRY:
            shutil.rmtree(p) if p.is_dir() else p.unlink()


def install_skill(root):
    dst = root / "noul-search"
    if UNINSTALL:
        return remove(dst)
    write(dst / "SKILL.md", render(ASSETS / "skills/noul-search/SKILL.md"))


def install_agent(dst, src):
    remove(dst) if UNINSTALL else write(dst, render(ASSETS / "agents" / src))


def install_command(dst, src):
    remove(dst) if UNINSTALL else write(dst, render(ASSETS / "commands" / src))


def patch_block(path, body, begin, end):
    """Insert/replace/remove a marked block in a text file."""
    if UNINSTALL and not path.exists():
        return
    try:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
    except UnicodeDecodeError as e:
        return fail(f"{path} is not valid UTF-8 ({e}); left untouched.")
    text = re.sub(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", original, flags=re.S).rstrip("\n")
    user_content = bool(text.strip())
    if not UNINSTALL:
        text += f"\n\n{begin}\n{body.strip()}\n{end}\n"
    elif user_content:
        text += "\n"
    if UNINSTALL and not user_content:
        return remove(path)
    write(path, text.lstrip("\n"), keep_backup=True)


def json_mcp(path, entry):
    if not path.exists():
        if UNINSTALL:
            return
        cfg = {}
    else:
        try:
            raw = path.read_text(encoding="utf-8")
            cfg = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return fail(f"{path} is not valid JSON ({e}). It was left untouched; fix it and re-run.")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("mcpServers", {}), dict):
        return fail(f"{path} does not contain a JSON object with an 'mcpServers' object; left untouched.")
    servers = cfg.setdefault("mcpServers", {})
    if UNINSTALL:
        if "noul" not in servers:
            return
        servers.pop("noul")
    else:
        servers["noul"] = entry
    write(path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", keep_backup=True)


def _toml_str(s):
    """TOML literal string ('...': no escapes, safe for Windows paths) or, if impossible, a basic string."""
    s = str(s)
    if "'" not in s and "\n" not in s and "\r" not in s:
        return f"'{s}'"
    return json.dumps(s)


def claude():
    install_skill(HOME / ".claude/skills")
    install_agent(HOME / ".claude/agents/noul-researcher.md", "claude-noul-researcher.md")
    install_command(HOME / ".claude/commands/noul-search-config.md", "noul-search-config.md")
    cmd, args = mcp_command()
    json_mcp(HOME / ".claude.json", {"type": "stdio", "command": cmd, "args": args, "env": {}})


def claude_desktop():
    if sys.platform == "darwin":
        cfg_path = HOME / "Library/Application Support/Claude/claude_desktop_config.json"
    elif os.name == "nt":
        appdata = Path(os.environ.get("APPDATA") or (HOME / "AppData/Roaming"))
        cfg_path = appdata / "Claude/claude_desktop_config.json"
    else:
        cfg_path = HOME / ".config/Claude/claude_desktop_config.json"
    cmd, args = mcp_command()
    json_mcp(cfg_path, {"command": cmd, "args": args})


def copilot():
    install_skill(HOME / ".copilot/skills")
    install_agent(HOME / ".copilot/agents/noul-researcher.agent.md", "copilot-noul-researcher.agent.md")
    cmd, args = mcp_command()
    json_mcp(HOME / ".copilot/mcp-config.json", {"type": "local", "command": cmd, "args": args, "tools": ["*"]})


def codex():
    install_skill(HOME / ".codex/skills")
    snippet = render(ASSETS / "agents/AGENTS.snippet.md", HOME / ".codex/skills/noul-search/SKILL.md")
    patch_block(HOME / ".codex/AGENTS.md", snippet, *MARK)
    cmd, args = mcp_command()
    toml = f"[mcp_servers.noul]\ncommand = {_toml_str(cmd)}\nargs = [{', '.join(_toml_str(a) for a in args)}]"
    patch_block(HOME / ".codex/config.toml", toml, *TOML_MARK)


def gemini():
    ext = HOME / ".gemini/extensions/noul"
    if UNINSTALL:
        return remove(ext)
    manifest = json.loads((ASSETS / "agents/gemini-extension.json").read_text(encoding="utf-8"))
    cmd, args = mcp_command()
    manifest["mcpServers"] = {"noul": {"command": cmd, "args": args}}
    write(ext / "gemini-extension.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    write(ext / "GEMINI.md", render(ASSETS / "agents/AGENTS.snippet.md", ext / "skills/noul-search/SKILL.md"))
    install_skill(ext / "skills")


def antigravity():
    install_skill(HOME / ".gemini/config/skills")
    cmd, args = mcp_command()
    json_mcp(HOME / ".gemini/antigravity/mcp_config.json", {"command": cmd, "args": args, "env": {}})


TARGETS = {
    "claude": claude,
    "claude-desktop": claude_desktop,
    "copilot": copilot,
    "codex": codex,
    "gemini": gemini,
    "antigravity": antigravity,
}


def main(argv=None):
    global DRY, UNINSTALL, RUNNER, ASSETS
    ap = argparse.ArgumentParser(prog="noul-install", description="Install Noul Search into coding & desktop agents.")
    ap.add_argument("targets", nargs="*", metavar="TARGET", help=f"Any of: {', '.join(TARGETS)} (default: all)")
    ap.add_argument("--all", action="store_true", help="Install into every supported agent")
    ap.add_argument(
        "--runner",
        choices=RUNNERS,
        help="How agents start the MCP server: this Python interpreter (default), `uvx` or `npx` "
        "(default from $NOUL_INSTALL_RUNNER)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Only print what would change")
    ap.add_argument("--uninstall", action="store_true", help="Remove everything installed by Noul")
    a = ap.parse_args(argv)
    bad = [x for x in a.targets if x not in TARGETS]
    if bad:
        ap.error(f"unknown target(s): {', '.join(bad)}. Choose from: {', '.join(TARGETS)}")

    DRY, UNINSTALL = a.dry_run, a.uninstall
    RUNNER = a.runner or _default_runner()
    ASSETS = _asset_root()
    ERRORS.clear()
    if not (ASSETS / "skills/noul-search/SKILL.md").is_file():
        print(
            f"ERROR: Noul skill/agent templates not found (looked in {REPO} and {sys.prefix}/share/{PKG}).",
            file=sys.stderr,
        )
        return 1

    if not UNINSTALL:
        cmd, args = mcp_command()
        print(f"MCP server command ({RUNNER}): {' '.join(_q(x) for x in [cmd, *args])}")
    for name in list(TARGETS) if a.all or not a.targets else a.targets:
        print(f"== {name}")
        try:
            TARGETS[name]()
        except OSError as e:
            fail(f"{name}: {e}")

    if ERRORS:
        print(f"\nFinished with {len(ERRORS)} error(s); the affected files were not modified.", file=sys.stderr)
        return 1
    if not UNINSTALL and not DRY:
        print(
            "\nDone. Next: set a contact email so Unpaywall / polite API pools can be used (optional):\n"
            "  - Claude Code: /noul-search-config email:you@university.edu\n"
            f"  - Any terminal: {cli_command()} config --email you@university.edu   (or export NOUL_EMAIL)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
