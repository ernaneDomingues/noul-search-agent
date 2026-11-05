"""User configuration: contact e-mail, workspace and output folders (config.json + env)."""

import json
import os
import re
import tempfile
from pathlib import Path

from noul import errors as _errors

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _cache_dir() -> Path:
    d = Path(os.environ.get("NOUL_HOME") or Path.home() / ".cache" / "noul-search-agent")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path():
    p = os.environ.get("NOUL_DB_PATH") or os.environ.get("DB_PATH") or str(_cache_dir() / "cache.db")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    return p


def _config_file() -> Path:
    return _cache_dir() / "config.json"


def _is_noul_home_path(path: Path) -> bool:
    """True inside Noul's own folder (under any spelling) and for a hard link to its config file, which holds the settings."""

    def same(a: Path, b: Path) -> bool:
        try:
            return os.path.samefile(a, b)
        except OSError:
            return False

    home = _cache_dir()
    if same(path, _config_file()) or any(same(part, home) for part in (path, *path.parents)):
        return True
    # Relocated databases, with their -wal files and copies.
    databases = [Path(os.environ[v]) for v in ("NOUL_DB_PATH", "DB_PATH", "NOUL_PROJECTS_DB") if os.environ.get(v)]
    return any(same(path.parent, db.parent) and path.name.startswith(db.name) for db in databases)


def _is_hidden_below(path: Path, root: Path) -> bool:
    """True when a component of `path` under `root` is hidden: such folders hold secrets and tool configuration."""
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _load_config_file() -> dict:
    cfg_file = _config_file()
    if not cfg_file.exists():
        return {}
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _valid_email(value):
    v = (value or "").strip()
    if not v or not EMAIL_RE.match(v) or v.lower().endswith(".local"):
        return None
    return v


def _resolve_email(cfg=None):
    """Return (email, source). Never fabricates a placeholder address."""
    cfg = _load_config_file() if cfg is None else cfg
    email = _valid_email(cfg.get("email"))
    if email:
        return email, "config"
    email = _valid_email(os.environ.get("NOUL_EMAIL") or os.environ.get("UNPAYWALL_EMAIL"))
    if email:
        return email, "env"
    return None, "none"


def _default_workspace() -> str:
    cwd = Path(os.getcwd())
    try:
        home = Path.home()
    except RuntimeError:  # a container user without a home folder
        return str(Path(tempfile.gettempdir()) / "noul-workspace") if cwd == Path(cwd.anchor) else str(cwd)
    if cwd == Path(cwd.anchor) or cwd.resolve() == home.resolve():
        return str(home / "Documents" / "Noul")
    return str(cwd)


def get_config() -> dict:
    cfg = _load_config_file()
    email, source = _resolve_email(cfg)
    # An explicit NOUL_WORKSPACE (set by the host, e.g. the Claude Desktop extension's "Noul folder") wins over
    # config.json, as environment variables usually do; without either, the current folder - unless that is a
    # filesystem root or the home folder (desktop apps start servers there), where ~/Documents/Noul is used instead.
    # A stored value counts only if `noul config` confirmed it: older versions let a tool call write it.
    env_workspace = os.environ.get("NOUL_WORKSPACE")
    confirmed = cfg.get("workspace") is not None and cfg.get("workspace") == cfg.get("workspace_confirmed")
    stored = cfg.get("workspace") if confirmed else None
    workspace = env_workspace or stored or _default_workspace()
    return {
        "email": email,
        "email_source": source,
        "unpaywall_enabled": bool(email),
        "workspace": str(Path(workspace).expanduser().resolve()),
        "workspace_source": "env" if env_workspace else "config" if stored else "default",
        # Saved by an older version and not used: the user decides with `noul config --workspace`.
        "workspace_ignored": None if confirmed else cfg.get("workspace"),
        "download_dir": cfg.get("download_dir") or "./papers",
        "research_dir": cfg.get("research_dir") or "./research",
        "config_file": str(_config_file()),
        # The user declined the one-time contact e-mail prompt (MCP elicitation); `clear` resets it.
        "email_prompt_declined": bool(cfg.get("email_prompt_declined")),
    }


def set_config(
    email=None,
    workspace=None,
    download_dir=None,
    research_dir=None,
    clear=False,
    email_prompt_declined=None,
    keep_protected=False,
) -> dict:
    cfg_file = _config_file()
    if clear:
        kept = _load_config_file() if keep_protected else {}
        kept = {k: kept[k] for k in ("workspace", "workspace_confirmed") if k in kept}
        if kept:
            cfg_file.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        elif cfg_file.exists():
            cfg_file.unlink()
        return get_config()
    cfg = _load_config_file()
    if email is not None:
        cleaned = re.sub(r"^email:\s*", "", str(email).strip(), flags=re.I).strip()
        if cleaned and not EMAIL_RE.match(cleaned):
            raise _errors.HTTPError(400, f"Invalid email address: {cleaned}")
        if cleaned:
            cfg["email"] = cleaned
        else:
            cfg.pop("email", None)
    if workspace is not None:
        ws = str(workspace).strip()
        if ws:
            ws_path = Path(ws).expanduser().resolve()
            if not ws_path.is_dir():
                raise _errors.HTTPError(400, f"Workspace directory does not exist: {ws_path}")
            cfg["workspace"] = str(ws_path)
            cfg["workspace_confirmed"] = str(ws_path)  # an older version rewriting `workspace` no longer matches
        else:
            cfg.pop("workspace", None)
            cfg.pop("workspace_confirmed", None)
    if email_prompt_declined is not None:
        if email_prompt_declined:
            cfg["email_prompt_declined"] = True
        else:
            cfg.pop("email_prompt_declined", None)
    for key, value in (("download_dir", download_dir), ("research_dir", research_dir)):
        if value is not None:
            if str(value).strip():
                cfg[key] = str(value).strip()
            else:
                cfg.pop(key, None)
    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(cfg_file, 0o600)
    except OSError:
        pass
    return get_config()


def _email():
    return _resolve_email()[0]
