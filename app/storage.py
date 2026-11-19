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
CREATE TABLE IF NOT EXISTS vacancies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT NOT NULL,
    url           TEXT NOT NULL,
    city          TEXT,
    salary        TEXT,
    description   TEXT,
    contact_email TEXT,
    published_at  TEXT,
    found_at      TEXT NOT NULL,
    status        TEXT NOT NULL,   -- filtered | duplicate | low_score | ready | error | (шаг 3: applied, emailed, skipped)
    status_note   TEXT,
    score         INTEGER,
    score_reason  TEXT,
    letter        TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS vacancies_dedupe ON vacancies(dedupe_key);
CREATE INDEX IF NOT EXISTS vacancies_found ON vacancies(found_at);
"""

STATUS_LABELS = {
    "ready": "Готово к отклику",
    "low_score": "Не подходит",
    "filtered": "Отсеяно фильтром",
    "duplicate": "Дубль",
    "error": "Ошибка",
    "applied": "Откликнулся",
    "emailed": "Отправлено на email",
    "skipped": "Пропущено",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    now = _now()
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


def vacancy_seen(source: str, external_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM vacancies WHERE source = ? AND external_id = ? AND status != 'error'",
            (source, external_id),
        ).fetchone()
    return row is not None


def dedupe_exists(key: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM vacancies WHERE dedupe_key = ? AND status NOT IN ('error')", (key,)
        ).fetchone()
    return row is not None


def save_vacancy(v, dedupe_key: str, status: str, note: str = "", score: int | None = None,
                 reason: str = "", letter: str = "") -> int:
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO vacancies(source, external_id, dedupe_key, title, company, url, city, salary, "
            "description, contact_email, published_at, found_at, status, status_note, score, score_reason, "
            "letter, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source, external_id) DO UPDATE SET status = excluded.status, "
            "status_note = excluded.status_note, score = excluded.score, score_reason = excluded.score_reason, "
            "letter = excluded.letter, description = excluded.description, "
            "contact_email = excluded.contact_email, updated_at = excluded.updated_at",
            (v.source, v.external_id, dedupe_key, v.title, v.company, v.url, v.city, v.salary_text(),
             v.description, v.contact_email, v.published_at, now, status, note, score, reason, letter, now),
        )
        return cur.lastrowid


PASSED = ("ready", "applied", "emailed", "skipped")


def count_passed_since(since_iso: str) -> int:
    """Сколько вакансий прошли оценку (и попали к вам) с указанного момента."""
    with connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM vacancies WHERE status IN ({','.join('?' * len(PASSED))}) AND found_at >= ?",
            (*PASSED, since_iso),
        ).fetchone()
    return row[0]


def list_vacancies(status: str | None = None, limit: int = 100) -> list[dict]:
    query = "SELECT * FROM vacancies"
    args: list = []
    if status:
        query += " WHERE status = ?"
        args.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def get_vacancy(vacancy_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    return dict(row) if row else None
