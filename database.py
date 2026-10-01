"""SQLite access layer: connection handling and schema creation."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class DatabaseError(Exception):
    """Raised for unexpected database failures (not for constraint violations)."""


def db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "app.db"))


@contextmanager
def get_conn():
    """Yield a connection; commit on success, roll back on error.

    sqlite3.IntegrityError is re-raised untouched so callers can detect
    duplicates; every other sqlite error becomes DatabaseError.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        raise DatabaseError(str(exc)) from exc
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL COLLATE NOCASE,
    stored_filename TEXT NOT NULL UNIQUE,
    file_type       TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    upload_date     TEXT NOT NULL,
    UNIQUE (user_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
