"""Persistent research projects (v2 Phase 3b): a question, the papers considered for it with an inclusion
status and reason, notes, and (for later use) searches, extractions and drafts.

One SQLite file per user next to config.json (`projects.db`, overridable with NOUL_PROJECTS_DB), separate from
the HTTP cache: the cache table is pruned on hosted instances (NOUL_CACHE_MAX_ROWS), a project must never be.
The schema is versioned (`schema_version`) and migrated forward on first use; migrations only ever append."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from noul import config as _config

STATUSES = ("candidate", "included", "excluded")
MAX_TITLE = 300
MAX_QUESTION = 2000
MAX_REASON = 2000
MAX_NOTE = 20000
MAX_IDS = 100

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            question TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            metadata_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE project_papers (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            paper_id TEXT NOT NULL REFERENCES papers(id),
            status TEXT NOT NULL CHECK (status IN ('candidate', 'included', 'excluded')),
            reason TEXT,
            added_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (project_id, paper_id)
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            paper_id TEXT,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            query TEXT NOT NULL,
            params_json TEXT,
            result_ids_json TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE extractions (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            paper_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE drafts (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            path TEXT,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE INDEX project_papers_status ON project_papers(project_id, status);
        CREATE INDEX notes_project ON notes(project_id, created_at);
        """,
    ),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]

_lock = threading.Lock()


class ProjectNotFound(KeyError):
    pass


class ReadOnlyStoreMissing(FileNotFoundError):
    """No projects.db yet (read tools return empty results instead of creating it)."""


MAX_PROJECTS = 1000
MAX_NOTES_PER_PROJECT = 5000


def db_path() -> Path:
    """NOUL_PROJECTS_DB, else `projects.db` in the Noul config folder (next to config.json)."""
    raw = os.environ.get("NOUL_PROJECTS_DB")
    return Path(raw).expanduser() if raw else _config._cache_dir() / "projects.db"


def _version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0


def _migrate(conn: sqlite3.Connection) -> None:
    if _version(conn) >= SCHEMA_VERSION:
        return
    # BEGIN IMMEDIATE takes the write lock before re-reading the version, so two processes starting together
    # (CLI + MCP server) apply each migration once instead of racing to create the same tables.
    conn.commit()
    previous = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _version(conn)
        for version, sql in MIGRATIONS:
            if version > current:
                for statement in (x.strip() for x in sql.split(";")):
                    if statement:
                        conn.execute(statement)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous


