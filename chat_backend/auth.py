import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import pyotp
from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from chat_backend.database import get_db
from chat_backend.connection_registry import connection_registry
from chat_backend import storm_guard
from chat_backend.settings import (
    ADMIN_DOC_USER,
    ADMIN_IP_ALLOWLIST,
    ADMIN_PASS_HASH,
    ADMIN_TOTP_SECRET,
    REGISTER_RATE_LIMIT_MAX,
    REGISTER_RATE_LIMIT_WINDOW,
    SESSION_CACHE_TTL,
    SESSION_EXPIRY_DAYS,
)


security = HTTPBasic()
ip_rate_limits: dict[str, list[float]] = {}
ip_register_rate_limits: dict[str, list[float]] = {}
lookup_rate_limits: dict[str, list[float]] = {}
admin_auth_failure_limits: dict[str, list[float]] = {}
last_rate_limit_cleanup = time.time()
RATE_LIMIT_CLEANUP_INTERVAL = 3600
LOOKUP_RATE_LIMIT_MAX = int(os.getenv("LOOKUP_RATE_LIMIT_MAX", "90"))
LOOKUP_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("LOOKUP_RATE_LIMIT_WINDOW_SECONDS", "30"))
ADMIN_AUTH_FAILURE_LIMIT = int(os.getenv("ADMIN_AUTH_FAILURE_LIMIT", "8"))
ADMIN_AUTH_FAILURE_WINDOW_SECONDS = int(os.getenv("ADMIN_AUTH_FAILURE_WINDOW_SECONDS", "300"))
NONCE_CACHE: dict[str, float] = {}
MAX_NONCE_CACHE_SIZE = 500
_sessions_last_seen_checked_at = 0.0
_session_cache: dict[str, tuple[str, float]] = {}


def _session_cache_get(token: str) -> Optional[str]:
    entry = _session_cache.get(token)
    if entry is None:
        return None
    user_id, expires = entry
    if time.time() > expires:
        _session_cache.pop(token, None)
        return None
    return user_id


def _session_cache_set(token: str, user_id: str) -> None:
    if len(_session_cache) > 5000:
        now = time.time()
        stale = [k for k, (_, exp) in _session_cache.items() if exp < now]
        if stale:
            for k in stale:
                del _session_cache[k]
        else:
            for k in list(_session_cache.keys())[:500]:
                del _session_cache[k]
    _session_cache[token] = (user_id, time.time() + SESSION_CACHE_TTL)


def session_cache_invalidate(token: str) -> None:
    """Invalidate a cached session entry. Call on logout / kill_device."""
    _session_cache.pop(token, None)


def _parse_db_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _ensure_session_last_seen_column() -> None:
    global _sessions_last_seen_checked_at
    now = time.time()
    if now - _sessions_last_seen_checked_at < 300:
        return
    _sessions_last_seen_checked_at = now

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN last_seen DATETIME")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_real_ip(request: Request) -> str:
    ip = request.headers.get("X-Real-IP")
    if ip:
        return ip

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    return request.client.host


def _get_request_ip(request: Request) -> str:
    return get_real_ip(request)


def check_admin_ip(request: Request) -> bool:
    if not ADMIN_IP_ALLOWLIST:
        return True
    client_ip = _get_request_ip(request)
    if client_ip not in ADMIN_IP_ALLOWLIST:
        raise HTTPException(status_code=403, detail="IP not allowed")
    return True


def _should_send_basic_challenge(request: Request) -> bool:
    path = (request.url.path or "").rstrip("/")
    return path in ("/docs", "/openapi.json")


def _raise_admin_unauthorized(request: Request, reason: str = "unauthorized"):
    client_ip = "unknown"
    try:
        client_ip = _get_request_ip(request)
        print(f"[WARN] Admin auth failed: {reason} (path={request.url.path}, ip={client_ip})")
    except Exception:
        pass
    _record_admin_auth_failure(client_ip)
    headers = {"WWW-Authenticate": "Basic"} if _should_send_basic_challenge(request) else None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers=headers,
    )


def _record_admin_auth_failure(client_ip: str) -> None:
    now = time.time()
    bucket = admin_auth_failure_limits.setdefault(client_ip, [])
    admin_auth_failure_limits[client_ip] = [
        timestamp for timestamp in bucket if now - timestamp < ADMIN_AUTH_FAILURE_WINDOW_SECONDS
    ]
    admin_auth_failure_limits[client_ip].append(now)
    if len(admin_auth_failure_limits[client_ip]) > ADMIN_AUTH_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many admin authentication attempts.")


