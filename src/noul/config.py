"""User configuration: contact e-mail, workspace and output folders (config.json + env)."""

import json
import os
import re
import tempfile
from pathlib import Path

from noul import errors as _errors

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZOTERO_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,128}$")
ZOTERO_USER_RE = re.compile(r"^[0-9]{1,20}$")


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


def zotero_credentials(cfg=None):
    """(user id, API key) for the Zotero Web API, or None. Environment first, then config.json; malformed values do
    not count, because the user id goes into a URL path and the key into a header."""
    cfg = _load_config_file() if cfg is None else cfg
    user_id = str(os.environ.get("NOUL_ZOTERO_USER_ID") or cfg.get("zotero_user_id") or "").strip()
    key = str(os.environ.get("NOUL_ZOTERO_API_KEY") or cfg.get("zotero_api_key") or "").strip()
    return (user_id, key) if ZOTERO_USER_RE.match(user_id) and ZOTERO_KEY_RE.match(key) else None


def _write_private(path: Path, text: str) -> None:
    """Replace a file with one that has mode 0600 from its creation, so the content never has wider permissions.
    On Windows the mode does not restrict access: the file inherits the ACL of its folder (the user profile)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")  # mkstemp creates the file with mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _default_workspace() -> str:
    cwd = Path(os.getcwd())
    if cwd == Path(cwd.anchor):
        return str(Path.home() / "Documents" / "Noul")
    return str(cwd)


def get_config() -> dict:
    cfg = _load_config_file()
    email, source = _resolve_email(cfg)
    # An explicit NOUL_WORKSPACE (set by the host, e.g. the Claude Desktop extension's "Noul folder") wins over
    # config.json, as environment variables usually do; without either, the current folder - unless that is a
    # filesystem root (desktop apps start servers in `/` or C:\), where ~/Documents/Noul is used instead.
    workspace = os.environ.get("NOUL_WORKSPACE") or cfg.get("workspace") or _default_workspace()
    return {
        "email": email,
        "email_source": source,
        "unpaywall_enabled": bool(email),
        "workspace": str(Path(workspace).expanduser().resolve()),
        "download_dir": cfg.get("download_dir") or "./papers",
        "research_dir": cfg.get("research_dir") or "./research",
        "config_file": str(_config_file()),
        # The user declined the one-time contact e-mail prompt (MCP elicitation); `clear` resets it.
        "email_prompt_declined": bool(cfg.get("email_prompt_declined")),
        # Never the key itself: this dictionary is what `configure_noul` and `noul config` show.
        "zotero_configured": zotero_credentials(cfg) is not None,
        "zotero_user_id": (zotero_credentials(cfg) or (None,))[0],  # shown so a changed account can be noticed
    }


def set_config(
    email=None,
    workspace=None,
    download_dir=None,
    research_dir=None,
    clear=False,
    email_prompt_declined=None,
    zotero_api_key=None,
    zotero_user_id=None,
) -> dict:
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
    for field, value, pattern in (
        ("zotero_api_key", zotero_api_key, ZOTERO_KEY_RE),
        ("zotero_user_id", zotero_user_id, ZOTERO_USER_RE),
    ):
        if value is not None:
            cleaned = str(value).strip()
            if cleaned and not pattern.match(cleaned):
                raise _errors.HTTPError(400, f"Invalid {field} (its value is not repeated here).")
            if cleaned:
                cfg[field] = cleaned
            else:
                cfg.pop(field, None)
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
    _write_private(cfg_file, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return get_config()


def _email():
    return _resolve_email()[0]
