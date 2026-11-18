#!/usr/bin/env python3
"""Install the Noul Search skill, subagent, /noul-search-config command and MCP server into coding & desktop agents.
Usage: noul-install [claude] [claude-desktop] [copilot] [codex] [gemini] [antigravity] [--all]
                    [--runner python|uvx] [--dry-run] [--uninstall]
       (from a checkout: uv run noul-install ...)
Everything is user-level (~/.claude, ~/.config/Claude, ~/.copilot, ~/.codex, ~/.gemini). Idempotent; an existing
config file gets a one-time `.bak` copy before it is first modified."""

import argparse
import json
import os
import re
import shutil
import sys
import sysconfig
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# Repository root of a source checkout (src/noul/install.py -> repo); wheels ship templates under share/.
REPO = HERE.parent.parent
# Running from the single-file executable (PyInstaller): templates are bundled under sys._MEIPASS and the
# executable itself is both the CLI and the MCP server (`noul mcp`).
FROZEN = bool(getattr(sys, "frozen", False))
HOME = Path.home()
PKG = "noul-search-agent"
MARK = ("<!-- noul-search-agent:start -->", "<!-- noul-search-agent:end -->")
TOML_MARK = ("# noul-search-agent:start", "# noul-search-agent:end")
RUNNERS = ("python", "uvx")
SKILL_REFS = ("__NOUL_DIR__/skills/noul-search/SKILL.md", "${CLAUDE_PLUGIN_ROOT}/skills/noul-search/SKILL.md")
CLI_REFS = ("uv run --project ${CLAUDE_PLUGIN_ROOT} noul",)

# Absolute-path launchers for when no console script is installed (see noul._bin).
_LAUNCHERS = Path(__file__).resolve().parent / "_bin"

APP_FILES = ("skills", "agents", "commands")


def _asset_root():
    """Skill/agent/command templates live next to this file in a checkout, or under <prefix>/share/noul-search-agent
    when installed from a wheel."""
    candidates = ([Path(getattr(sys, "_MEIPASS", REPO))] if FROZEN else []) + [REPO, Path(sys.prefix) / "share" / PKG]
    for getter in (lambda: sysconfig.get_path("data"), lambda: __import__("site").getuserbase()):
        try:
            candidates.append(Path(getter()) / "share" / PKG)
        except Exception:  # noqa: BLE001 - probing interpreter layouts (frozen, venv, user site): any failure means 'not here'
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


def mcp_command(runner="python"):
    """(command, args) agents use to start the MCP server over stdio."""
    if runner == "uvx":
        return "uvx", [PKG, "mcp"]
    if FROZEN:
        return str(frozen_target()), ["mcp"]
    script = _console_script("noul-mcp")
    if script:
        return str(script), []
    return sys.executable, [str(_LAUNCHERS / "noul_mcp_launcher.py")]


def cli_command(runner="python"):
    if runner == "uvx":
        return f"uvx {PKG}"
    if FROZEN:
        return _q(frozen_target())
    script = _console_script("noul")
    if script:
        return _q(script)
    return f"{_q(sys.executable)} {_q(_LAUNCHERS / 'noul_cli_launcher.py')}"


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


def app_dir():
    """Stable per-user folder for the frozen executable and its templates (never Downloads or a temp folder)."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData/Local") / "Programs"
        return base / "Noul"
    if sys.platform == "darwin":
        return HOME / "Library/Application Support/Noul"
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "noul"


def frozen_target():
    """Where agents run the frozen executable from: a copy in app_dir(), so moving or deleting the downloaded file
    (often in Downloads) does not break every agent configuration."""
    return app_dir() / ("noul.exe" if os.name == "nt" else "noul")


def _atomic_copy(src, dst):
    fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", dir=dst.parent)
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _atomic_write(dst, text):
    """Write via a temp file in the same folder + os.replace: an interrupted install never leaves a half-written
    agent configuration. Keeps the existing file's permissions."""
    fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if dst.exists():
            shutil.copymode(dst, tmp)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _toml_str(s):
    """TOML literal string ('...': no escapes, safe for Windows paths) or, if impossible, a basic string."""
    s = str(s)
    if "'" not in s and "\n" not in s and "\r" not in s:
        return f"'{s}'"
    return json.dumps(s, ensure_ascii=False)  # \ud83d-style surrogate escapes are not valid TOML


