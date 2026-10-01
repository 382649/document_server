"""Authentication: sign up, login, password hashing (bcrypt)."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

import bcrypt

from database import get_conn


class AuthError(Exception):
    """User-facing authentication problem."""


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())  # equalises timing


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def signup(username: str, email: str, password: str, confirm: str) -> dict:
    username, email = (username or "").strip(), (email or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise AuthError("Username must be 3-30 characters: letters, numbers, . _ - only.")
    if not EMAIL_RE.match(email):
        raise AuthError("Please enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if len(password.encode("utf-8")) > 72:
        raise AuthError("Password is too long (max 72 bytes).")
    if password != confirm:
        raise AuthError("Passwords do not match.")

    created = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (username, email, hash_password(password), created),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise AuthError("An account with that username or email already exists.") from None
    return {"id": user_id, "username": username, "email": email}


def login(identifier: str, password: str) -> dict:
    identifier = (identifier or "").strip()
    if not identifier or not password:
        raise AuthError("Please enter your username/email and password.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, password_hash FROM users WHERE username = ? OR email = ?",
            (identifier, identifier),
        ).fetchone()
    if row is None:
        bcrypt.checkpw(password.encode("utf-8")[:72], _DUMMY_HASH)  # no user-enumeration timing leak
        raise AuthError("Invalid username/email or password.")
    if not verify_password(password, row["password_hash"]):
        raise AuthError("Invalid username/email or password.")
    return {"id": row["id"], "username": row["username"], "email": row["email"]}


def user_exists(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None
