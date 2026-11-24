"""SQLite storage. `events` and `workstreams` are rebuilt by refresh; `pomodoros` and `cache` persist."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DATA

DB = DATA / "hub.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts TEXT, day TEXT, project TEXT, source TEXT, kind TEXT,
  title TEXT, detail TEXT, ref TEXT, tool TEXT, weight INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS ev_day ON events(project, day);
CREATE TABLE IF NOT EXISTS workstreams (
  id TEXT PRIMARY KEY, project TEXT, title TEXT, start TEXT, end TEXT, status TEXT,
  source TEXT, ref TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS pomodoros (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, started TEXT, ended TEXT, day TEXT,
  minutes REAL, kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stamp TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def event_id(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:20]


def cache_get(con, key: str, stamp: str):
    row = con.execute("SELECT stamp, payload FROM cache WHERE key=?", (key,)).fetchone()
    if row and row["stamp"] == stamp:
        return json.loads(row["payload"])
    return None


def cache_put(con, key: str, stamp: str, payload) -> None:
    con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, stamp, json.dumps(payload)))


def file_stamp(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"