def verify_docs_access(
    request: Request,
    credentials: HTTPBasicCredentials = Security(security),
):
    check_rate_limit(request, 5)
    check_admin_ip(request)

    provided_user = (credentials.username or "").strip()
    if provided_user != ADMIN_DOC_USER:
        _raise_admin_unauthorized(request, "username_mismatch")

    if not ADMIN_PASS_HASH:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin password hash not configured",
        )

    try:
        if not bcrypt.checkpw(credentials.password.encode(), ADMIN_PASS_HASH.encode()):
            _raise_admin_unauthorized(request, "password_mismatch")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin password hash invalid",
        )

    return True


def verify_admin_access(
    request: Request,
    credentials: HTTPBasicCredentials = Security(security),
    x_admin_totp: Optional[str] = Header(default=None, alias="x-admin-totp"),
):
    verify_docs_access(request, credentials)

    if ADMIN_TOTP_SECRET:
        if not x_admin_totp:
            try:
                client_ip = get_real_ip(request)
                print(f"[WARN] Admin auth failed: missing x-admin-totp (path={request.url.path}, ip={client_ip})")
            except Exception:
                client_ip = "unknown"
            _record_admin_auth_failure(client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin TOTP required",
            )
        try:
            totp = pyotp.TOTP(ADMIN_TOTP_SECRET)
            if not totp.verify(str(x_admin_totp).strip(), valid_window=1):
                try:
                    client_ip = get_real_ip(request)
                    print(f"[WARN] Admin auth failed: invalid x-admin-totp (path={request.url.path}, ip={client_ip})")
                except Exception:
                    client_ip = "unknown"
                _record_admin_auth_failure(client_ip)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid admin TOTP",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Admin TOTP configuration invalid",
            )

    return True


def validate_session_token(token: str) -> tuple[bool, str]:
    # Guard: soft-deleted sessions have session_secret='' — reject empty tokens
    # before any DB lookup so they can never match a soft-deleted row.
    if not token or not token.strip():
        return False, "Invalid session"
    storm_guard.maybe_heal_global(get_db)
    # ⭐ PERF FIX: In-process session cache avoids DB round-trip on burst calls.
    cached_uid = _session_cache_get(token)
    if cached_uid is not None:
        return True, cached_uid

    _ensure_session_last_seen_column()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, created_at, grace_period_expires, last_seen "
        "FROM sessions WHERE session_secret=?",
        (token,),
    )
    sess = cursor.fetchone()
    conn.close()

    if not sess:
        return False, "Invalid session"

    created_at = _parse_db_datetime(sess["created_at"])
    last_seen = _parse_db_datetime(sess["last_seen"])
    grace_expires = _parse_db_datetime(sess["grace_period_expires"])
    session_reference = last_seen or created_at or datetime.now()
    expiry_threshold = datetime.now() - timedelta(days=SESSION_EXPIRY_DAYS)

    if session_reference < expiry_threshold:
        user_id = sess["user_id"]
        has_active_ws = connection_registry.has(user_id, token) or bool(
            connection_registry.get_user_sessions(user_id)
        )
        if has_active_ws:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET last_seen = CURRENT_TIMESTAMP WHERE session_secret=?",
                (token,),
            )
            conn.commit()
            conn.close()
            _session_cache_set(token, user_id)
            return True, user_id

        if grace_expires and grace_expires > datetime.now():
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET last_seen = CURRENT_TIMESTAMP WHERE session_secret=?",
                (token,),
            )
            conn.commit()
            conn.close()
            _session_cache_set(token, user_id)
            return True, user_id

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE session_secret=?", (token,))
        conn.commit()
        conn.close()
        return False, "Session expired, please login again"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET last_seen = CURRENT_TIMESTAMP WHERE session_secret=?",
        (token,),
    )
    conn.commit()
    conn.close()

    global last_rate_limit_cleanup
    now = time.time()
    if now - last_rate_limit_cleanup > 3600:
        last_rate_limit_cleanup = now
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, session_secret, created_at, last_seen FROM sessions")
        rows = cursor.fetchall()
        stale_sessions: list[str] = []
        for row in rows:
            row_user = row["user_id"]
            row_secret = row["session_secret"]
            row_created = _parse_db_datetime(row["created_at"])
            row_seen = _parse_db_datetime(row["last_seen"])
            row_ref = row_seen or row_created or datetime.now()
            if row_ref >= expiry_threshold:
                continue
            if connection_registry.has(row_user, row_secret):
                continue
            if connection_registry.get_user_sessions(row_user):
                continue
            stale_sessions.append(row_secret)

        if stale_sessions:
            cursor.executemany(
                "DELETE FROM sessions WHERE session_secret=?",
                [(secret,) for secret in stale_sessions],
            )
        conn.commit()
        conn.close()

    _session_cache_set(token, sess["user_id"])
    storm_guard.maybe_heal_user_state(get_db, sess["user_id"])
    return True, sess["user_id"]


