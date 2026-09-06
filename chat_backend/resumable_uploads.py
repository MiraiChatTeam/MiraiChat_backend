"""Owner-bound, ciphertext-only resumable attachment uploads."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import struct
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from chat_backend.database import get_db
from chat_backend.settings import (
    FILE_RETENTION_DAYS,
    FILE_TOKEN_TTL_MINUTES,
    MAX_CONCURRENT_UPLOAD_SESSIONS,
    MAX_UPLOAD_CHUNK_BYTES,
    MAX_UPLOAD_CHUNKS,
    MAX_UPLOAD_FILE_BYTES,
    UPLOAD_DIR,
    UPLOAD_SESSION_TTL_HOURS,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_COPY_BUFFER_BYTES = 1024 * 1024
_V2_HEADER_BYTES = 48
_V2_PLAINTEXT_CHUNK_BYTES = 4 * 1024 * 1024
_V2_TAG_BYTES = 16
_COMMIT_LEASE_MINUTES = 5


class UploadSessionCreate(BaseModel):
    client_request_id: str
    encrypted_size: int
    chunk_count: int
    ciphertext_sha256: str
    storage_label: Optional[str] = None


class UploadManifestChunk(BaseModel):
    chunk_index: int
    chunk_size: int
    chunk_sha256: str


class UploadSessionCommit(BaseModel):
    ciphertext_sha256: str
    chunks: List[UploadManifestChunk]


def _now() -> datetime:
    return datetime.now()


def _upload_root() -> Path:
    root = Path(UPLOAD_DIR).resolve() / ".upload_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_session_dir(upload_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(upload_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Upload session not found") from exc
    root = _upload_root()
    result = (root / normalized).resolve()
    if result.parent != root:
        raise HTTPException(status_code=400, detail="Invalid upload id")
    return result


def _chunk_path(upload_id: str, chunk_index: int) -> Path:
    return _safe_session_dir(upload_id) / f"{chunk_index:08d}.chunk"


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            block = source.read(_COPY_BUFFER_BYTES)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _validate_v2_transport_chunk(path: Path, index: int, is_final: bool) -> None:
    """Validate public container framing without possessing the file key."""

    with path.open("rb") as source:
        if index == 0:
            header = source.read(_V2_HEADER_BYTES)
            if (
                len(header) != _V2_HEADER_BYTES
                or header[:8] != b"MIRAIAT2"
                or header[8] != 2
                or header[9] != 1
                or header[10:12] != b"\x00\x00"
                or struct.unpack(">I", header[12:16])[0]
                != _V2_PLAINTEXT_CHUNK_BYTES
            ):
                raise HTTPException(status_code=409, detail="Invalid attachment v2 header")
        length_bytes = source.read(4)
        if len(length_bytes) != 4:
            raise HTTPException(status_code=409, detail="Invalid attachment v2 frame")
        plaintext_length = struct.unpack(">I", length_bytes)[0]
        if plaintext_length > _V2_PLAINTEXT_CHUNK_BYTES:
            raise HTTPException(status_code=409, detail="Invalid attachment v2 frame size")
        if plaintext_length == 0 and not (index == 0 and is_final):
            raise HTTPException(status_code=409, detail="Unexpected empty attachment frame")
        if not is_final and plaintext_length != _V2_PLAINTEXT_CHUNK_BYTES:
            raise HTTPException(status_code=409, detail="Non-final attachment frame is short")
        expected_size = (
            (_V2_HEADER_BYTES if index == 0 else 0)
            + 4
            + plaintext_length
            + _V2_TAG_BYTES
        )
        if path.stat().st_size != expected_size:
            raise HTTPException(status_code=409, detail="Attachment frame boundary mismatch")


def _authenticated_user(cursor, session_secret: Optional[str]):
    cursor.execute(
        "SELECT u.user_id, u.storage_used FROM users u "
        "JOIN sessions s ON s.user_id=u.user_id WHERE s.session_secret=?",
        ((session_secret or "").strip(),),
    )
    user = cursor.fetchone()
    if user is None:
        raise HTTPException(status_code=401)
    return user


def _session_payload(cursor, row, *, include_missing: bool = True) -> dict:
    upload_id = row["upload_id"]
    received = []
    if include_missing and row["state"] != "committed":
        cursor.execute(
            "SELECT chunk_index, chunk_size, chunk_sha256 "
            "FROM attachment_upload_chunks "
            "WHERE upload_id=? ORDER BY chunk_index",
            (upload_id,),
        )
        for item in cursor.fetchall():
            index = int(item["chunk_index"])
            path = _chunk_path(upload_id, index)
            valid = path.is_file() and path.stat().st_size == int(item["chunk_size"])
            if valid:
                received.append(index)
            elif row["state"] == "pending":
                path.unlink(missing_ok=True)
                cursor.execute(
                    "DELETE FROM attachment_upload_chunks "
                    "WHERE upload_id=? AND chunk_index=?",
                    (upload_id, index),
                )
    received_set = set(received)
    result = {
        "status": "ok",
        "upload_id": upload_id,
        "state": row["state"],
        "encrypted_size": int(row["encrypted_size"]),
        "chunk_count": int(row["chunk_count"]),
        "max_chunk_size": MAX_UPLOAD_CHUNK_BYTES,
        "attachment_version": 2,
        "chunk_format": "attachment_v2_authenticated_frame",
        "received_chunks": received,
        "missing_chunks": [
            index
            for index in range(int(row["chunk_count"]))
            if index not in received_set
        ],
        "expires_at": row["expires_at"],
    }
    if row["state"] == "committed":
        file_id = row["file_id"]
        cursor.execute(
            "SELECT token FROM file_download_tokens "
            "WHERE file_id=? AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
            (file_id, _now()),
        )
        token_row = cursor.fetchone()
        if token_row is None:
            token = secrets.token_urlsafe(32)
            cursor.execute(
                "INSERT INTO file_download_tokens (token, file_id, expires_at) "
                "VALUES (?, ?, ?)",
                (
                    token,
                    file_id,
                    _now() + timedelta(minutes=FILE_TOKEN_TTL_MINUTES),
                ),
            )
        else:
            token = token_row["token"]
        result.update(
            {
                "file_id": file_id,
                "size": int(row["encrypted_size"]),
                "sha256": row["ciphertext_sha256"],
                "download_token": token,
                "missing_chunks": [],
            }
        )
    return result


def cleanup_expired_upload_sessions() -> int:
    """Remove expired reservations and ciphertext fragments.

    Committed files remain governed by file_registry retention. Their upload
    session metadata can expire without changing storage accounting.
    """

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT upload_id, owner_id, state, reserved_size, file_id "
        "FROM attachment_upload_sessions WHERE expires_at<=?",
        (_now(),),
    )
    rows = cursor.fetchall()
    removed = 0
    for row in rows:
        if row["state"] != "committed":
            cursor.execute(
                "UPDATE users SET storage_used=MAX(0, storage_used-?) "
                "WHERE user_id=?",
                (int(row["reserved_size"] or 0), row["owner_id"]),
            )
            if row["file_id"]:
                try:
                    (Path(UPLOAD_DIR).resolve() / row["file_id"]).unlink(missing_ok=True)
                except Exception:
                    pass
        cursor.execute(
            "DELETE FROM attachment_upload_chunks WHERE upload_id=?",
            (row["upload_id"],),
        )
        cursor.execute(
            "DELETE FROM attachment_upload_sessions WHERE upload_id=?",
            (row["upload_id"],),
        )
        shutil.rmtree(_safe_session_dir(row["upload_id"]), ignore_errors=True)
        removed += 1
    conn.commit()
    conn.close()
    return removed


def purge_user_upload_sessions(cursor, user_id: str) -> int:
    """Delete a user's uncommitted fragments during account wipe/reset."""

    cursor.execute(
        "SELECT upload_id, state, reserved_size, file_id "
        "FROM attachment_upload_sessions WHERE owner_id=?",
        (user_id,),
    )
    rows = cursor.fetchall()
    release = sum(
        int(row["reserved_size"] or 0)
        for row in rows
        if row["state"] != "committed"
    )
    for row in rows:
        if row["state"] != "committed" and row["file_id"]:
            try:
                (Path(UPLOAD_DIR).resolve() / row["file_id"]).unlink(missing_ok=True)
            except Exception:
                pass
        shutil.rmtree(_safe_session_dir(row["upload_id"]), ignore_errors=True)
        cursor.execute(
            "DELETE FROM attachment_upload_chunks WHERE upload_id=?",
            (row["upload_id"],),
        )
    cursor.execute(
        "DELETE FROM attachment_upload_sessions WHERE owner_id=?",
        (user_id,),
    )
    if release:
        cursor.execute(
            "UPDATE users SET storage_used=MAX(0, storage_used-?) WHERE user_id=?",
            (release, user_id),
        )
    return len(rows)


