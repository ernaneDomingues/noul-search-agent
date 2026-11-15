"""SQLite key-value cache (WAL) shared by providers and tools."""

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager

from noul import config as _config
from noul import log as _log

PAPER = "paper:"  # key prefix of a cached paper record
_write_failure_reported = False


@contextmanager
def _conn():
    conn = sqlite3.connect(_config._db_path(), timeout=15)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def cache_get(key, max_age=86400 * 14):
    try:
        with _conn() as c:
            row = c.execute("SELECT v, ts FROM kv WHERE k = ?", (key,)).fetchone()
            if not row:
                return None
            val, ts = row
            if max_age and (time.time() - ts) > max_age:
                return None
            return json.loads(val)
    except Exception as exc:
        # A cache that cannot be read is a miss, never an error for the caller; the type alone is logged.
        _log.logger.debug("cache read failed: %s", type(exc).__name__)
        return None


def _max_rows():
    """NOUL_CACHE_MAX_ROWS caps the cache (oldest entries pruned on write); unset = unbounded (local use)."""
    try:
        return max(int(os.environ.get("NOUL_CACHE_MAX_ROWS") or 0), 0)
    except ValueError:
        return 0


def cache_set(key, val):
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
                (key, json.dumps(val, ensure_ascii=False), int(time.time())),
            )
            limit = _max_rows()
            if limit:
                # On hosted instances /tmp lives in RAM: keep the cache bounded (oldest rows first).
                c.execute(
                    "DELETE FROM kv WHERE k IN (SELECT k FROM kv ORDER BY ts ASC, rowid ASC "
                    "LIMIT max(0, (SELECT COUNT(*) FROM kv) - ?))",
                    (limit,),
                )
    except Exception as exc:
        # Said once at WARNING (an unwritable cache makes every call slower), then only at DEBUG.
        global _write_failure_reported
        level = logging.DEBUG if _write_failure_reported else logging.WARNING
        _write_failure_reported = True
        _log.logger.log(level, "cache write failed: %s", type(exc).__name__)


def get_paper(paper_id, max_age=86400 * 14):
    """The cached record of a paper id (or of an identifier it was resolved from), else None."""
    return cache_get(PAPER + str(paper_id), max_age=max_age)


def put_paper(paper_id, paper):
    cache_set(PAPER + str(paper_id), paper)


def scan(*prefixes, limit=None, newest_first=True):
    """Decoded dict values whose key starts with one of `prefixes`, newest first (rows of the same second in
    the order they were written) or simply in the order they were written; `limit` caps the rows read.

    A full scan of the table: fine for a local cache, and what the offline tools that match against every
    cached paper need."""
    where = " OR ".join("k LIKE ? ESCAPE '\\'" for _ in prefixes)
    params = [p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for p in prefixes]
    sql = f"SELECT v FROM kv WHERE {where} ORDER BY " + ("ts DESC, rowid ASC" if newest_first else "rowid ASC")
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    try:
        with _conn() as c:
            rows = c.execute(sql, params).fetchall()
    except Exception as exc:
        _log.logger.debug("cache scan failed: %s", type(exc).__name__)
        return []
    out = []
    for (raw,) in rows:
        try:
            val = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(val, dict):
            out.append(val)
    return out
