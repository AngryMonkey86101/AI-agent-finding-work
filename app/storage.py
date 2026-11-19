"""Хранение настроек и резюме в SQLite."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resume (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    text        TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
"""


@contextmanager
def connect():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def load_settings() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}


def save_settings(values: dict) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()],
        )


def save_resume(filename: str, stored_path: str, text: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO resume(id, filename, stored_path, text, uploaded_at) "
            "VALUES(1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "filename = excluded.filename, stored_path = excluded.stored_path, "
            "text = excluded.text, uploaded_at = excluded.uploaded_at",
            (filename, stored_path, text, now),
        )


def load_resume() -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM resume WHERE id = 1").fetchone()
    return dict(row) if row else None
