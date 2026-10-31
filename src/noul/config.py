"""User configuration: contact e-mail, workspace and output folders (config.json + env)."""

import json
import os
import re
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


def get_config() -> dict:
    cfg = _load_config_file()
    email, source = _resolve_email(cfg)
    workspace = cfg.get("workspace") or os.environ.get("NOUL_WORKSPACE") or os.getcwd()
    return {
        "email": email,
        "email_source": source,
        "unpaywall_enabled": bool(email),
        "workspace": str(Path(workspace).expanduser().resolve()),
        "download_dir": cfg.get("download_dir") or "./papers",
        "research_dir": cfg.get("research_dir") or "./research",
        "config_file": str(_config_file()),
    }


def set_config(email=None, workspace=None, download_dir=None, research_dir=None, clear=False) -> dict:
    cfg_file = _config_file()
    if clear:
        if cfg_file.exists():
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
        else:
            cfg.pop("workspace", None)
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