@contextmanager
def _conn(readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """A connection to the store. `readonly` opens the file read-only (raises ReadOnlyStoreMissing when it does
    not exist yet), so read tools never create or migrate it."""
    path = db_path()
    if readonly:
        if not path.is_file():
            raise ReadOnlyStoreMissing(str(path))
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(path, timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            _private(path)
            with _lock:  # cheap when up to date (one SELECT); a deleted file is re-created at the current version
                _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _private(path: Path) -> None:
    """Notes may hold unpublished research: the database and its WAL files are readable by the user only."""
    for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if f.exists():
                os.chmod(f, 0o600)
        except OSError:
            pass


def _now() -> int:
    return int(time.time())


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(text: str) -> str:
    """ASCII slug of a title: accents transliterated ("Café" -> "cafe"), at most 60 characters."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:60].strip("-") or "project"


def _same_title(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a or "").strip().casefold() == re.sub(r"\s+", " ", b or "").strip().casefold()


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = str(text).strip()
    if len(text) > limit:
        raise ValueError(f"text longer than {limit} characters")
    return text


def _project_row(conn: sqlite3.Connection, ref: str | int) -> sqlite3.Row:
    """By slug first (exact, then slugified), then by numeric id - so a project titled "2" never shadows id 2's
    lookup by slug, and vice versa only when no slug matches."""
    ref_s = str(ref).strip()
    for query, value in (
        ("SELECT * FROM projects WHERE slug = ?", ref_s),
        ("SELECT * FROM projects WHERE slug = ?", slugify(ref_s)),
        ("SELECT * FROM projects WHERE id = ?", int(ref_s.lstrip("#")) if ref_s.lstrip("#").isdigit() else None),
    ):
        if value is None:
            continue
        row = conn.execute(query, (value,)).fetchone()
        if row is not None:
            return row
    raise ProjectNotFound(ref_s)


def _counts(conn: sqlite3.Connection, project_id: int) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for status, n in conn.execute(
        "SELECT status, COUNT(*) FROM project_papers WHERE project_id = ? GROUP BY status", (project_id,)
    ):
        counts[status] = n
    return counts


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    notes = conn.execute("SELECT COUNT(*) FROM notes WHERE project_id = ?", (row["id"],)).fetchone()[0]
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "question": row["question"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "counts": _counts(conn, row["id"]),
        "note_count": notes,
    }


def _touch(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))


# --------------------------------------------------------------------------- public API


def create_project(title: str, question: str | None = None, slug: str | None = None) -> dict[str, Any]:
    """Create a project (idempotent on its slug: an existing one is returned with `created: False`)."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:MAX_TITLE]  # callers may pass long dossier topics
    if not title:
        raise ValueError("title must not be empty")
    question = _clip(question, MAX_QUESTION) or None
    explicit = slug is not None
    if explicit:
        slug = str(slug)
        if not _SLUG.match(slug) or len(slug) > 60:
            raise ValueError("slug must be lowercase letters, digits and hyphens (at most 60 characters)")
    else:
        slug = slugify(title)
    with _conn() as conn:
        existing = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        if existing is not None and not explicit and not _same_title(existing["title"], title):
            # Different titles that share a slug ("!!!" and "???", or a long common prefix) are different projects.
            base, n = slug[:56].rstrip("-"), 2
            while existing is not None and not _same_title(existing["title"], title):
                slug = f"{base}-{n}"
                existing = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
                n += 1
        if existing is not None:
            if question and not existing["question"]:
                conn.execute(
                    "UPDATE projects SET question = ?, updated_at = ? WHERE id = ?", (question, _now(), existing["id"])
                )
                existing = _project_row(conn, slug)
            return {**_summary(conn, existing), "created": False}
        if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= MAX_PROJECTS:
            raise ValueError(f"at most {MAX_PROJECTS} projects")
        now = _now()
        conn.execute(
            "INSERT INTO projects (slug, title, question, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (slug, title, question, now, now),
        )
        return {**_summary(conn, _project_row(conn, slug)), "created": True}


def list_projects() -> list[dict[str, Any]]:
    try:
        with _conn(readonly=True) as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall()
            return [_summary(conn, r) for r in rows]
    except ReadOnlyStoreMissing:
        return []
    except sqlite3.OperationalError:  # a store created by an older version: migrate it once
        pass
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall()
        return [_summary(conn, r) for r in rows]


def get_project(ref: str | int, include_papers: bool = True) -> dict[str, Any]:
    """Project summary plus its papers (with status and reason) and notes."""
    try:
        with _conn(readonly=True) as conn:
            return _detail(conn, ref, include_papers)
    except ReadOnlyStoreMissing:
        raise ProjectNotFound(str(ref)) from None
    except sqlite3.OperationalError:  # a store created by an older version: migrate it once
        with _conn() as conn:
            return _detail(conn, ref, include_papers)


def _detail(conn: sqlite3.Connection, ref: str | int, include_papers: bool) -> dict[str, Any]:
    row = _project_row(conn, ref)
    out = _summary(conn, row)
    out["papers"] = []
    if include_papers:
        for r in conn.execute(
            "SELECT pp.*, p.metadata_json FROM project_papers pp JOIN papers p ON p.id = pp.paper_id "
            "WHERE pp.project_id = ? ORDER BY CASE pp.status WHEN 'included' THEN 0 WHEN 'candidate' THEN 1 "
            "ELSE 2 END, pp.added_at, pp.paper_id",
            (row["id"],),
        ):
            meta = json.loads(r["metadata_json"])
            out["papers"].append(
                {
                    "id": r["paper_id"],
                    "title": meta.get("title"),
                    "authors": meta.get("authors") or [],
                    "year": meta.get("year"),
                    "doi": meta.get("doi"),
                    "evidence": meta.get("evidence"),
                    "status": r["status"],
                    "reason": r["reason"],
                    "added_at": r["added_at"],
                    "updated_at": r["updated_at"],
                }
            )
    out["notes"] = [
        {"id": n["id"], "paper_id": n["paper_id"], "text": n["text"], "created_at": n["created_at"]}
        for n in conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY created_at, id", (row["id"],))
    ]
    return out


def _status(status: str) -> str:
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    return status


def add_papers(ref: str | int, papers: list[dict], status: str = "candidate", reason: str | None = None) -> dict:
    """Add resolved paper records to a project. A paper already in the project keeps its status (use
    set_status to change it); its stored metadata is refreshed."""
    status = _status(status)
    reason = _clip(reason, MAX_REASON) or None
    if len(papers) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} papers per call")
    now = _now()
    added: list[str] = []
    already: list[str] = []
    with _conn() as conn:
        row = _project_row(conn, ref)
        for paper in papers:
            pid = str(paper["id"])
            conn.execute(
                "INSERT INTO papers (id, metadata_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET metadata_json = excluded.metadata_json, updated_at = excluded.updated_at",
                (pid, json.dumps(paper, ensure_ascii=False, default=str), now),
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO project_papers (project_id, paper_id, status, reason, added_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], pid, status, reason, now, now),
            )
            (added if cur.rowcount else already).append(pid)
        _touch(conn, row["id"])
        return {"project": _summary(conn, _project_row(conn, row["slug"])), "added": added, "already_present": already}