def build_resumable_upload_router(
    effective_storage_limit: Callable[[object, str], int],
) -> APIRouter:
    router = APIRouter(prefix="/storage")

    @router.post("/upload_sessions")
    async def create_upload_session(
        req: UploadSessionCreate,
        session_secret: str = Header(default=None, alias="session-secret"),
    ):
        request_id = req.client_request_id.strip()
        expected_ciphertext_hash = req.ciphertext_sha256.strip().lower()
        label = (req.storage_label or "").strip().lower()
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise HTTPException(status_code=400, detail="Invalid client request id")
        if not _HASH_RE.fullmatch(expected_ciphertext_hash):
            raise HTTPException(status_code=400, detail="Invalid ciphertext hash")
        if label not in {"", "temporary_image"}:
            raise HTTPException(status_code=400, detail="Invalid storage label")
        if req.encrypted_size <= 0 or req.encrypted_size > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Invalid encrypted size")
        if req.chunk_count <= 0 or req.chunk_count > MAX_UPLOAD_CHUNKS:
            raise HTTPException(status_code=400, detail="Invalid chunk count")
        if req.chunk_count > req.encrypted_size:
            raise HTTPException(status_code=400, detail="Invalid chunk layout")

        cleanup_expired_upload_sessions()
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            user = _authenticated_user(cursor, session_secret)
            user_id = user["user_id"]
            cursor.execute(
                "SELECT * FROM attachment_upload_sessions "
                "WHERE owner_id=? AND client_request_id=?",
                (user_id, request_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    int(existing["encrypted_size"]) != req.encrypted_size
                    or int(existing["chunk_count"]) != req.chunk_count
                    or existing["ciphertext_sha256"] != expected_ciphertext_hash
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Upload request id already has a different manifest",
                    )
                result = _session_payload(cursor, existing)
                conn.commit()
                return result
            cursor.execute(
                "SELECT COUNT(*) AS count FROM attachment_upload_sessions "
                "WHERE owner_id=? AND state!='committed' AND expires_at>?",
                (user_id, _now()),
            )
            if int(cursor.fetchone()["count"] or 0) >= MAX_CONCURRENT_UPLOAD_SESSIONS:
                raise HTTPException(status_code=429, detail="Too many upload sessions")
            current_used = int(user["storage_used"] or 0)
            storage_limit = int(effective_storage_limit(cursor, user_id))
            if current_used + req.encrypted_size > storage_limit:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "storage_quota_exceeded",
                        "message": "Storage limit exceeded",
                        "used_bytes": current_used,
                        "limit_bytes": storage_limit,
                        "requested_bytes": req.encrypted_size,
                    },
                )
            upload_id = str(uuid.uuid4())
            expires_at = _now() + timedelta(hours=UPLOAD_SESSION_TTL_HOURS)
            cursor.execute(
                "INSERT INTO attachment_upload_sessions "
                "(upload_id, owner_id, client_request_id, state, encrypted_size, "
                "chunk_count, storage_label, ciphertext_sha256, reserved_size, expires_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                (
                    upload_id,
                    user_id,
                    request_id,
                    req.encrypted_size,
                    req.chunk_count,
                    label,
                    expected_ciphertext_hash,
                    req.encrypted_size,
                    expires_at,
                ),
            )
            cursor.execute(
                "UPDATE users SET storage_used=storage_used+? WHERE user_id=?",
                (req.encrypted_size, user_id),
            )
            cursor.execute(
                "SELECT * FROM attachment_upload_sessions WHERE upload_id=?",
                (upload_id,),
            )
            result = _session_payload(cursor, cursor.fetchone())
            conn.commit()
            _safe_session_dir(upload_id).mkdir(parents=True, exist_ok=True)
            return result
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail="Upload session creation failed") from exc
        finally:
            conn.close()

    @router.get("/upload_sessions/{upload_id}")
    async def get_upload_session(
        upload_id: str,
        session_secret: str = Header(default=None, alias="session-secret"),
    ):
        _safe_session_dir(upload_id)
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = _authenticated_user(cursor, session_secret)
            cursor.execute(
                "SELECT * FROM attachment_upload_sessions "
                "WHERE upload_id=? AND owner_id=?",
                (upload_id, user["user_id"]),
            )
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Upload session not found")
            if datetime.fromisoformat(str(row["expires_at"])) <= _now():
                raise HTTPException(status_code=410, detail="Upload session expired")
            result = _session_payload(cursor, row)
            conn.commit()
            return result
        finally:
            conn.close()

    @router.put("/upload_sessions/{upload_id}/chunks/{chunk_index}")
    async def put_upload_chunk(
        upload_id: str,
        chunk_index: int,
        request: Request,
        session_secret: str = Header(default=None, alias="session-secret"),
        chunk_sha256: str = Header(default=None, alias="x-chunk-sha256"),
    ):
        session_dir = _safe_session_dir(upload_id)
        claimed_hash = (chunk_sha256 or "").strip().lower()
        if not _HASH_RE.fullmatch(claimed_hash):
            raise HTTPException(status_code=400, detail="Invalid chunk hash")
        content_length = request.headers.get("content-length")
        if content_length is None:
            raise HTTPException(status_code=411, detail="Content-Length required")
        try:
            expected_size = int(content_length)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if expected_size <= 0 or expected_size > MAX_UPLOAD_CHUNK_BYTES:
            raise HTTPException(status_code=413, detail="Chunk too large")

        conn = get_db()
        cursor = conn.cursor()
        try:
            user = _authenticated_user(cursor, session_secret)
        except Exception:
            conn.close()
            raise
        cursor.execute(
            "SELECT * FROM attachment_upload_sessions "
            "WHERE upload_id=? AND owner_id=?",
            (upload_id, user["user_id"]),
        )
        session = cursor.fetchone()
        conn.close()
        if session is None:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["state"] != "pending":
            raise HTTPException(status_code=409, detail="Upload session is not writable")
        if datetime.fromisoformat(str(session["expires_at"])) <= _now():
            raise HTTPException(status_code=410, detail="Upload session expired")
        if chunk_index < 0 or chunk_index >= int(session["chunk_count"]):
            raise HTTPException(status_code=400, detail="Chunk index out of range")

        session_dir.mkdir(parents=True, exist_ok=True)
        temporary = session_dir / f".{chunk_index}.{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as sink:
                async for block in request.stream():
                    if not block:
                        continue
                    written += len(block)
                    if written > expected_size or written > MAX_UPLOAD_CHUNK_BYTES:
                        raise HTTPException(status_code=413, detail="Chunk exceeded declared size")
                    digest.update(block)
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
            actual_hash = digest.hexdigest()
            if written != expected_size or actual_hash != claimed_hash:
                raise HTTPException(status_code=400, detail="Chunk integrity mismatch")
            final_chunk = _chunk_path(upload_id, chunk_index)
            created = False
            try:
                os.link(temporary, final_chunk)
                created = True
            except FileExistsError:
                existing_size, existing_hash = _hash_file(final_chunk)
                if existing_size != written or existing_hash != actual_hash:
                    raise HTTPException(status_code=409, detail="Chunk index already has different ciphertext")

            conn = get_db()
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                current_user = _authenticated_user(cursor, session_secret)
                cursor.execute(
                    "SELECT state, chunk_count FROM attachment_upload_sessions "
                    "WHERE upload_id=? AND owner_id=?",
                    (upload_id, current_user["user_id"]),
                )
                current = cursor.fetchone()
                if current is None or current["state"] != "pending":
                    raise HTTPException(status_code=409, detail="Upload session is not writable")
                cursor.execute(
                    "INSERT OR IGNORE INTO attachment_upload_chunks "
                    "(upload_id, chunk_index, chunk_size, chunk_sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (upload_id, chunk_index, written, actual_hash),
                )
                cursor.execute(
                    "SELECT chunk_size, chunk_sha256 FROM attachment_upload_chunks "
                    "WHERE upload_id=? AND chunk_index=?",
                    (upload_id, chunk_index),
                )
                stored = cursor.fetchone()
                if (
                    stored is None
                    or int(stored["chunk_size"]) != written
                    or stored["chunk_sha256"] != actual_hash
                ):
                    raise HTTPException(status_code=409, detail="Chunk metadata conflict")
                cursor.execute(
                    "UPDATE attachment_upload_sessions SET updated_at=CURRENT_TIMESTAMP "
                    "WHERE upload_id=?",
                    (upload_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                if created:
                    final_chunk.unlink(missing_ok=True)
                raise
            finally:
                conn.close()
            return {
                "status": "ok",
                "upload_id": upload_id,
                "chunk_index": chunk_index,
                "chunk_size": written,
                "chunk_sha256": actual_hash,
                "duplicate": not created,
            }
        finally:
            temporary.unlink(missing_ok=True)

    @router.post("/upload_sessions/{upload_id}/commit")
    async def commit_upload_session(
        upload_id: str,
        req: UploadSessionCommit,
        session_secret: str = Header(default=None, alias="session-secret"),
    ):
        session_dir = _safe_session_dir(upload_id)
        overall_hash = req.ciphertext_sha256.strip().lower()
        if not _HASH_RE.fullmatch(overall_hash):
            raise HTTPException(status_code=400, detail="Invalid ciphertext hash")

        conn = get_db()
        cursor = conn.cursor()
        try:
            user = _authenticated_user(cursor, session_secret)
        except Exception:
            conn.close()
            raise
        cursor.execute(
            "SELECT * FROM attachment_upload_sessions "
            "WHERE upload_id=? AND owner_id=?",
            (upload_id, user["user_id"]),
        )
        session = cursor.fetchone()
        if session is None:
            conn.close()
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["state"] == "committed":
            result = _session_payload(cursor, session, include_missing=False)
            conn.commit()
            conn.close()
            return result
        if datetime.fromisoformat(str(session["expires_at"])) <= _now():
            conn.close()
            raise HTTPException(status_code=410, detail="Upload session expired")
        if session["state"] not in {"pending", "committing"}:
            conn.close()
            raise HTTPException(status_code=409, detail="Upload session cannot commit")
        if session["ciphertext_sha256"] != overall_hash:
            conn.close()
            raise HTTPException(status_code=409, detail="Commit ciphertext hash changed")
        stale_commit = False
        if session["state"] == "committing":
            updated_at = datetime.fromisoformat(str(session["updated_at"]))
            stale_commit = updated_at <= _now() - timedelta(minutes=_COMMIT_LEASE_MINUTES)
            if not stale_commit:
                conn.close()
                raise HTTPException(status_code=409, detail="Upload commit is already in progress")
        chunk_count = int(session["chunk_count"])
        if len(req.chunks) != chunk_count:
            conn.close()
            raise HTTPException(status_code=409, detail="Manifest chunk count mismatch")
        manifest = {}
        for chunk in req.chunks:
            chunk_hash = chunk.chunk_sha256.strip().lower()
            if (
                chunk.chunk_index < 0
                or chunk.chunk_index >= chunk_count
                or chunk.chunk_index in manifest
                or chunk.chunk_size <= 0
                or chunk.chunk_size > MAX_UPLOAD_CHUNK_BYTES
                or not _HASH_RE.fullmatch(chunk_hash)
            ):
                conn.close()
                raise HTTPException(status_code=400, detail="Invalid chunk manifest")
            manifest[chunk.chunk_index] = (chunk.chunk_size, chunk_hash)
        cursor.execute(
            "SELECT chunk_index, chunk_size, chunk_sha256 "
            "FROM attachment_upload_chunks WHERE upload_id=? ORDER BY chunk_index",
            (upload_id,),
        )
        stored_chunks = cursor.fetchall()
        if len(stored_chunks) != chunk_count:
            conn.close()
            raise HTTPException(status_code=409, detail="Upload has missing chunks")
        for stored in stored_chunks:
            expected = manifest.get(int(stored["chunk_index"]))
            if expected != (int(stored["chunk_size"]), stored["chunk_sha256"]):
                conn.close()
                raise HTTPException(status_code=409, detail="Manifest does not match uploaded chunks")
        if sum(size for size, _ in manifest.values()) != int(session["encrypted_size"]):
            conn.close()
            raise HTTPException(status_code=409, detail="Manifest size mismatch")
        file_id = session["file_id"] or str(uuid.uuid4())
        if session["state"] == "committing" and (
            session["ciphertext_sha256"] != overall_hash
            or session["file_id"] != file_id
        ):
            conn.close()
            raise HTTPException(status_code=409, detail="Commit manifest changed")
        cursor.execute("BEGIN IMMEDIATE")
        if stale_commit:
            cursor.execute(
                "UPDATE attachment_upload_sessions SET file_id=?, "
                "ciphertext_sha256=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE upload_id=? AND owner_id=? AND state='committing' "
                "AND updated_at=?",
                (
                    file_id,
                    overall_hash,
                    upload_id,
                    user["user_id"],
                    session["updated_at"],
                ),
            )
        else:
            cursor.execute(
                "UPDATE attachment_upload_sessions SET state='committing', file_id=?, "
                "ciphertext_sha256=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE upload_id=? AND owner_id=? AND state='pending'",
                (file_id, overall_hash, upload_id, user["user_id"]),
            )
        if cursor.rowcount != 1:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=409, detail="Upload commit state changed")
        conn.commit()
        conn.close()

        assembled = session_dir / f".assembled.{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        assembled_size = 0
        try:
            with assembled.open("xb") as sink:
                for index in range(chunk_count):
                    path = _chunk_path(upload_id, index)
                    if not path.is_file():
                        raise HTTPException(status_code=409, detail="Upload has missing chunk files")
                    _validate_v2_transport_chunk(
                        path,
                        index,
                        index == chunk_count - 1,
                    )
                    chunk_digest = hashlib.sha256()
                    actual_size = 0
                    with path.open("rb") as source:
                        while True:
                            block = source.read(_COPY_BUFFER_BYTES)
                            if not block:
                                break
                            sink.write(block)
                            chunk_digest.update(block)
                            digest.update(block)
                            actual_size += len(block)
                            assembled_size += len(block)
                    if (actual_size, chunk_digest.hexdigest()) != manifest[index]:
                        raise HTTPException(status_code=409, detail="Chunk file integrity mismatch")
                sink.flush()
                os.fsync(sink.fileno())
            if (
                assembled_size != int(session["encrypted_size"])
                or digest.hexdigest() != overall_hash
            ):
                raise HTTPException(status_code=409, detail="Committed ciphertext integrity mismatch")
            final_path = Path(UPLOAD_DIR).resolve() / file_id
            try:
                os.link(assembled, final_path)
                assembled.unlink(missing_ok=True)
            except FileExistsError:
                final_size, final_hash = _hash_file(final_path)
                if final_size != assembled_size or final_hash != overall_hash:
                    raise HTTPException(status_code=500, detail="Final file collision")
                assembled.unlink(missing_ok=True)

            conn = get_db()
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    "SELECT * FROM attachment_upload_sessions "
                    "WHERE upload_id=? AND owner_id=?",
                    (upload_id, user["user_id"]),
                )
                current = cursor.fetchone()
                if current is None or current["state"] not in {"committing", "committed"}:
                    raise RuntimeError("Upload reservation disappeared")
                if current["state"] != "committed":
                    filename = (
                        "Temporary Image"
                        if (current["storage_label"] or "") == "temporary_image"
                        else "Unknown Encrypted File"
                    )
                    file_expiry = _now() + timedelta(days=FILE_RETENTION_DAYS)
                    cursor.execute(
                        "INSERT INTO file_registry "
                        "(file_id, owner_id, filename, file_size, expires_at, status, content_sha256) "
                        "VALUES (?, ?, ?, ?, ?, 'ready', ?)",
                        (
                            file_id,
                            user["user_id"],
                            filename,
                            assembled_size,
                            file_expiry,
                            overall_hash,
                        ),
                    )
                    cursor.execute(
                        "UPDATE attachment_upload_sessions SET state='committed', "
                        "committed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP, "
                        "expires_at=? WHERE upload_id=?",
                        (file_expiry, upload_id),
                    )
                    token = secrets.token_urlsafe(32)
                    cursor.execute(
                        "INSERT INTO file_download_tokens (token, file_id, expires_at) "
                        "VALUES (?, ?, ?)",
                        (
                            token,
                            file_id,
                            _now() + timedelta(minutes=FILE_TOKEN_TTL_MINUTES),
                        ),
                    )
                cursor.execute(
                    "SELECT * FROM attachment_upload_sessions WHERE upload_id=?",
                    (upload_id,),
                )
                result = _session_payload(cursor, cursor.fetchone(), include_missing=False)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            shutil.rmtree(session_dir, ignore_errors=True)
            return result
        except HTTPException as exc:
            if exc.status_code == 409:
                reset_conn = get_db()
                reset_cursor = reset_conn.cursor()
                try:
                    reset_cursor.execute("BEGIN IMMEDIATE")
                    reset_cursor.execute(
                        "UPDATE attachment_upload_sessions SET state='pending', "
                        "file_id=NULL, "
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE upload_id=? AND owner_id=? AND state='committing'",
                        (upload_id, user["user_id"]),
                    )
                    did_reset = reset_cursor.rowcount == 1
                    if did_reset:
                        reset_cursor.execute(
                            "DELETE FROM attachment_upload_chunks WHERE upload_id=?",
                            (upload_id,),
                        )
                    reset_conn.commit()
                    if did_reset:
                        for index in range(chunk_count):
                            _chunk_path(upload_id, index).unlink(missing_ok=True)
                except Exception:
                    reset_conn.rollback()
                finally:
                    reset_conn.close()
            raise
        finally:
            assembled.unlink(missing_ok=True)

    @router.delete("/upload_sessions/{upload_id}")
    async def cancel_upload_session(
        upload_id: str,
        session_secret: str = Header(default=None, alias="session-secret"),
    ):
        session_dir = _safe_session_dir(upload_id)
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            user = _authenticated_user(cursor, session_secret)
            cursor.execute(
                "SELECT state, reserved_size, file_id FROM attachment_upload_sessions "
                "WHERE upload_id=? AND owner_id=?",
                (upload_id, user["user_id"]),
            )
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Upload session not found")
            if row["state"] == "committed":
                raise HTTPException(status_code=409, detail="Committed files use the delete endpoint")
            cursor.execute(
                "UPDATE users SET storage_used=MAX(0, storage_used-?) WHERE user_id=?",
                (int(row["reserved_size"] or 0), user["user_id"]),
            )
            cursor.execute("DELETE FROM attachment_upload_chunks WHERE upload_id=?", (upload_id,))
            cursor.execute("DELETE FROM attachment_upload_sessions WHERE upload_id=?", (upload_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        shutil.rmtree(session_dir, ignore_errors=True)
        return {"status": "ok", "upload_id": upload_id}

    return router
