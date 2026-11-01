"""SQLite key-value cache (WAL) shared by providers and tools."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from noul import config as _config


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
    except Exception:
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
    except Exception:
        pass
