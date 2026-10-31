"""SQLite key-value cache (WAL) shared by providers and tools."""

import json
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


def cache_set(key, val):
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
                (key, json.dumps(val, ensure_ascii=False), int(time.time())),
            )
    except Exception:
        pass