def verify_ephemeral_token(from_id: str, nonce: str, timestamp_str: str, token: str, session_secret: str = None) -> bool:
    try:
        msg_timestamp = int(timestamp_str)
        now_timestamp = int(datetime.now().timestamp())
        if abs(now_timestamp - msg_timestamp) > 300:
            return False

        if nonce in NONCE_CACHE:
            return False

        if session_secret is None:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT session_secret FROM sessions WHERE user_id=?", (from_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return False
            session_secret = row["session_secret"]

        message = f"{nonce}:{timestamp_str}:{from_id}"
        expected_token = hmac.new(
            session_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_token, token):
            return False

        NONCE_CACHE[nonce] = time.time()
        now = time.time()
        old_nonces = [key for key, cached_at in NONCE_CACHE.items() if now - cached_at > 600]
        for old_nonce in old_nonces:
            del NONCE_CACHE[old_nonce]

        # Hard size cap: time-based eviction alone lets the cache grow unbounded
        # under a high-throughput sender within the 600s window. Evict the
        # oldest entries when over MAX_NONCE_CACHE_SIZE.
        if len(NONCE_CACHE) > MAX_NONCE_CACHE_SIZE:
            for stale_nonce, _ in sorted(NONCE_CACHE.items(), key=lambda kv: kv[1])[
                : len(NONCE_CACHE) - MAX_NONCE_CACHE_SIZE
            ]:
                NONCE_CACHE.pop(stale_nonce, None)

        return True
    except Exception:
        return False


def check_rate_limit(request: Request, limit: int):
    global last_rate_limit_cleanup
    client_ip = get_real_ip(request)
    now = time.time()

    if now - last_rate_limit_cleanup > RATE_LIMIT_CLEANUP_INTERVAL:
        ip_rate_limits.clear()
        ip_register_rate_limits.clear()
        lookup_rate_limits.clear()
        admin_auth_failure_limits.clear()
        last_rate_limit_cleanup = now

    if client_ip not in ip_rate_limits:
        ip_rate_limits[client_ip] = []

    ip_rate_limits[client_ip] = [timestamp for timestamp in ip_rate_limits[client_ip] if now - timestamp < 60]
    if len(ip_rate_limits[client_ip]) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests.")

    ip_rate_limits[client_ip].append(now)


def _resolve_lookup_bucket_key(request: Request, session_secret: Optional[str] = None) -> str:
    token = str(session_secret or request.headers.get("session-secret") or "").strip()
    if token:
        cached_user = _session_cache_get(token)
        if cached_user:
            return f"user:{cached_user}"

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT user_id FROM sessions WHERE session_secret=? LIMIT 1",
                (token,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row and row["user_id"]:
            user_id = str(row["user_id"])
            _session_cache_set(token, user_id)
            return f"user:{user_id}"

    return f"ip:{get_real_ip(request)}"


def check_lookup_rate_limit(request: Request, session_secret: Optional[str] = None):
    global last_rate_limit_cleanup
    now = time.time()

    if now - last_rate_limit_cleanup > RATE_LIMIT_CLEANUP_INTERVAL:
        ip_rate_limits.clear()
        ip_register_rate_limits.clear()
        lookup_rate_limits.clear()
        admin_auth_failure_limits.clear()
        last_rate_limit_cleanup = now

    bucket_key = _resolve_lookup_bucket_key(request, session_secret=session_secret)
    bucket = lookup_rate_limits.setdefault(bucket_key, [])
    lookup_rate_limits[bucket_key] = [
        timestamp for timestamp in bucket if now - timestamp < LOOKUP_RATE_LIMIT_WINDOW_SECONDS
    ]

    if len(lookup_rate_limits[bucket_key]) >= LOOKUP_RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many lookup requests.")

    lookup_rate_limits[bucket_key].append(now)


def check_register_rate_limit(request: Request):
    client_ip = get_real_ip(request)
    now = time.time()
    if client_ip not in ip_register_rate_limits:
        ip_register_rate_limits[client_ip] = []
    ip_register_rate_limits[client_ip] = [
        timestamp for timestamp in ip_register_rate_limits[client_ip]
        if now - timestamp < REGISTER_RATE_LIMIT_WINDOW
    ]
    if len(ip_register_rate_limits[client_ip]) >= REGISTER_RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many registration requests.")
    ip_register_rate_limits[client_ip].append(now)


