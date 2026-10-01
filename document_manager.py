"""Document storage + metadata. All queries are scoped to a user_id.

Storage is behind a small interface (StorageBackend) so LocalStorage can be
swapped for S3 / Supabase / Firebase later without touching the rest of the app.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import unicodedata
import uuid
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from database import BASE_DIR, DatabaseError, get_conn


class DocumentError(Exception):
    """User-facing document problem."""


ALLOWED_TYPES = {
    ".pdf": ("PDF", "application/pdf"),
    ".docx": ("DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".txt": ("TXT", "text/plain"),
    ".jpg": ("JPG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".png": ("PNG", "image/png"),
}


def max_file_size() -> int:
    try:
        return int(float(os.getenv("MAX_FILE_SIZE_MB", "10")) * 1024 * 1024)
    except ValueError:
        return 10 * 1024 * 1024


@dataclass(frozen=True)
class Document:
    """Safe, UI-facing view of a document. Deliberately has no filesystem path."""
    id: int
    filename: str
    file_type: str
    file_size: int
    upload_date: str


def mime_for(file_type: str) -> str:
    return ALLOWED_TYPES.get("." + file_type.lower(), (None, "application/octet-stream"))[1]


# --------------------------------------------------------------------- storage
class StorageBackend(ABC):
    """Swap this out for S3, Supabase Storage, Firebase, ..."""

    @abstractmethod
    def save(self, user_id: int, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read(self, user_id: int, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, user_id: int, key: str) -> None: ...


_KEY_RE = re.compile(r"^[a-f0-9]{32}\.[a-z0-9]{2,5}$")


class LocalStorage(StorageBackend):
    """uploads/user_<id>/<random-key>.<ext>. Keys are generated server-side only."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, user_id: int, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise OSError("Invalid storage key")
        base = (self.root / f"user_{int(user_id)}").resolve()
        path = (base / key).resolve()
        if path.parent != base:  # path-traversal guard
            raise OSError("Invalid storage path")
        return path

    def save(self, user_id: int, key: str, data: bytes) -> None:
        path = self._path(user_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read(self, user_id: int, key: str) -> bytes:
        return self._path(user_id, key).read_bytes()

    def delete(self, user_id: int, key: str) -> None:
        try:
            self._path(user_id, key).unlink()
        except FileNotFoundError:
            pass


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        backend = os.getenv("STORAGE_BACKEND", "local").lower()
        if backend != "local":
            raise DocumentError(f"Unsupported STORAGE_BACKEND '{backend}'.")
        _storage = LocalStorage(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
    return _storage


# ------------------------------------------------------------------ validation
def sanitize_filename(name: str) -> tuple[str, str]:
    """Return (safe_display_name, lowercase_extension). Never used as a path."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f]", "", name)
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    stem = re.sub(r"[^\w\-. ()]", "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")[:100] or "document"
    return stem + ext, ext


def _content_matches(ext: str, data: bytes) -> bool:
    """Check magic bytes so a renamed .exe can't pose as a PDF."""
    if ext == ".pdf":
        return b"%PDF-" in data[:1024]
    if ext in (".jpg", ".jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    if ext == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "word/document.xml" in z.namelist()
        except zipfile.BadZipFile:
            return False
    if ext == ".txt":
        return b"\x00" not in data[:8192]
    return False


# ------------------------------------------------------------------ operations
def _to_doc(row: sqlite3.Row) -> Document:
    return Document(row["id"], row["filename"], row["file_type"], row["file_size"], row["upload_date"])


def upload_document(user_id: int, original_name: str, data: bytes) -> Document:
    safe_name, ext = sanitize_filename(original_name)
    if ext not in ALLOWED_TYPES:
        raise DocumentError("Unsupported file type. Allowed: PDF, DOCX, TXT, JPG, JPEG, PNG.")
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > max_file_size():
        raise DocumentError(f"File too large (limit {max_file_size() // (1024 * 1024)} MB).")
    if not _content_matches(ext, data):
        raise DocumentError("File content doesn't match its extension.")

    storage = get_storage()
    key = f"{uuid.uuid4().hex}{ext}"
    try:
        storage.save(user_id, key, data)
    except OSError:
        raise DocumentError("Upload failed while saving the file. Please try again.") from None

    stem = safe_name[: -len(ext)]
    now = datetime.now(timezone.utc).isoformat()
    try:
        for n in range(0, 100):  # duplicates become "name (1).pdf", "name (2).pdf", ...
            name = safe_name if n == 0 else f"{stem} ({n}){ext}"
            try:
                with get_conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO documents (user_id, filename, stored_filename, file_type, file_size, upload_date)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, name, key, ALLOWED_TYPES[ext][0], len(data), now),
                    )
                return Document(cur.lastrowid, name, ALLOWED_TYPES[ext][0], len(data), now)
            except sqlite3.IntegrityError:
                continue
    except DatabaseError:
        storage.delete(user_id, key)
        raise DocumentError("Database error while saving the document.") from None
    storage.delete(user_id, key)
    raise DocumentError("Could not find a unique name for this file.")


def list_documents(user_id: int, query: str | None = None) -> list[Document]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, file_type, file_size, upload_date FROM documents"
            " WHERE user_id = ? ORDER BY upload_date DESC, id DESC",
            (user_id,),
        ).fetchall()
    docs = [_to_doc(r) for r in rows]
    if query and query.strip():
        q = query.strip().lower()
        docs = [d for d in docs if q in d.filename.lower()]
    return docs


def count_documents(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)).fetchone()[0]


def get_document(user_id: int, doc_id: int) -> Document | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, filename, file_type, file_size, upload_date FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
    return _to_doc(row) if row else None


def _stored_key(user_id: int, doc_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT stored_filename FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)
        ).fetchone()
    return row["stored_filename"] if row else None


def read_document(user_id: int, doc_id: int) -> tuple[Document, bytes]:
    doc = get_document(user_id, doc_id)
    key = _stored_key(user_id, doc_id)
    if doc is None or key is None:
        raise DocumentError("That document no longer exists.")
    try:
        return doc, get_storage().read(user_id, key)
    except FileNotFoundError:
        raise DocumentError(f"The file for '{doc.filename}' is missing from storage.") from None
    except OSError:
        raise DocumentError("Could not read the file. Please try again.") from None


def delete_document(user_id: int, doc_id: int) -> bool:
    key = _stored_key(user_id, doc_id)
    if key is None:
        return False
    with get_conn() as conn:
        conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id))
    try:
        get_storage().delete(user_id, key)
    except OSError:
        pass  # metadata is gone; orphaned file can be cleaned up later
    return True