def set_status(ref: str | int, paper_ids: list[str], status: str, reason: str | None = None) -> dict:
    """Change the inclusion status (and reason) of papers already in the project."""
    status = _status(status)
    reason = _clip(reason, MAX_REASON) or None
    if len(paper_ids) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} papers per call")
    updated: list[str] = []
    missing: list[str] = []
    with _conn() as conn:
        row = _project_row(conn, ref)
        for pid in paper_ids:
            cur = conn.execute(
                "UPDATE project_papers SET status = ?, reason = ?, updated_at = ? WHERE project_id = ? AND paper_id = ?",
                (status, reason, _now(), row["id"], str(pid)),
            )
            (updated if cur.rowcount else missing).append(str(pid))
        _touch(conn, row["id"])
        return {"project": _summary(conn, _project_row(conn, row["slug"])), "updated": updated, "missing": missing}


def add_note(ref: str | int, text: str, paper_id: str | None = None) -> dict:
    text = _clip(text, MAX_NOTE) or ""
    if not text:
        raise ValueError("note text must not be empty")
    with _conn() as conn:
        row = _project_row(conn, ref)
        notes = conn.execute("SELECT COUNT(*) FROM notes WHERE project_id = ?", (row["id"],)).fetchone()[0]
        if notes >= MAX_NOTES_PER_PROJECT:
            raise ValueError(f"at most {MAX_NOTES_PER_PROJECT} notes per project")
        if (
            paper_id is not None
            and not conn.execute(
                "SELECT 1 FROM project_papers WHERE project_id = ? AND paper_id = ?", (row["id"], str(paper_id))
            ).fetchone()
        ):
            raise ValueError(f"paper {paper_id!r} is not in this project: add it first")
        cur = conn.execute(
            "INSERT INTO notes (project_id, paper_id, text, created_at) VALUES (?, ?, ?, ?)",
            (row["id"], str(paper_id) if paper_id else None, text, _now()),
        )
        _touch(conn, row["id"])
        return {"project": _summary(conn, _project_row(conn, row["slug"])), "note_id": cur.lastrowid}


def meta_get(key: str) -> str | None:
    try:
        with _conn(readonly=True) as conn:
            row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
            return row["v"] if row else None
    except (ReadOnlyStoreMissing, sqlite3.OperationalError):
        return None


def meta_set(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (key, value))
