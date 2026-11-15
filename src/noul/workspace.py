"""Workspace confinement: every file a tool reads or writes on the user's behalf is checked here.

Output folders go through `safe_output_dir` + `reject_symlink_path`; input files through `validate_path`.
Refusals are HTTP 403. Keep these checks at least as strict as they are: text from papers can steer a model
into asking for paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

from noul import config as _config
from noul import errors as _errors

_FILE_EXT_RE = re.compile(r"\.(?:md|markdown|tex|txt|bib|pdf)$", re.I)


def safe_output_dir(requested_dir=None, default_subdir="papers") -> Path:
    """Resolve an output directory confined to the configured workspace (relative paths are workspace-relative)."""
    cfg = _config.get_config()
    workspace = Path(cfg["workspace"]).resolve()
    cfg_key = {"papers": "download_dir", "research": "research_dir"}.get(default_subdir, f"{default_subdir}_dir")
    raw = requested_dir or cfg.get(cfg_key) or f"./{default_subdir}"
    p = Path(str(raw)).expanduser()
    target = p.resolve() if p.is_absolute() else (workspace / p).resolve()
    if os.environ.get("NOUL_ALLOW_ANY_PATH") != "1":
        try:
            target.relative_to(workspace)
        except ValueError:
            raise _errors.HTTPError(
                403,
                f"Path '{target}' is outside the allowed workspace '{workspace}'. "
                "Use a path inside the workspace. The user can change it with `noul config --workspace`, NOUL_WORKSPACE or "
                "the extension's Noul folder setting; an environment value wins.",
            ) from None
        if _config._is_hidden_below(target, workspace):
            raise _errors.HTTPError(403, f"Refusing to use the hidden path '{target}'.")
    if _config._is_noul_home_path(target):
        raise _errors.HTTPError(403, "Refusing to use Noul's own folder: keep the workspace outside NOUL_HOME.")
    return target


def reject_symlink_path(path, requested_dir=None):
    """Refuse (HTTP 403) output paths with a symlink in any component below the workspace root, checking both the
    resolved path and the path as requested (whose symlinks `safe_output_dir` already followed)."""
    workspace = Path(_config.get_config()["workspace"]).expanduser()
    roots = {Path(os.path.abspath(workspace)), workspace.resolve()}
    candidates = [Path(os.path.abspath(path))]
    if requested_dir:
        raw = Path(str(requested_dir)).expanduser()
        candidates.append(Path(os.path.abspath(raw if raw.is_absolute() else workspace / raw)))
    for cur in candidates:
        while cur not in roots and any(r in cur.parents for r in roots):
            if cur.is_symlink():
                raise _errors.HTTPError(403, f"Refusing to write through symbolic link '{cur}'.")
            cur = cur.parent
    return Path(path)


def workspace_root() -> Path:
    return Path(_config.get_config()["workspace"]).expanduser().resolve()


def validate_path(raw_path: str, must_exist: bool = True) -> Path:
    ws = workspace_root()
    raw_str = str(raw_path).strip()
    p = Path(raw_str).expanduser()
    candidate_abs = Path(os.path.abspath(p if p.is_absolute() else ws / p))

    # Reject symlinks anywhere along the path inside or escaping workspace
    roots = {Path(os.path.abspath(ws)), ws}
    cur = candidate_abs
    while True:
        if cur.is_symlink():
            raise _errors.HTTPError(403, f"Refusing to access symbolic link '{cur}'.")
        if cur in roots or cur.parent == cur:
            break
        cur = cur.parent

    resolved = p.resolve() if p.is_absolute() else (ws / p).resolve()
    if _config._is_noul_home_path(resolved):  # holds the Zotero key, even with NOUL_ALLOW_ANY_PATH
        raise _errors.HTTPError(403, "Refusing to access Noul's own folder: keep the workspace outside NOUL_HOME.")
    if os.environ.get("NOUL_ALLOW_ANY_PATH") != "1":
        try:
            resolved.relative_to(ws)
        except ValueError:
            raise _errors.HTTPError(
                403,
                f"Path '{resolved}' is outside the allowed workspace '{ws}'.",
            ) from None
        if _config._is_hidden_below(resolved, ws):
            raise _errors.HTTPError(403, f"Refusing to access the hidden path '{raw_str}'.")
    if resolved.is_symlink():
        raise _errors.HTTPError(403, f"Refusing to access symbolic link '{resolved}'.")
    if must_exist and not resolved.is_file():
        raise _errors.HTTPError(404, f"File not found in workspace: '{raw_str}'")
    return resolved


def looks_like_filepath(raw_input: str) -> bool:
    s = (raw_input or "").strip()
    if not s or "\n" in s or "\r" in s or len(s) > 260:
        return False
    if s.startswith(("../", "./", "/", "~/", "..\\")):
        return True
    if _FILE_EXT_RE.search(s) and len(s.split()) <= 4:
        return True
    ws = workspace_root()
    cand = ws / s
    try:
        if cand.exists() or cand.is_symlink():
            return True
    except OSError:
        pass
    return False