def _app_config_dir(app):
    """Per-user config folder of a desktop app (Claude, Code) on this OS."""
    if sys.platform == "darwin":
        return HOME / "Library/Application Support" / app
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or (HOME / "AppData/Roaming")) / app
    return HOME / ".config" / app


@dataclass
class Install:
    """One installer run: its options, where its templates come from, and the errors it met. Every file
    operation goes through it, so `dry` and `uninstall` are honoured in one place."""

    dry: bool = False
    uninstall: bool = False
    runner: str = "python"
    assets: Path = REPO
    # Folder written into rendered templates (`__NOUL_DIR__`); differs from `assets` for the frozen executable,
    # whose bundle folder (sys._MEIPASS) is a temporary directory deleted when the process exits.
    install_root: Path | None = None
    errors: list[str] = field(default_factory=list)

    def log(self, msg):
        print(("[dry-run] " if self.dry else "") + msg)

    def fail(self, msg):
        self.errors.append(msg)
        print(f"ERROR: {msg}", file=sys.stderr)

    def mcp_command(self):
        return mcp_command(self.runner)

    def cli_command(self):
        return cli_command(self.runner)

    def deploy_frozen(self):
        """Copy the running executable and its templates to app_dir(); returns that folder (also in dry-run)."""
        root, target = app_dir(), frozen_target()
        source = Path(sys.executable).resolve()
        if source != target.resolve():
            self.log(f"copy {source} -> {target}")
            if not self.dry:
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_copy(source, target)
                target.chmod(0o755)
        for name in APP_FILES:
            src = self.assets / name
            if src.is_dir() and src.resolve() != (root / name).resolve():
                self.log(f"copy {src.name}/ -> {root / name}")
                if not self.dry:
                    shutil.rmtree(root / name, ignore_errors=True)
                    shutil.copytree(src, root / name)
        return root

    def render(self, path, skill_path=None):
        text = path.read_text(encoding="utf-8")
        if skill_path:
            for ref in SKILL_REFS:
                text = text.replace(ref, str(skill_path))
        for ref in CLI_REFS:
            text = text.replace(ref, self.cli_command())
        root = str(self.install_root or self.assets)
        return text.replace("__NOUL_DIR__", root).replace("${CLAUDE_PLUGIN_ROOT}", root)

    def backup(self, path):
        bak = path.with_name(path.name + ".bak")
        if path.is_file() and not bak.exists():
            self.log(f"backup {path} -> {bak.name}")
            if not self.dry:
                shutil.copy2(path, bak)

    def write(self, dst, text, keep_backup=False):
        # A config file that is a symlink (dotfiles managed elsewhere) is updated where it points, not replaced.
        dst = Path(os.path.realpath(dst))
        if dst.is_file() and dst.read_text(encoding="utf-8", errors="replace") == text:
            return self.log(f"unchanged {dst}")
        if keep_backup:
            self.backup(dst)
        self.log(f"write {dst}")
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(dst, text)

    def remove(self, p):
        if p.exists():
            self.log(f"remove {p}")
            if not self.dry:
                shutil.rmtree(p) if p.is_dir() else p.unlink()

    def skill(self, root):
        dst = root / "noul-search"
        if self.uninstall:
            return self.remove(dst)
        self.write(dst / "SKILL.md", self.render(self.assets / "skills/noul-search/SKILL.md"))
        # The skill is layered: SKILL.md routes to one reference per journey (search, review, writing, submission).
        for ref in sorted((self.assets / "skills/noul-search/references").glob("*.md")):
            self.write(dst / "references" / ref.name, self.render(ref))

    def template(self, dst, folder, src):
        """Install (or remove) one rendered template of `folder` (`agents`, `commands`) at `dst`."""
        self.remove(dst) if self.uninstall else self.write(dst, self.render(self.assets / folder / src))

    def patch_block(self, path, body, begin, end):
        """Insert/replace/remove a marked block in a text file."""
        if self.uninstall and not path.exists():
            return
        try:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
        except UnicodeDecodeError as e:
            return self.fail(f"{path} is not valid UTF-8 ({e}); left untouched.")
        text = re.sub(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", original, flags=re.S).rstrip("\n")
        user_content = bool(text.strip())
        if not self.uninstall:
            text += f"\n\n{begin}\n{body.strip()}\n{end}\n"
        elif user_content:
            text += "\n"
        if self.uninstall and not user_content:
            return self.remove(path)
        self.write(path, text.lstrip("\n"), keep_backup=True)

    def json_mcp(self, path, entry=None, key="mcpServers"):
        """Register (or remove) the `noul` server in a JSON config. `entry` holds the fields the client wants
        besides `command` and `args`, which come from the run."""
        if not path.exists():
            if self.uninstall:
                return
            cfg: dict[Any, Any] = {}
        else:
            try:
                raw = path.read_text(encoding="utf-8")
                cfg = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return self.fail(f"{path} is not valid JSON ({e}). It was left untouched; fix it and re-run.")
        if not isinstance(cfg, dict) or not isinstance(cfg.get(key, {}), dict):
            return self.fail(f"{path} does not contain a JSON object with an '{key}' object; left untouched.")
        servers = cfg.setdefault(key, {})
        if self.uninstall:
            if "noul" not in servers:
                return
            servers.pop("noul")
        else:
            servers["noul"] = self.server_entry(**(entry or {}))
        self.write(path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", keep_backup=True)

    def server_entry(self, type=None, **extra):
        """The `noul` server as a JSON config entry: optional `type` first, then command and args, then `extra`."""
        cmd, args = self.mcp_command()
        return {**({"type": type} if type else {}), "command": cmd, "args": args, **extra}


# -----------------------------------------------------------------------------------------------------------------
# Clients: what installing Noul means for each app


def _claude(run):
    run.skill(HOME / ".claude/skills")
    run.template(HOME / ".claude/agents/noul-researcher.md", "agents", "claude-noul-researcher.md")
    run.template(HOME / ".claude/commands/noul-search-config.md", "commands", "noul-search-config.md")
    run.json_mcp(HOME / ".claude.json", {"type": "stdio", "env": {}})


def _claude_desktop(run):
    run.json_mcp(_app_config_dir("Claude") / "claude_desktop_config.json")


def _vscode(run):
    """VS Code user-level MCP servers (`servers` key in User/mcp.json, used by Copilot agent mode)."""
    run.json_mcp(_app_config_dir("Code") / "User/mcp.json", {"type": "stdio"}, key="servers")


def _cursor(run):
    run.json_mcp(HOME / ".cursor/mcp.json")


def _copilot(run):
    run.skill(HOME / ".copilot/skills")
    run.template(HOME / ".copilot/agents/noul-researcher.agent.md", "agents", "copilot-noul-researcher.agent.md")
    run.json_mcp(HOME / ".copilot/mcp-config.json", {"type": "local", "tools": ["*"]})


def _codex(run):
    run.skill(HOME / ".codex/skills")
    snippet = run.render(run.assets / "agents/AGENTS.snippet.md", HOME / ".codex/skills/noul-search/SKILL.md")
    run.patch_block(HOME / ".codex/AGENTS.md", snippet, *MARK)
    config = HOME / ".codex/config.toml"
    if not run.uninstall and _declares_noul_table_outside_markers(config):
        return run.fail(f"{config} already declares [mcp_servers.noul] outside Noul's block; left untouched.")
    cmd, args = run.mcp_command()
    toml = f"[mcp_servers.noul]\ncommand = {_toml_str(cmd)}\nargs = [{', '.join(_toml_str(a) for a in args)}]"
    run.patch_block(HOME / ".codex/config.toml", toml, *TOML_MARK)


def _declares_noul_table_outside_markers(path):
    """True when the user's Codex config already has its own [mcp_servers.noul] table (a second one is invalid TOML
    and would break Codex)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    outside = re.sub(re.escape(TOML_MARK[0]) + r".*?" + re.escape(TOML_MARK[1]), "", text, flags=re.S)
    return re.search(r"^\s*\[\s*mcp_servers\.noul\s*\]", outside, re.M) is not None


def _gemini(run):
    ext = HOME / ".gemini/extensions/noul"
    if run.uninstall:
        return run.remove(ext)
    manifest = json.loads((run.assets / "agents/gemini-extension.json").read_text(encoding="utf-8"))
    manifest["mcpServers"] = {"noul": run.server_entry()}
    run.write(ext / "gemini-extension.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    run.write(
        ext / "GEMINI.md", run.render(run.assets / "agents/AGENTS.snippet.md", ext / "skills/noul-search/SKILL.md")
    )
    run.skill(ext / "skills")


def _antigravity(run):
    run.skill(HOME / ".gemini/config/skills")
    run.json_mcp(HOME / ".gemini/antigravity/mcp_config.json", {"env": {}})


@dataclass(frozen=True)
class Client:
    """An app Noul installs into: how to (un)install there and how to tell that the app is present."""

    name: str
    label: str
    install: Callable[[Install], None]
    hints: Callable[[], list[Any]]  # paths (or `shutil.which` results) whose existence means the app is installed


CLIENTS = (
    Client("claude", "Claude Code", _claude, lambda: [HOME / ".claude", shutil.which("claude")]),
    Client("claude-desktop", "Claude Desktop", _claude_desktop, lambda: [_app_config_dir("Claude")]),
    Client("vscode", "VS Code (Copilot agent mode)", _vscode, lambda: [_app_config_dir("Code"), shutil.which("code")]),
    Client("cursor", "Cursor", _cursor, lambda: [HOME / ".cursor", shutil.which("cursor")]),
    Client("copilot", "GitHub Copilot CLI", _copilot, lambda: [HOME / ".copilot"]),
    Client("codex", "OpenAI Codex CLI", _codex, lambda: [HOME / ".codex", shutil.which("codex")]),
    Client("gemini", "Gemini CLI", _gemini, lambda: [HOME / ".gemini", shutil.which("gemini")]),
    Client("antigravity", "Antigravity", _antigravity, lambda: [HOME / ".gemini/antigravity"]),
)
TARGETS = {client.name: client for client in CLIENTS}


def detect_targets():
    """Targets whose app seems installed (its config folder or command exists)."""
    return [c.name for c in CLIENTS if any(h and Path(h).exists() for h in c.hints())]


def choose_targets(ask=input, attempts=3):
    """Interactive picker: lists the apps (marking detected ones) and returns the chosen target names ([] = cancel).
    Enter picks the detected apps; with none detected, or after an invalid answer, it asks again."""
    detected = detect_targets()
    names = list(TARGETS)
    print("Where should Noul be installed?\n")
    for i, name in enumerate(names, 1):
        mark = "  (detected)" if name in detected else ""
        print(f"  {i}. {TARGETS[name].label}{mark}")
    default = ",".join(str(names.index(n) + 1) for n in detected)
    hint = f"Enter for [{default}]" if default else "'q' to quit"
    for _ in range(attempts):
        answer = ask(f"\nComma-separated numbers, 'all' or {hint}: ").strip() or default
        if answer.lower() in ("q", "quit", "sair", "n", "no", "nao", "não"):
            return []
        if answer.lower() in ("all", "todos", "a"):
            chosen = names
        else:
            chosen, bad = [], []
            for part in answer.replace(" ", "").split(","):
                if part.isdigit() and 1 <= int(part) <= len(names):
                    chosen.append(names[int(part) - 1])
                elif part in TARGETS:
                    chosen.append(part)
                elif part:
                    bad.append(part)
            if bad or not chosen:
                print(f"Invalid choice: {', '.join(bad) or '(empty)'}")
                continue
        chosen = list(dict.fromkeys(chosen))
        if "claude-desktop" in chosen:
            print(
                "Note: if the noul.mcpb extension is already installed in Claude Desktop, skip this option "
                "(you would end up with two 'noul' servers)."
            )
        return chosen
    return []


def _parse_args(argv):
    ap = argparse.ArgumentParser(prog="noul-install", description="Install Noul Search into coding & desktop agents.")
    ap.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help=f"Any of: {', '.join(TARGETS)} (default: ask in a terminal, otherwise all)",
    )
    ap.add_argument("--all", action="store_true", help="Install into every supported agent")
    ap.add_argument(
        "--runner",
        choices=RUNNERS,
        help="How agents start the MCP server: this Python interpreter (default) or `uvx` "
        "(default from $NOUL_INSTALL_RUNNER)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Only print what would change")
    ap.add_argument("--uninstall", action="store_true", help="Remove everything installed by Noul")
    a = ap.parse_args(argv)
    bad = [x for x in a.targets if x not in TARGETS]
    if bad:
        ap.error(f"unknown target(s): {', '.join(bad)}. Choose from: {', '.join(TARGETS)}")
    return a


def _chosen_targets(a, run):
    """Target names for this run: --all, the named ones, the interactive picker, else all. [] = cancelled."""
    if a.all:
        return list(TARGETS)
    if a.targets:
        return a.targets
    if _interactive() and not run.uninstall:
        return choose_targets()
    return list(TARGETS)


def main(argv=None):
    a = _parse_args(argv)
    run = Install(dry=a.dry_run, uninstall=a.uninstall, runner=a.runner or _default_runner(), assets=_asset_root())
    if not (run.assets / "skills/noul-search/SKILL.md").is_file():
        print(
            f"ERROR: Noul skill/agent templates not found (looked in {REPO} and {sys.prefix}/share/{PKG}).",
            file=sys.stderr,
        )
        return 1
    frozen_copy = FROZEN and run.runner == "python"
    if frozen_copy and not run.uninstall:
        try:
            run.install_root = run.deploy_frozen()
        except OSError as e:
            print(f"ERROR: could not copy Noul to {app_dir()}: {e}", file=sys.stderr)
            return 1

    if not run.uninstall:
        cmd, args = run.mcp_command()
        how = "executable" if frozen_copy else run.runner
        print(f"MCP server command ({how}): {' '.join(_q(x) for x in [cmd, *args])}")
    targets = _chosen_targets(a, run)
    if not targets:
        print("Nothing installed.")
        return 0
    for name in targets:
        print(f"== {name}")
        try:
            TARGETS[name].install(run)
        except OSError as e:
            run.fail(f"{name}: {e}")
    if FROZEN and run.uninstall and a.all and not run.errors:
        # Everything was unregistered: also remove the stable copy (not the file being run).
        for name in APP_FILES:
            run.remove(app_dir() / name)
        if Path(sys.executable).resolve() != frozen_target().resolve():
            run.remove(frozen_target())

    if run.errors:
        print(f"\nFinished with {len(run.errors)} error(s); the affected files were not modified.", file=sys.stderr)
        return 1
    if not run.uninstall and not run.dry:
        print(
            "\nDone. Next: set a contact email so Unpaywall / polite API pools can be used (optional):\n"
            "  - Claude Code: /noul-search-config email:you@university.edu\n"
            f"  - Any terminal: {run.cli_command()} config --email you@university.edu   (or export NOUL_EMAIL)"
        )
    return 0


def _interactive():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
