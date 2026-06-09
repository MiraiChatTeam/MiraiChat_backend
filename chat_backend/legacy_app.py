from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Header, BackgroundTasks, Depends, HTTPException, status, Request, Security
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Optional, Literal
import sqlite3, secrets, json, bcrypt, uuid, os, shutil, hmac, hashlib, time, base64
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
import pyotp
import requests
import subprocess
import re
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
import hmac
import hashlib
import time

from chat_backend.auth import (
    check_admin_ip,
    check_lookup_rate_limit,
    check_rate_limit,
    check_register_rate_limit,
    get_real_ip,
    security,
    session_cache_invalidate,
    validate_session_token,
    verify_admin_access,
    verify_docs_access,
    verify_ephemeral_token,
)
from chat_backend.connection_registry import connection_registry
from chat_backend.database import get_db, initialize_database
from chat_backend.fanout_bridge import fanout_bridge
from chat_backend.push import push_delivery_status, should_send_push_for_message
from chat_backend.push_queue import enqueue_push, start_push_workers, stop_push_workers
from chat_backend import storm_guard
from chat_backend import latency_tracker
from chat_backend import summary_export
from chat_backend.unread_state import (
    get_user_unread_count,
    mark_read_up_to,
    upsert_unread_message,
)
from chat_backend.settings import (
    ADMIN_DOC_USER,
    ADMIN_IP_ALLOWLIST,
    ADMIN_PASS_HASH,
    ADMIN_TOTP_SECRET,
    ALLOW_EXTERNAL_DONATIONS,
    API_PUBLIC_BASE_URL,
    APPLE_IAP_BUNDLE_ID,
    APPLE_IAP_SHARED_SECRET,
    CHINA_FCM_POLICY,
    DEFAULT_REGION,
    DEFAULT_STORAGE_LIMIT,
    DONATION_BADGE_DAYS,
    DONATION_MONTHLY_PRIORITY_USERS,
    DONATION_MONTHLY_STORAGE_LIMIT,
    FILE_RETENTION_DAYS,
    FILE_TOKEN_TTL_MINUTES,
    GOOGLE_PLAY_PACKAGE_NAME,
    GOOGLE_PLAY_SERVICE_ACCOUNT_JSON,
    IS_PUBLIC_HUB,
    OFFLINE_MSG_RETENTION_DAYS,
    OLD_SIGNING_KEY_FILE,
    PAYMENT_MODE,
    PENDING_LEASE_SECONDS,
    PRESENCE_BACKEND,
    PRESENCE_REDIS_URL,
    PRESENCE_TTL_SECONDS,
    PIN_SYNC_AUTO_DEFAULT,
    PIN_SYNC_FORCE_MANUAL,
    PRIVATE_SERVER_STORAGE_LIMIT,
    PUBLIC_HUB_URL,
    REGISTRATION_KEY,
    REGION_POLICY_MODE,
    REGISTER_RATE_LIMIT_MAX,
    REGISTER_RATE_LIMIT_WINDOW,
    ROTATION_GRACE_PERIOD_DAYS,
    ROTATION_INTERVAL_DAYS,
    SERVER_IDENTITY_SALT,
    SESSION_EXPIRY_DAYS,
    SIGNING_KEY_FILE,
    STRIPE_MERCHANT_COUNTRY,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    TRANSPORT_PIN_HINTS,
    UPLOAD_DIR,
    WELCOME_MESSAGES_FILE,
    WS_CONNECTION_MODE,
    WS_DIRECT_PUBLIC_URL,
    WS_TUNNEL_BASE_URL,
    _build_allowed_ws_hosts,
    _build_allowed_ws_origins,
    _normalize_ws_host,
    _normalize_ws_origin,
    ensure_upload_dir,
)

# Temporary in-memory storage for device linking (Handshake)
linking_blobs = {}

# Freshly registered accounts may bootstrap a first main-device session once.
_fresh_registration_main_bootstrap: dict[str, float] = {}

# --- Master Signing Key for Sealed Sender ---
# Path may be provided via env; default kept for compatibility
SIGNING_KEY_FILE = os.getenv("SIGNING_KEY_FILE", "server_signing_key.pem")
if os.path.exists(SIGNING_KEY_FILE):
    try:
        with open(SIGNING_KEY_FILE, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        _private_key = ed25519.Ed25519PrivateKey.generate()
        with open(SIGNING_KEY_FILE, "wb") as f:
            f.write(_private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
else:
    _private_key = ed25519.Ed25519PrivateKey.generate()
    with open(SIGNING_KEY_FILE, "wb") as f:
        f.write(_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))

_server_public_key = _private_key.public_key()

# Old signing key retained during grace period after rotation
# _old_private_key is set in-process after _load_grace_period_state(); do not initialise from file here.
_old_private_key: Optional[ed25519.Ed25519PrivateKey] = None
_old_public_key = None
_grace_period_ends_at: Optional[float] = None

# WELCOME MESSAGE CONFIGURATION
# Path to a JSON file containing per-language welcome messages.
# The file is written by the backend GUI and read at runtime by the register endpoint.
_WELCOME_MESSAGES_DEFAULT: dict = {
    "en": (
        "\U0001f389 Welcome to MiraiChat! We're so excited to have you here as one of our earliest testers. "
        "If you run into any bugs or hiccups, please share a screenshot with us \u2014 "
        "your feedback means the world and helps us make MiraiChat even better. "
        "Thank you so much for being part of this journey! \U0001f499"
    ),
    "zh_hans": (
        "\U0001f389 欢迎来到 MiraiChat\uff01非常高兴你成为我们最早的测试用户之一。"
        "如果遇到任何问题或 Bug\uff0c欢迎截图并反馈给我们 \u2014 "
        "你的每一条反馈对我们都至关重要\uff0c帮助我们把产品做得更好。"
        "非常感谢你的支持与参与\uff01\U0001f499"
    ),
    "zh_hant": (
        "\U0001f389 歡迎來到 MiraiChat\uff01非常高興您成為我們最早的測試用戶之一。"
        "如果遇到任何問題或 Bug\uff0c歡迎截圖並回報給我們 \u2014 "
        "您的每一份意見對我們都非常寶貴\uff0c幫助我們把產品做得更好。"
        "非常感謝您的支持與參與\uff01\U0001f499"
    ),
    "ja": (
        "\U0001f389 MiraiChat へようこそ\uff01最初のテストメンバーのおひとりとして参加いただき、本当にありがとうございます。"
        "もし不具合を見つけたら、ぜひスクリーンショット付きで教えてください \u2014 "
        "みなさんのフィードバックが私たちにとって何より大切です。"
        "一緒により良いものを作っていきましょう\uff01\U0001f499"
    ),
}

_push_skip_last_log_at = {}
_PUSH_SKIP_LOG_THROTTLE_SECONDS = 15
GENERIC_PUSH_TITLE = os.getenv("GENERIC_PUSH_TITLE", "MiraiChat")
GENERIC_PUSH_BODY = os.getenv("GENERIC_PUSH_BODY", "A message from MiraiChat")
_PENDING_DELIVERY_TIMEOUT_SECONDS = int(os.getenv("PENDING_DELIVERY_TIMEOUT_SECONDS", "8"))
PENDING_FETCH_MIN_INTERVAL_SECONDS = float(
    os.getenv("PENDING_FETCH_MIN_INTERVAL_SECONDS", "5.0")
)
DONATION_STATUS_DEDUP_SECONDS = float(os.getenv("DONATION_STATUS_DEDUP_SECONDS", "60"))
PIN_LIST_DEDUP_SECONDS = float(os.getenv("PIN_LIST_DEDUP_SECONDS", "30"))
REQUEST_GUARD_CLEANUP_INTERVAL = 600
_pending_fetch_last_at: dict[str, float] = {}
_donation_status_last: dict[str, float] = {}
_donation_status_cache: dict[str, dict] = {}
_pin_list_last: dict[str, float] = {}
_request_guard_last_cleanup = time.time()
_pending_delivery_lock = asyncio.Lock()
_pending_deliveries: dict[str, dict] = {}
_ws_observability: dict[str, int] = {
    "ws_ticket_401_terminal_failures": 0,
    "ghost_socket_cleanup": 0,
    "offline_routing_missing_session": 0,
    "ack_timeout_fallback": 0,
    "delivery_ack_received": 0,
    "read_up_to_sent": 0,
    "read_up_to_retry": 0,
    "read_up_to_ack_received": 0,
    "read_up_to_cursor_advanced": 0,
    "read_up_to_cursor_regress_rejected": 0,
    "read_up_to_queue_size": 0,
    "decrypt_fail_blocked_read": 0,
    "ephemeral_auth_missing_drops": 0,
    "ephemeral_auth_invalid_drops": 0,
}
READ_UP_TO_MIN_CLIENT_VERSION = 2


def _metric_inc(name: str, amount: int = 1) -> None:
    _ws_observability[name] = _ws_observability.get(name, 0) + amount


def _storm_throttled_response(*, retry_after: int, include_pending_shape: bool = False, reason: str = "storm_guard"):
    retry = max(1, int(retry_after or 1))
    payload = {
        "status": "ok",
        "ok": True,
        "throttled": True,
        "retry_after": retry,
        "reason": reason,
    }
    if include_pending_shape:
        payload.update(
            {
                "messages": [],
                "lease_token": None,
                "lease_message_ids": [],
                "lease_ttl_seconds": PENDING_LEASE_SECONDS,
            }
        )
    return payload


def _cleanup_request_guards(now: float) -> None:
    global _request_guard_last_cleanup
    if now - _request_guard_last_cleanup < REQUEST_GUARD_CLEANUP_INTERVAL:
        return

    _request_guard_last_cleanup = now
    pending_cutoff = now - (PENDING_FETCH_MIN_INTERVAL_SECONDS * 15)
    stale_pending = [
        key for key, ts in _pending_fetch_last_at.items() if ts < pending_cutoff
    ]
    for key in stale_pending:
        _pending_fetch_last_at.pop(key, None)

    donation_cutoff = now - (DONATION_STATUS_DEDUP_SECONDS * 4)
    pin_cutoff = now - (PIN_LIST_DEDUP_SECONDS * 4)
    for key in [k for k, ts in _donation_status_last.items() if ts < donation_cutoff]:
        _donation_status_last.pop(key, None)
        _donation_status_cache.pop(key, None)
    for key in [k for k, ts in _pin_list_last.items() if ts < pin_cutoff]:
        _pin_list_last.pop(key, None)


def _is_donation_status_too_soon(session_secret: str) -> bool:
    now = time.time()
    _cleanup_request_guards(now)
    key = (session_secret or "").strip()
    if not key:
        return False
    last = _donation_status_last.get(key)
    _donation_status_last[key] = now
    return last is not None and (now - last) < DONATION_STATUS_DEDUP_SECONDS


def _is_pin_list_too_soon(cache_key: str) -> bool:
    now = time.time()
    _cleanup_request_guards(now)
    key = (cache_key or "").strip()
    if not key:
        return False
    last = _pin_list_last.get(key)
    _pin_list_last[key] = now
    return last is not None and (now - last) < PIN_LIST_DEDUP_SECONDS


def _is_pending_fetch_too_soon(session_secret: str) -> bool:
    now = time.time()
    _cleanup_request_guards(now)
    key = (session_secret or "").strip()
    if not key:
        return False

    last = _pending_fetch_last_at.get(key)
    _pending_fetch_last_at[key] = now
    return last is not None and (now - last) < PENDING_FETCH_MIN_INTERVAL_SECONDS


_SQLITE_INT64_MIN = -(2 ** 63)
_SQLITE_INT64_MAX = (2 ** 63) - 1


def _clamp_int64(value: int) -> int:
    if value > _SQLITE_INT64_MAX:
        return _SQLITE_INT64_MAX
    if value < _SQLITE_INT64_MIN:
        return _SQLITE_INT64_MIN
    return value


def _coerce_int64(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return _clamp_int64(int(value))
        txt = str(value).strip()
        if not txt:
            return default
        return _clamp_int64(int(txt))
    except Exception:
        return default


def _utc_now_ms() -> int:
    return int(time.time() * 1000)


def _parse_msg_rank(
    msg_rank: Optional[object],
    client_timestamp: Optional[str] = None,
) -> int:
    # 64-bit-safe monotonic rank policy:
    # 1) Prefer client-sent msg_rank (expected unix ms)
    # 2) Fallback to client_timestamp parsed as unix ms / ISO time
    # 3) Fallback to server current unix ms
    now_ms = _utc_now_ms()

    rank_from_payload = _coerce_int64(msg_rank, default=0)
    if rank_from_payload > 0:
        return rank_from_payload

    if client_timestamp:
        try:
            ts = str(client_timestamp).strip()
            if ts:
                if ts.isdigit():
                    return _coerce_int64(int(ts), default=now_ms)
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return _coerce_int64(int(dt.timestamp() * 1000), default=now_ms)
        except Exception:
            pass

    return _coerce_int64(now_ms, default=0)


def _upsert_read_cursor(
    *,
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    user_id: str,
    peer_id: str,
    msg_id: str,
    msg_rank: int,
    device_id: Optional[str],
) -> tuple[bool, str, int]:
    safe_rank = _coerce_int64(msg_rank, default=_utc_now_ms())
    cursor.execute(
        "SELECT last_read_msg_id, COALESCE(last_read_rank, 0) AS last_read_rank "
        "FROM conversation_read_state WHERE user_id=? AND peer_id=?",
        (user_id, peer_id),
    )
    row = cursor.fetchone()
    if row is not None:
        current_rank = _coerce_int64(row["last_read_rank"], default=0)
        if safe_rank <= current_rank:
            _metric_inc("read_up_to_retry")
        if safe_rank < current_rank:
            _metric_inc("read_up_to_cursor_regress_rejected")
            return False, str(row["last_read_msg_id"] or msg_id), current_rank

    cursor.execute(
        """
        INSERT INTO conversation_read_state
            (user_id, peer_id, last_read_msg_id, last_read_rank, last_read_at, updated_by_device)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(user_id, peer_id) DO UPDATE SET
            last_read_msg_id = CASE
                WHEN excluded.last_read_rank >= COALESCE(conversation_read_state.last_read_rank, 0)
                    THEN excluded.last_read_msg_id
                ELSE conversation_read_state.last_read_msg_id
            END,
            last_read_rank = MAX(COALESCE(conversation_read_state.last_read_rank, 0), excluded.last_read_rank),
            last_read_at = CASE
                WHEN excluded.last_read_rank >= COALESCE(conversation_read_state.last_read_rank, 0)
                    THEN CURRENT_TIMESTAMP
                ELSE conversation_read_state.last_read_at
            END,
            updated_by_device = CASE
                WHEN excluded.last_read_rank >= COALESCE(conversation_read_state.last_read_rank, 0)
                    THEN excluded.updated_by_device
                ELSE conversation_read_state.updated_by_device
            END
        """,
        (user_id, peer_id, msg_id, safe_rank, device_id),
    )

    cursor.execute(
        "SELECT last_read_msg_id, COALESCE(last_read_rank, 0) AS last_read_rank "
        "FROM conversation_read_state WHERE user_id=? AND peer_id=?",
        (user_id, peer_id),
    )
    updated = cursor.fetchone()
    applied_msg_id = str(updated["last_read_msg_id"] if updated else msg_id)
    applied_rank = _coerce_int64(updated["last_read_rank"] if updated else safe_rank, default=safe_rank)
    advanced = applied_rank >= safe_rank
    if advanced:
        _metric_inc("read_up_to_cursor_advanced")
    return advanced, applied_msg_id, applied_rank


def _queue_offline_with_push(
    *,
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    to_id: str,
    sender_id: Optional[str],
    payload: str,
    msg: dict,
    reason: str,
    exclude_session_secrets: Optional[set[str]] = None,
) -> Optional[dict]:
    """Insert offline message and return a push job when policy allows."""
    cursor.execute(
        "INSERT INTO offline_messages (receiver_id, payload) VALUES (?, ?)",
        (to_id, payload),
    )

    if reason == "missing_session":
        _metric_inc("offline_routing_missing_session")

    try:
        push_enabled = bool(push_delivery_status().get("enabled"))
    except Exception as exc:
        push_enabled = False
        print(f"[WARN] Push status check failed; skipping delegated wake: {exc}")

    if not push_enabled:
        _log_skipped_push_throttled(
            to_id=to_id,
            sender_id=sender_id or "",
            delivered_online=False,
            msg_type=str(msg.get("type")),
            reason="push_disabled",
        )
        return None

    if to_id != sender_id and should_send_push_for_message(msg):
        push_data = _build_delegated_push_metadata(msg)
        return {
            "user_id": to_id,
            "title": GENERIC_PUSH_TITLE,
            "body": GENERIC_PUSH_BODY,
            "data_payload": push_data,
            "exclude_session_secrets": list(exclude_session_secrets or []),
        }

    _log_skipped_push_throttled(
        to_id=to_id,
        sender_id=sender_id or "",
        delivered_online=False,
        msg_type=str(msg.get("type")),
        reason=f"policy_{reason}",
    )
    return None


def _build_delegated_push_metadata(msg: dict) -> dict:
    """Build opaque delegated push metadata without inspecting preview content."""
    push_data = {
        "type": msg.get("push_type") or msg.get("type"),
        "enc_v": "2",
    }

    for source_key, output_key in (
        ("msg_id", "msg_id"),
        ("conversation_id", "conversation_id"),
        ("group_id", "conversation_id"),
        ("groupId", "conversation_id"),
    ):
        if output_key in push_data:
            continue
        value = msg.get(source_key)
        if value is not None and str(value).strip():
            push_data[output_key] = value

    if "preview_envelope_v1" in msg:
        preview_envelope = msg.get("preview_envelope_v1")
        if preview_envelope not in (None, ""):
            push_data["preview_envelope_v1"] = preview_envelope

    return push_data


def _build_online_push_exclusions(
    *,
    cursor: sqlite3.Cursor,
    to_id: str,
    sender_id: Optional[str],
    msg: dict,
    delivered_session_secrets: set[str],
) -> set[str]:
    """Build push exclusions for online delivery.

    Suppress push only for delivered sessions that report foreground + same
    conversation. WebSocket delivery alone does not prove push is unnecessary.
    """
    normalized = {
        str(session_secret).strip()
        for session_secret in (delivered_session_secrets or set())
        if str(session_secret).strip()
    }
    if not normalized:
        return set()

    group_id = str(msg.get("group_id") or msg.get("groupId") or "").strip()
    sender_id_norm = str(sender_id or "").strip()

    same_chat_sessions: set[str] = set()
    for session_secret in normalized:
        state = _connection_registry_get_session_state(to_id, session_secret)
        app_foreground = bool(state.get("app_foreground"))
        if not app_foreground:
            continue

        active_chat_id = str(state.get("active_chat_id") or "").strip()
        if group_id:
            active_group_id = str(state.get("active_group_id") or "").strip()
            if active_group_id == group_id or active_chat_id == group_id:
                same_chat_sessions.add(session_secret)
            continue

        if sender_id_norm:
            active_peer_user_id = str(state.get("active_peer_user_id") or "").strip()
            if active_peer_user_id == sender_id_norm or active_chat_id == sender_id_norm:
                same_chat_sessions.add(session_secret)

    return same_chat_sessions


def _connection_registry_set_session_state(
    user_id: str,
    session_secret: str,
    state: dict,
) -> None:
    """Best-effort compatibility bridge for mixed ConnectionRegistry versions.

    Presence updates must never crash websocket loops, otherwise mobile clients
    enter reconnect storms and eventually hit ws_ticket rate limits.
    """
    setter = getattr(connection_registry, "set_session_state", None)
    if callable(setter):
        setter(user_id, session_secret, state)
        return

    lock = getattr(connection_registry, "_lock", None)
    session_state = getattr(connection_registry, "_session_state", None)
    if not isinstance(session_state, dict):
        return

    if lock is not None:
        with lock:
            user_state = session_state.setdefault(user_id, {})
            merged = dict(user_state.get(session_secret, {}))
            merged.update(state)
            user_state[session_secret] = merged
        return

    user_state = session_state.setdefault(user_id, {})
    merged = dict(user_state.get(session_secret, {}))
    merged.update(state)
    user_state[session_secret] = merged


def _connection_registry_get_session_state(user_id: str, session_secret: str) -> dict:
    getter = getattr(connection_registry, "get_session_state", None)
    if callable(getter):
        try:
            data = getter(user_id, session_secret)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}

    lock = getattr(connection_registry, "_lock", None)
    session_state = getattr(connection_registry, "_session_state", None)
    if not isinstance(session_state, dict):
        return {}

    if lock is not None:
        with lock:
            return dict(session_state.get(user_id, {}).get(session_secret, {}))
    return dict(session_state.get(user_id, {}).get(session_secret, {}))


async def _register_pending_delivery_ack(
    *,
    delivery_id: str,
    to_id: str,
    sender_id: Optional[str],
    msg: dict,
    payload: str,
) -> None:
    expires_at = time.time() + _PENDING_DELIVERY_TIMEOUT_SECONDS
    async with _pending_delivery_lock:
        _pending_deliveries[delivery_id] = {
            "to_id": to_id,
            "sender_id": sender_id,
            "msg": msg,
            "payload": payload,
            "expires_at": expires_at,
        }


async def _consume_pending_delivery_ack(delivery_id: str) -> bool:
    async with _pending_delivery_lock:
        existed = _pending_deliveries.pop(delivery_id, None) is not None
    if existed:
        _metric_inc("delivery_ack_received")
    return existed


async def _pending_delivery_timeout_worker() -> None:
    while True:
        await asyncio.sleep(1)
        now = time.time()
        expired: list[dict] = []
        async with _pending_delivery_lock:
            stale_ids = [
                delivery_id
                for delivery_id, data in _pending_deliveries.items()
                if float(data.get("expires_at", 0)) <= now
            ]
            for stale_id in stale_ids:
                data = _pending_deliveries.pop(stale_id, None)
                if data:
                    expired.append(data)

        if not expired:
            continue

        conn = get_db()
        cursor = conn.cursor()
        fallback_count = 0
        pending_push_jobs: list[dict] = []
        try:
            for item in expired:
                to_id = str(item.get("to_id") or "").strip()
                payload = str(item.get("payload") or "")
                msg = item.get("msg") if isinstance(item.get("msg"), dict) else {}
                sender_id = item.get("sender_id")
                if not to_id or not payload:
                    continue
                fallback_count += 1
                push_job = _queue_offline_with_push(
                    conn=conn,
                    cursor=cursor,
                    to_id=to_id,
                    sender_id=sender_id,
                    payload=payload,
                    msg=msg,
                    reason="ack_timeout",
                )
                if push_job:
                    pending_push_jobs.append(push_job)

            if fallback_count:
                _metric_inc("ack_timeout_fallback", fallback_count)
                conn.commit()
        finally:
            conn.close()
        for push_job in pending_push_jobs:
            enqueue_push(**push_job)


def _log_skipped_push_throttled(*, to_id: str, sender_id: str, delivered_online: bool, msg_type: str, reason: str):
    now = time.time()
    key = f"{to_id}:{sender_id}:{msg_type}:{reason}:{int(delivered_online)}"
    last = _push_skip_last_log_at.get(key)
    if last is not None and (now - last) < _PUSH_SKIP_LOG_THROTTLE_SECONDS:
        return

    _push_skip_last_log_at[key] = now

    if len(_push_skip_last_log_at) > 2000:
        cutoff = now - (_PUSH_SKIP_LOG_THROTTLE_SECONDS * 4)
        stale_keys = [k for k, ts in _push_skip_last_log_at.items() if ts < cutoff]
        for stale_key in stale_keys:
            _push_skip_last_log_at.pop(stale_key, None)

    print(
        f"[INFO] Skipping push (reason={reason}, to_id={to_id}, sender={sender_id}, online={delivered_online}, type={msg_type})"
    )


_BLOCK_BYPASS_MESSAGE_TYPES = {
    "delivery_receipt",
    "read_receipt",
    "read_up_to",
    "read_up_to_ack",
    "delivery_ack",
    "delete_message",
}


def _is_blockable_direct_message(msg: dict, sender_id: str, to_id: str) -> bool:
    if not sender_id or not to_id or sender_id == to_id:
        return False
    if msg.get("group_id") or msg.get("groupId"):
        return False
    msg_type = str(msg.get("type") or "").strip()
    return msg_type not in _BLOCK_BYPASS_MESSAGE_TYPES


def _recipient_has_blocked_sender(cursor: sqlite3.Cursor, recipient_id: str, sender_id: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM user_blocks WHERE user_id=? AND blocked_id=? LIMIT 1",
        (recipient_id, sender_id),
    )
    return cursor.fetchone() is not None


async def _send_sender_blocked_notice(
    websocket: WebSocket,
    *,
    peer_id: str,
    msg_id: Optional[str] = None,
) -> None:
    payload = {
        "type": "sender_blocked_notice",
        "peer_id": peer_id,
        "reason": "blocked_by_user",
    }
    if msg_id:
        payload["msg_id"] = msg_id
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:
        pass

ensure_upload_dir()


app = FastAPI(
    title="Chatapp API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.on_event("startup")
async def _startup_handler() -> None:
    """Launch background tasks on server start."""
    connection_registry.configure_backend(
        backend=PRESENCE_BACKEND,
        redis_url=PRESENCE_REDIS_URL,
        ttl_seconds=PRESENCE_TTL_SECONDS,
    )
    fanout_bridge.configure()
    fanout_bridge.start(asyncio.get_running_loop(), _deliver_fanout_to_local_session)
    start_push_workers()
    asyncio.create_task(_rotation_scheduler_task())
    asyncio.create_task(_storage_cleanup_scheduler_task())
    asyncio.create_task(_pending_delivery_timeout_worker())


@app.on_event("shutdown")
async def _shutdown_handler() -> None:
    fanout_bridge.stop()
    stop_push_workers()
    connection_registry.stop()


async def _deliver_fanout_to_local_session(user_id: str, session_secret: str, payload: str) -> bool:
    if not connection_registry.has(user_id, session_secret):
        return False
    try:
        socket = connection_registry.get_socket(user_id, session_secret)
        if socket is None:
            return False
        await socket.send_text(payload)
        return True
    except Exception:
        connection_registry.remove(user_id, session_secret)
        return False


async def _rotation_scheduler_task() -> None:
    """Hourly check for scheduled identity key rotation (custom servers only)."""
    while True:
        await asyncio.sleep(3600)
        try:
            _check_scheduled_rotation()
        except Exception as exc:
            print(f"⚠️  Rotation scheduler error: {exc}")


async def _storage_cleanup_scheduler_task() -> None:
    """Periodic cleanup so expired uploaded files are removed on time."""
    while True:
        await asyncio.sleep(600)
        try:
            cleanup_system()
        except Exception as exc:
            print(f"⚠️  Storage cleanup scheduler error: {exc}")





initialize_database()


# --- Identity Key Rotation Helpers ---

def _compute_identity_fingerprint(public_key) -> str:
    """Return the SHA-256 fingerprint of an Ed25519 public key as colon-separated uppercase hex.

    This is the canonical "server identity fingerprint" pinned by clients.
    """
    raw_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(raw_bytes).digest()
    return ':'.join(f'{b:02X}' for b in digest)


def _load_grace_period_state() -> None:
    """Load the old identity key and grace-period end time from the DB + filesystem.

    Called once at startup (after init_db + get_db are available).  When the grace
    period has already expired the stale old-key file is deleted automatically.
    """
    global _old_private_key, _old_public_key, _grace_period_ends_at
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT old_fingerprint, new_fingerprint, grace_period_ends_at "
            "FROM identity_key_rotation_announcements "
            "ORDER BY id DESC LIMIT 1"
        )
        ann = cursor.fetchone()
        conn.close()

        if ann is None:
            return

        grace_ends: float = ann["grace_period_ends_at"]
        if grace_ends <= time.time():
            # Grace period over – remove stale old-key file if present
            if os.path.exists(OLD_SIGNING_KEY_FILE):
                try:
                    os.remove(OLD_SIGNING_KEY_FILE)
                except OSError:
                    pass
            return

        _grace_period_ends_at = grace_ends

        if os.path.exists(OLD_SIGNING_KEY_FILE):
            try:
                with open(OLD_SIGNING_KEY_FILE, "rb") as f:
                    _old_private_key = serialization.load_pem_private_key(f.read(), password=None)
                _old_public_key = _old_private_key.public_key()
                print(
                    f"🔑 Old identity key loaded "
                    f"(grace period active until "
                    f"{datetime.fromtimestamp(grace_ends).isoformat()})"
                )
            except Exception as exc:
                print(f"⚠️  Failed to load old signing key from {OLD_SIGNING_KEY_FILE}: {exc}")
    except Exception as exc:
        print(f"⚠️  Failed to load grace period state: {exc}")


_load_grace_period_state()


def _perform_identity_key_rotation() -> dict:
    """Generate a new Ed25519 keypair, create a signed rotation announcement, persist
    both keys, store the announcement in the DB, and update the in-process globals.

    MUST NOT be called when IS_PUBLIC_HUB is True.  Raises ValueError in that case.
    """
    global _private_key, _server_public_key, _old_private_key, _old_public_key, _grace_period_ends_at

    if IS_PUBLIC_HUB:
        raise ValueError("Public hub uses a long-term identity key – rotation is not permitted.")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT grace_period_days, rotation_interval_days FROM identity_key_rotation_config LIMIT 1")
        config = cursor.fetchone()
        grace_days = int(config["grace_period_days"]) if config else ROTATION_GRACE_PERIOD_DAYS
        interval_days = int(config["rotation_interval_days"]) if config else ROTATION_INTERVAL_DAYS

        old_priv = _private_key
        old_pub = _server_public_key
        old_fingerprint = _compute_identity_fingerprint(old_pub)

        new_priv = ed25519.Ed25519PrivateKey.generate()
        new_pub = new_priv.public_key()
        new_fingerprint = _compute_identity_fingerprint(new_pub)

        valid_from: float = time.time()
        grace_period_ends_at: float = valid_from + (grace_days * 86_400.0)

        new_pub_raw = new_pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        new_pub_b64 = base64.b64encode(new_pub_raw).decode()

        # Canonical JSON payload signed by the OLD private key
        announcement_payload = json.dumps(
            {
                "old_fingerprint": old_fingerprint,
                "new_fingerprint": new_fingerprint,
                "new_public_key": new_pub_b64,
                "valid_from": valid_from,
                "grace_period_ends_at": grace_period_ends_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        sig_bytes = old_priv.sign(announcement_payload.encode())
        sig_b64 = base64.b64encode(sig_bytes).decode()

        # Persist old key first (idempotent backup)
        with open(OLD_SIGNING_KEY_FILE, "wb") as f:
            f.write(old_priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ))

        # Atomic write of new key (write temp then rename)
        tmp_path = SIGNING_KEY_FILE + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(new_priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ))
        os.replace(tmp_path, SIGNING_KEY_FILE)

        cursor.execute(
            """
            INSERT INTO identity_key_rotation_announcements
                (old_fingerprint, new_fingerprint, new_public_key, valid_from, grace_period_ends_at, signature)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (old_fingerprint, new_fingerprint, new_pub_b64, valid_from, grace_period_ends_at, sig_b64),
        )

        next_rotation = (datetime.now() + timedelta(days=interval_days)).isoformat()
        cursor.execute(
            """
            INSERT INTO identity_key_rotation_config
                (id, rotation_enabled, rotation_interval_days, grace_period_days, next_rotation_at, updated_at)
            VALUES (1, 1, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                next_rotation_at = excluded.next_rotation_at,
                updated_at       = excluded.updated_at
            """,
            (interval_days, grace_days, next_rotation),
        )
        conn.commit()

        # Update in-process globals
        _private_key = new_priv
        _server_public_key = new_pub
        _old_private_key = old_priv
        _old_public_key = old_pub
        _grace_period_ends_at = grace_period_ends_at

        print(f"✅ Identity key rotated.")
        print(f"   Old fingerprint : {old_fingerprint}")
        print(f"   New fingerprint : {new_fingerprint}")
        print(f"   Grace period ends: {datetime.fromtimestamp(grace_period_ends_at).isoformat()}")

        return {
            "status": "ok",
            "old_fingerprint": old_fingerprint,
            "new_fingerprint": new_fingerprint,
            "valid_from": valid_from,
            "grace_period_ends_at": grace_period_ends_at,
            "next_rotation_at": next_rotation,
        }
    finally:
        conn.close()


def _check_scheduled_rotation() -> None:
    """Trigger a rotation when the scheduled time has arrived.

    No-op on public hub or when rotation is disabled in the DB config.
    """
    if IS_PUBLIC_HUB:
        return
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT rotation_enabled, next_rotation_at FROM identity_key_rotation_config LIMIT 1")
        config = cursor.fetchone()
        conn.close()
    except Exception as exc:
        print(f"⚠️  Rotation scheduler DB error: {exc}")
        return

    if config is None or not config["rotation_enabled"]:
        return

    next_at = config["next_rotation_at"]
    if not next_at:
        return

    try:
        next_dt = datetime.fromisoformat(next_at)
    except (ValueError, TypeError):
        return

    if datetime.now() >= next_dt:
        print("⏰ Scheduled identity key rotation triggered.")
        try:
            _perform_identity_key_rotation()
        except Exception as exc:
            print(f"❌ Scheduled rotation failed: {exc}")


# --- Security Helpers ---
def get_secure_identity(client_hash: str) -> str:
    return hmac.new(SERVER_IDENTITY_SALT.encode(), client_hash.encode(), hashlib.sha256).hexdigest()

def _receipt_hash(platform: str, verification_source: str, verification_data: str) -> str:
    base = f"{platform}|{verification_source}|{verification_data}".encode("utf-8")
    return hashlib.sha256(base).hexdigest()

def _receipt_id(purchase_id: str) -> str:
    digest = hashlib.sha256(purchase_id.encode("utf-8")).hexdigest()[:16].upper()
    return f"DON-{digest}"

def _month_window_utc(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now or datetime.utcnow()
    month_start = datetime(current.year, current.month, 1)
    if current.month == 12:
        next_month_start = datetime(current.year + 1, 1, 1)
    else:
        next_month_start = datetime(current.year, current.month + 1, 1)
    return month_start, next_month_start

def _list_monthly_priority_donor_user_ids(cursor: sqlite3.Cursor, now: Optional[datetime] = None) -> list[str]:
    if DONATION_MONTHLY_PRIORITY_USERS <= 0:
        return []

    month_start, next_month_start = _month_window_utc(now)
    month_start_sql = month_start.strftime("%Y-%m-%d %H:%M:%S")
    next_month_start_sql = next_month_start.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        SELECT user_id, MIN(created_at) AS first_donation_at, MIN(id) AS first_donation_id
        FROM iap_donations
        WHERE created_at >= ? AND created_at < ?
        GROUP BY user_id
        ORDER BY first_donation_at ASC, first_donation_id ASC
        LIMIT ?
        """,
        (month_start_sql, next_month_start_sql, DONATION_MONTHLY_PRIORITY_USERS),
    )
    rows = cursor.fetchall()
    return [str(row["user_id"]) for row in rows if row and row["user_id"]]

def _has_active_donor_badge(cursor: sqlite3.Cursor, user_id: str, now: Optional[datetime] = None) -> bool:
    current = now or datetime.utcnow()
    cutoff = (current - timedelta(days=DONATION_BADGE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "SELECT 1 FROM iap_donations WHERE user_id = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
        (user_id, cutoff),
    )
    return cursor.fetchone() is not None

def _get_effective_storage_limit(cursor: sqlite3.Cursor, user_id: str, now: Optional[datetime] = None) -> int:
    cursor.execute(
        "SELECT COALESCE(storage_limit, ?) AS storage_limit FROM users WHERE user_id = ?",
        (DEFAULT_STORAGE_LIMIT, user_id),
    )
    row = cursor.fetchone()
    base_limit = int(row["storage_limit"] or DEFAULT_STORAGE_LIMIT) if row else DEFAULT_STORAGE_LIMIT
    if base_limit <= 0:
        base_limit = DEFAULT_STORAGE_LIMIT

    if not IS_PUBLIC_HUB:
        return PRIVATE_SERVER_STORAGE_LIMIT

    monthly_priority_ids = _list_monthly_priority_donor_user_ids(cursor, now=now)
    if user_id in monthly_priority_ids:
        return max(base_limit, DONATION_MONTHLY_STORAGE_LIMIT)
    return base_limit

def _build_donation_status(cursor: sqlite3.Cursor, user_id: str) -> dict:
    now = datetime.utcnow()
    monthly_priority_ids = _list_monthly_priority_donor_user_ids(cursor, now=now)
    has_monthly_priority_bonus = IS_PUBLIC_HUB and user_id in monthly_priority_ids
    effective_storage_limit = _get_effective_storage_limit(cursor, user_id, now=now)
    donor_badge_active = _has_active_donor_badge(cursor, user_id, now=now)
    return {
        "donor_badge_active": donor_badge_active,
        "has_monthly_priority_bonus": has_monthly_priority_bonus,
        "effective_storage_limit": effective_storage_limit,
        "monthly_priority_slots": DONATION_MONTHLY_PRIORITY_USERS,
    }

def _is_basic_receipt_format_valid(platform: str, verification_data: str) -> bool:
    data = (verification_data or "").strip()
    if not data:
        return False

    if platform == "ios":
        # StoreKit 2 transaction token is usually JWS (three segments)
        return data.count(".") == 2

    if platform == "android":
        # Play purchase token is opaque but generally non-trivial length
        return len(data) >= 20

    return False

def _verify_apple_receipt(product_id: str, verification_data: str) -> tuple[bool, str]:
    if not APPLE_IAP_SHARED_SECRET:
        return False, "Apple verification is not configured (missing APPLE_IAP_SHARED_SECRET)"

    payload = {
        "receipt-data": verification_data,
        "password": APPLE_IAP_SHARED_SECRET,
        "exclude-old-transactions": True,
    }

    try:
        response = requests.post(
            "https://buy.itunes.apple.com/verifyReceipt",
            json=payload,
            timeout=15,
        )
        data = response.json()
    except Exception as error:
        return False, f"Apple receipt verification request failed: {error}"

    status_code = int(data.get("status", -1)) if isinstance(data, dict) else -1
    if status_code == 21007:
        try:
            sandbox_response = requests.post(
                "https://sandbox.itunes.apple.com/verifyReceipt",
                json=payload,
                timeout=15,
            )
            data = sandbox_response.json()
            status_code = int(data.get("status", -1)) if isinstance(data, dict) else -1
        except Exception as error:
            return False, f"Apple sandbox verification failed: {error}"

    if status_code != 0:
        return False, f"Apple receipt invalid (status={status_code})"

    receipt = data.get("receipt", {}) if isinstance(data, dict) else {}
    if APPLE_IAP_BUNDLE_ID:
        bundle_id = (receipt.get("bundle_id") or "").strip()
        if bundle_id and bundle_id != APPLE_IAP_BUNDLE_ID:
            return False, "Apple receipt bundle_id mismatch"

    in_app_items = receipt.get("in_app", []) if isinstance(receipt, dict) else []
    latest_receipt_items = data.get("latest_receipt_info", []) if isinstance(data, dict) else []
    all_items = []
    if isinstance(in_app_items, list):
        all_items.extend(in_app_items)
    if isinstance(latest_receipt_items, list):
        all_items.extend(latest_receipt_items)

    for item in all_items:
        if not isinstance(item, dict):
            continue
        if (item.get("product_id") or "").strip() == product_id:
            return True, "ok"

    return False, "Apple receipt does not contain expected product"

def _get_google_access_token() -> tuple[Optional[str], str]:
    if not GOOGLE_PLAY_SERVICE_ACCOUNT_JSON:
        return None, "Google verification is not configured (missing GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)"

    try:
        cred_info = json.loads(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            cred_info,
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        creds.refresh(GoogleAuthRequest())
        token = creds.token
        if not token:
            return None, "Google auth token is empty"
        return token, "ok"
    except Exception as error:
        return None, f"Google credential setup failed: {error}"

def _verify_google_play_purchase(product_id: str, donation_type: str, verification_data: str) -> tuple[bool, str]:
    if not GOOGLE_PLAY_PACKAGE_NAME:
        return False, "Google verification is not configured (missing GOOGLE_PLAY_PACKAGE_NAME)"

    access_token, token_msg = _get_google_access_token()
    if not access_token:
        return False, token_msg

    encoded_package = quote(GOOGLE_PLAY_PACKAGE_NAME, safe="")
    encoded_product = quote(product_id, safe="")
    encoded_token = quote(verification_data, safe="")

    if donation_type == "monthly":
        url = (
            "https://androidpublisher.googleapis.com/androidpublisher/v3/"
            f"applications/{encoded_package}/purchases/subscriptions/{encoded_product}/tokens/{encoded_token}"
        )
    else:
        url = (
            "https://androidpublisher.googleapis.com/androidpublisher/v3/"
            f"applications/{encoded_package}/purchases/products/{encoded_product}/tokens/{encoded_token}"
        )

    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except Exception as error:
        return False, f"Google verification request failed: {error}"

    if response.status_code >= 400:
        return False, f"Google purchase verification failed (HTTP {response.status_code})"

    try:
        data = response.json()
    except Exception as error:
        return False, f"Google verification decode failed: {error}"

    if donation_type == "monthly":
        if not isinstance(data, dict):
            return False, "Invalid Google subscription response"
        expiry = data.get("expiryTimeMillis")
        if expiry is None:
            return False, "Google subscription missing expiry"
        if "cancelReason" in data and str(data.get("cancelReason")) not in ("", "0"):
            return False, "Google subscription is cancelled"
        return True, "ok"

    purchase_state = data.get("purchaseState") if isinstance(data, dict) else None
    if str(purchase_state) != "0":
        return False, "Google product not purchased"

    return True, "ok"

def _verify_iap_receipt(platform: str, product_id: str, donation_type: str, verification_data: str) -> tuple[bool, str]:
    if platform == "ios":
        return _verify_apple_receipt(product_id, verification_data)
    if platform == "android":
        return _verify_google_play_purchase(product_id, donation_type, verification_data)
    return False, "Unsupported platform"

# --- Background Tasks ---
def cleanup_system():
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now()
    cursor.execute("SELECT file_id, owner_id, file_size FROM file_registry WHERE expires_at < ?", (now,))
    for row in cursor.fetchall():
        fid, owner, fsize = row["file_id"], row["owner_id"], row["file_size"]
        file_path = os.path.join(UPLOAD_DIR, fid)
        if os.path.exists(file_path):
            os.remove(file_path)
        cursor.execute("UPDATE users SET storage_used = storage_used - ? WHERE user_id = ?", (fsize, owner))
        cursor.execute("DELETE FROM file_registry WHERE file_id = ?", (fid,))
        cursor.execute("DELETE FROM file_download_tokens WHERE file_id = ?", (fid,))
    cursor.execute("DELETE FROM file_download_tokens WHERE expires_at < ?", (now,))
    cursor.execute("DELETE FROM file_download_tokens WHERE file_id NOT IN (SELECT file_id FROM file_registry)")
    cursor.execute("DELETE FROM ws_tickets WHERE expires_at < ? OR used = 1", (now,))
    msg_expiry = now - timedelta(days=OFFLINE_MSG_RETENTION_DAYS)
    cursor.execute(
        "DELETE FROM offline_messages WHERE COALESCE(created_at, CURRENT_TIMESTAMP) < ?",
        (msg_expiry,),
    )
    expired_links = [k for k, v in linking_blobs.items() if time.time() - v['t'] > 600]
    for k in expired_links: del linking_blobs[k]
    conn.commit()
    conn.close()

# --- Models ---
class UserReg(BaseModel):
    username_hash: str
    password: str
    public_key: str
    user_id: Optional[str] = None
    reg_key: Optional[str] = None
    lang: Optional[str] = "en"

class UserLogin(BaseModel):
    username_hash: str
    password: str
    device_id: Optional[str] = "unknown"
    device_name: Optional[str] = "Device"
    is_main: Optional[bool] = False


class AccountResetRequest(BaseModel):
    password: str
    mode: Literal["friend_only", "account_reset"] = "friend_only"
    purge_files: bool = True

class LinkSubmit(BaseModel):
    link_id: str
    encrypted_blob: str

class BroadcastRequest(BaseModel):
    title: str
    body: str

class StorageUpdate(BaseModel):
    username_hash: str
    new_limit_mb: int

class RotationPinUpdate(BaseModel):
    domain: str
    fingerprint: str

class IdentityRotationConfigUpdate(BaseModel):
    rotation_enabled: bool
    rotation_interval_days: int = 90
    grace_period_days: int = 30

class DonationIntentRequest(BaseModel):
    amount_minor: int
    currency: str = "usd"
    donation_type: str = "once"

class DonationIapRecordRequest(BaseModel):
    donation_type: str = "once"
    product_id: str
    purchase_id: str
    transaction_date: str = ""
    verification_data: str = ""
    verification_source: str = ""
    amount_minor: int
    currency: str = "usd"
    platform: str

class BlockUserRequest(BaseModel):
    user_id: str
    blocked_id: str


@app.post("/chat/admin/broadcast")
async def admin_broadcast(
    request: Request,
    req: BroadcastRequest,
    credentials: HTTPBasicCredentials = Security(security),
    x_admin_totp: Optional[str] = Header(default=None, alias="x-admin-totp"),
):
    # Preserve legacy admin API semantics: HTTP Basic password verified against ADMIN_PASS_HASH,
    # with x-admin-totp required when ADMIN_TOTP_SECRET is configured.
    verify_admin_access(request, credentials, x_admin_totp)
    check_rate_limit(request, 5)
    check_admin_ip(request)

    title = (req.title or "").strip()
    body = (req.body or "").strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="title and body are required")

    msg = {
        "type": "broadcast",
        "title": title,
        "body": body,
        "timestamp": datetime.now().isoformat(),
    }
    payload = json.dumps(msg, ensure_ascii=False)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id FROM users")
    user_rows = cursor.fetchall()
    user_ids = [row["user_id"] for row in user_rows if row["user_id"]]

    users_total = len(user_ids)
    online_users = 0
    queued_users = 0
    push_targeted = 0

    for user_id in user_ids:
        delivered_online = False
        sessions = connection_registry.get_user_sessions(user_id)
        if sessions:
            for session_key, ws in list(sessions.items()):
                try:
                    await ws.send_text(payload)
                    delivered_online = True
                except Exception:
                    connection_registry.remove(user_id, session_key)

        if delivered_online:
            online_users += 1
            continue

        cursor.execute(
            "INSERT INTO offline_messages (receiver_id, payload) VALUES (?, ?)",
            (user_id, payload),
        )
        queued_users += 1

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "users_total": users_total,
        "online_users": online_users,
        "queued_users": queued_users,
        "push_targeted": push_targeted,
    }

@app.get("/chat/donation/config")
async def donation_config(request: Request):
    check_rate_limit(request, 30)
    return {
        "status": "ok",
        "payment_mode": PAYMENT_MODE,
        "external_payments_enabled": ALLOW_EXTERNAL_DONATIONS,
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "merchant_country": STRIPE_MERCHANT_COUNTRY,
    }

@app.post("/chat/donation/create_payment_intent")
async def create_donation_payment_intent(
    request: Request,
    req: DonationIntentRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    check_rate_limit(request, 20)

    if not ALLOW_EXTERNAL_DONATIONS:
        return {
            "status": "error",
            "msg": "External donations are disabled on this server (IAP-only mode)",
            "payment_mode": PAYMENT_MODE,
        }

    if not STRIPE_SECRET_KEY:
        return {
            "status": "error",
            "msg": "Payment backend is not configured (missing STRIPE_SECRET_KEY)",
        }

    if not STRIPE_PUBLISHABLE_KEY:
        return {
            "status": "error",
            "msg": "Payment backend is not configured (missing STRIPE_PUBLISHABLE_KEY)",
        }

    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, user_id = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=user_id)

    amount_minor = int(req.amount_minor)
    if amount_minor < 50:
        return {"status": "error", "msg": "Amount too small"}
    if amount_minor > 100000000:
        return {"status": "error", "msg": "Amount too large"}

    currency = (req.currency or "usd").strip().lower()
    if currency not in ("usd", "hkd", "jpy"):
        return {"status": "error", "msg": "Unsupported currency"}

    donation_type = (req.donation_type or "once").strip().lower()
    if donation_type not in ("once", "monthly"):
        donation_type = "once"

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        intent = stripe.PaymentIntent.create(
            amount=amount_minor,
            currency=currency,
            automatic_payment_methods={"enabled": True},
            metadata={
                "type": "donation",
                "user_id": user_id,
                "donation_type": donation_type,
            },
        )
    except Exception as e:
        return {"status": "error", "msg": f"Stripe error: {e}"}

    return {
        "status": "ok",
        "client_secret": intent.client_secret,
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "merchant_country": STRIPE_MERCHANT_COUNTRY,
        "currency": currency,
    }

@app.post("/chat/donation/record_iap")
async def record_iap_donation(
    request: Request,
    req: DonationIapRecordRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    check_rate_limit(request, 20)

    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, user_id = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=user_id)

    purchase_id = (req.purchase_id or "").strip()
    product_id = (req.product_id or "").strip()
    platform = (req.platform or "").strip().lower()

    if not purchase_id:
        return {"status": "error", "msg": "Missing purchase_id"}
    if not product_id:
        return {"status": "error", "msg": "Missing product_id"}
    if platform not in ("ios", "android"):
        return {"status": "error", "msg": "Invalid platform"}

    verification_data = (req.verification_data or "").strip()
    verification_source = (req.verification_source or "").strip()
    if not _is_basic_receipt_format_valid(platform, verification_data):
        return {"status": "error", "msg": "Invalid receipt format"}

    donation_type = (req.donation_type or "once").strip().lower()
    if donation_type not in ("once", "monthly"):
        donation_type = "once"

    currency = (req.currency or "usd").strip().lower()
    if len(currency) != 3:
        currency = "usd"

    amount_minor = int(req.amount_minor)
    if amount_minor <= 0:
        return {"status": "error", "msg": "Invalid amount"}

    verified, verify_msg = _verify_iap_receipt(
        platform=platform,
        product_id=product_id,
        donation_type=donation_type,
        verification_data=verification_data,
    )
    if not verified:
        return {
            "status": "error",
            "msg": f"Receipt verification failed: {verify_msg}",
        }

    receipt_hash = _receipt_hash(platform, verification_source, verification_data)
    receipt_id = _receipt_id(purchase_id)

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO iap_donations (
                user_id,
                platform,
                product_id,
                purchase_id,
                receipt_id,
                receipt_hash,
                donation_type,
                amount_minor,
                currency,
                verification_data,
                verification_source,
                transaction_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                platform,
                product_id,
                purchase_id,
                receipt_id,
                receipt_hash,
                donation_type,
                amount_minor,
                currency,
                "",
                verification_source,
                req.transaction_date or "",
            ),
        )

        donation_status = _build_donation_status(cursor, user_id)

        conn.commit()
        response = {
            "status": "ok",
            "msg": "Donation recorded",
            "purchase_id": purchase_id,
            "receipt_id": receipt_id,
            "storage_limit": donation_status["effective_storage_limit"],
            "donor_badge_active": donation_status["donor_badge_active"],
        }
        return response
    except sqlite3.IntegrityError:
        donation_status = _build_donation_status(cursor, user_id)
        return {
            "status": "ok",
            "msg": "Donation already recorded",
            "purchase_id": purchase_id,
            "receipt_id": receipt_id,
            "storage_limit": donation_status["effective_storage_limit"],
            "donor_badge_active": donation_status["donor_badge_active"],
        }
    finally:
        conn.close()

@app.get("/chat/donation/status")
async def donation_status(
    request: Request,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    check_rate_limit(request, 30)

    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, user_id = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=user_id)

    if _is_donation_status_too_soon(session_secret) and session_secret in _donation_status_cache:
        return {"status": "ok", **_donation_status_cache[session_secret]}

    conn = get_db()
    cursor = conn.cursor()
    try:
        status_data = _build_donation_status(cursor, user_id)
        _donation_status_cache[session_secret] = status_data
        return {
            "status": "ok",
            **status_data,
        }
    finally:
        conn.close()

## TODO(v4-central-backend): Official clients must call trusted central APIs
## for push authorization, license validation, server_id enforcement, quota
## governance, and central identity authority. The open-source custom backend
## intentionally exposes no relay, purchase, or push-registration endpoints.



# --- Dashboard Endpoints ---
@app.get("/openapi.json", include_in_schema=False)
async def custom_openapi(credentials: HTTPBasicCredentials = Depends(verify_docs_access)):
    return JSONResponse(content=get_openapi(title="ZK Chat API", version="2.0.0", routes=app.routes))

@app.get("/docs", include_in_schema=False)
async def custom_docs(credentials: HTTPBasicCredentials = Depends(verify_docs_access)):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="API Dashboard",
        swagger_ui_parameters={"persistAuthorization": True},
    )

# --- Welcome Message Helpers ---

def _get_welcome_messages() -> dict:
    """Load per-language welcome messages from file, falling back to built-in defaults."""
    try:
        if os.path.isfile(WELCOME_MESSAGES_FILE):
            with open(WELCOME_MESSAGES_FILE, "r", encoding="utf-8") as _f:
                _data = json.load(_f)
            return {**_WELCOME_MESSAGES_DEFAULT, **{k: v for k, v in _data.items() if isinstance(v, str) and v.strip()}}
    except Exception:
        pass
    return dict(_WELCOME_MESSAGES_DEFAULT)


def _normalize_lang(lang: str) -> str:
    """Normalise a BCP-47 / locale string to one of the supported message keys."""
    code = (lang or "en").lower().replace("-", "_").strip()
    if code in ("zh_cn", "zh_sg") or code.startswith("zh_hans"):
        return "zh_hans"
    if code in ("zh_tw", "zh_hk", "zh_mo") or code.startswith("zh_hant") or code.startswith("zh"):
        return "zh_hant"
    if code.startswith("ja"):
        return "ja"
    if code.startswith("en"):
        return "en"
    # For any other language fall back to English
    return "en"


def _to_ws_base_url(api_base_url: str) -> str:
    base = (api_base_url or "").strip()
    if not base:
        return ""
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return base


def _resolve_transport_descriptor() -> dict:
    api_base_url = (API_PUBLIC_BASE_URL or PUBLIC_HUB_URL or "").strip()
    ws_mode = WS_CONNECTION_MODE

    if ws_mode == "direct" and WS_DIRECT_PUBLIC_URL:
        ws_public_url = WS_DIRECT_PUBLIC_URL
    elif ws_mode == "tunnel" and WS_TUNNEL_BASE_URL:
        ws_public_url = WS_TUNNEL_BASE_URL
    else:
        ws_public_url = _to_ws_base_url(api_base_url)

    if not ws_public_url:
        ws_public_url = _to_ws_base_url(PUBLIC_HUB_URL)

    return {
        "api_base_url": api_base_url,
        "ws_connection_mode": ws_mode,
        "ws_public_url": ws_public_url,
        "pins": dict(TRANSPORT_PIN_HINTS),
        "region_policy_mode": REGION_POLICY_MODE,
        "default_region": DEFAULT_REGION,
        "china_fcm_policy": CHINA_FCM_POLICY,
    }


@app.get("/chat/transport_descriptor")
async def transport_descriptor():
    descriptor = _resolve_transport_descriptor()
    return {"status": "ok", **descriptor}


@app.get("/_new/presence_stats")
async def get_presence_stats():
    return {"status": "ok", **connection_registry.status()}


@app.get("/_new/fanout_stats")
async def get_fanout_stats():
    return {"status": "ok", **fanout_bridge.stats()}


@app.get("/_new/ws_reliability_stats")
async def get_ws_reliability_stats():
    async with _pending_delivery_lock:
        pending_count = len(_pending_deliveries)
    _ws_observability["read_up_to_queue_size"] = pending_count
    return {
        "status": "ok",
        "pending_delivery_ack_count": pending_count,
        "pending_delivery_timeout_seconds": _PENDING_DELIVERY_TIMEOUT_SECONDS,
        "counters": dict(_ws_observability),
    }


@app.get("/chat/runtime_stats")
async def get_runtime_stats():
    async with _pending_delivery_lock:
        pending_count = len(_pending_deliveries)
    _ws_observability["read_up_to_queue_size"] = pending_count
    return {
        "status": "ok",
        "pending_delivery_ack_count": pending_count,
        "pending_delivery_timeout_seconds": _PENDING_DELIVERY_TIMEOUT_SECONDS,
        "counters": dict(_ws_observability),
    }


def _get_fanout_delivery_data() -> dict:
    """Shared helper: build fanout delivery rate data from live bridge stats."""
    stats = fanout_bridge.stats()
    fanout_active: bool = bool(stats.get("enabled", False))
    published: int = int(stats.get("published", 0))
    received: int = int(stats.get("received", 0))
    delivered: int = int(stats.get("delivered", 0))
    dropped: int = int(stats.get("dropped", 0))
    errors: int = int(stats.get("errors", 0))
    uptime: int = int(stats.get("uptime_seconds", 0))

    def _safe_rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "fanout_active": fanout_active,
        "window_seconds": uptime,
        "window_note": "lifetime counters since startup",
        "published": published,
        "received": received,
        "delivered": delivered,
        "dropped": dropped,
        "errors": errors,
        "delivery_rate": {
            "delivered_over_published": _safe_rate(delivered, published),
            "delivered_over_received": _safe_rate(delivered, received),
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/_new/load_test_summary")
async def get_load_test_summary(window_seconds: int = 300):
    """Latency distribution (p50/p95/p99) for recently locally-delivered messages."""
    return {"status": "ok", **latency_tracker.get_summary(window_seconds=window_seconds)}


@app.get("/_new/fanout_delivery_rate")
async def get_fanout_delivery_rate():
    """Cross-node fanout effectiveness counters and derived delivery rates."""
    return {"status": "ok", **_get_fanout_delivery_data()}


@app.get("/_new/load_fanout_summary_txt")
async def get_load_fanout_summary_txt(window_seconds: int = 300):
    """Plain-text snapshot of latency + fanout metrics for canary/load-test review."""
    load_data = latency_tracker.get_summary(window_seconds=window_seconds)
    fanout_data = _get_fanout_delivery_data()
    return PlainTextResponse(content=summary_export.render_text(load_data, fanout_data))


def _queue_welcome_message(user_id: str, lang: str) -> None:
    """Insert a localised welcome broadcast into offline_messages for a newly registered user."""
    messages = _get_welcome_messages()
    key = _normalize_lang(lang)
    body = messages.get(key) or messages.get("en", "")
    if not body:
        return
    payload = json.dumps(
        {
            "type": "broadcast",
            "title": "MiraiChat",
            "body": body,
            "timestamp": datetime.now().isoformat(),
        },
        ensure_ascii=False,
    )
    try:
        _conn = get_db()
        _cursor = _conn.cursor()
        _cursor.execute(
            "INSERT INTO offline_messages (receiver_id, payload) VALUES (?, ?)",
            (user_id, payload),
        )
        _conn.commit()
        _conn.close()
    except Exception:
        pass


# --- Auth Endpoints ---
@app.post("/chat/register")
async def register(request: Request, user: UserReg):
    check_register_rate_limit(request)
    if REGISTRATION_KEY and user.reg_key != REGISTRATION_KEY:
        # Reviewer account is always allowed to register without a registration key.
        from chat_backend.reviewer_demo_hook import is_reviewer_username_hash
        if not is_reviewer_username_hash(user.username_hash):
            return {"status": "error", "msg": "Invalid registration key"}
    conn = get_db()
    cursor = conn.cursor()
    hashed_pw = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
    identity = get_secure_identity(user.username_hash)
    final_user_id = user.user_id if user.user_id else str(uuid.uuid4())
    try:
        cursor.execute(
            "INSERT INTO users (user_id, username_hash, password, public_key, storage_limit) VALUES (?, ?, ?, ?, ?)",
            (final_user_id, identity, hashed_pw, user.public_key, DEFAULT_STORAGE_LIMIT),
        )
        conn.commit()
        _fresh_registration_main_bootstrap[final_user_id] = time.time()
        _queue_welcome_message(final_user_id, user.lang or "en")
        return {"status": "ok", "user_id": final_user_id}
    except Exception:
        return {"status": "error", "msg": "Registration failed"}
    finally:
        conn.close()
        

@app.post("/chat/login")
async def login(request: Request, user: UserLogin):
    check_rate_limit(request, 15)
    storm_guard.maybe_heal_global(get_db)

    login_user_key = get_secure_identity(user.username_hash)
    login_session_key = (user.device_id or "").strip() or "unknown_device"
    allowed, retry_after, reason = storm_guard.allow_heavy_endpoint(
        "login",
        login_user_key,
        login_session_key,
    )
    if not allowed:
        return _storm_throttled_response(retry_after=retry_after, reason=reason)

    conn = get_db()
    cursor = conn.cursor()

    identity = get_secure_identity(user.username_hash)
    cursor.execute("SELECT user_id, password FROM users WHERE username_hash=?", (identity,))
    row = cursor.fetchone()

    if row is None or not bcrypt.checkpw(user.password.encode(), row["password"].encode()):
        conn.close()
        storm_guard.note_endpoint_storm(login_user_key)
        return {"status": "error", "msg": "invalid credentials"}

    uid = row["user_id"]
    safe_retry = storm_guard.get_safe_mode_retry_after(uid)
    if safe_retry > 0:
        conn.close()
        return _storm_throttled_response(retry_after=safe_retry, reason="safe_mode")

    storm_guard.maybe_heal_user_state(get_db, uid, device_id=(user.device_id or "").strip() or None)

    requested_main = bool(user.is_main)
    normalized_device_id = (user.device_id or "").strip() or "unknown_device"

    # Preserve existing main-device status for this same device only.
    cursor.execute(
        "SELECT is_main FROM sessions WHERE user_id=? AND device_id=? ORDER BY created_at DESC LIMIT 1",
        (uid, normalized_device_id),
    )
    prev = cursor.fetchone()
    was_main_on_this_device = bool(prev and int(prev["is_main"] or 0) == 1)

    # One-time bootstrap for a freshly registered account so registration flow
    # can establish an initial main device without enabling password-only
    # promotion for existing accounts.
    bootstrap_ts = _fresh_registration_main_bootstrap.get(uid)
    bootstrap_allowed = (
        requested_main
        and bootstrap_ts is not None
        and (time.time() - bootstrap_ts) <= 600
    )

    effective_is_main = was_main_on_this_device or bootstrap_allowed

    # ⭐ EPHEMERAL TOKEN SYSTEM: Store session_secret instead of token for authentication
    # Messages derive ephemeral tokens from: HMAC(session_secret, nonce:timestamp:user_id)
    # This prevents token reuse and central token logging
    
    # ⭐ CRITICAL FIX: Delete old sessions for this device FIRST
    # This ensures old session_secrets don't interfere with the new one
    cursor.execute("DELETE FROM sessions WHERE user_id = ? AND device_id = ?", (uid, normalized_device_id))
    conn.commit()

    # Keep uniqueness for main-device marker when this device is effectively main.
    if effective_is_main:
        cursor.execute(
            "UPDATE sessions SET is_main=0 WHERE user_id=? AND device_id<>?",
            (uid, normalized_device_id),
        )
        conn.commit()

    # Create new session with ephemeral secret
    session_secret = secrets.token_hex(32)  # Store this, derive tokens per-message
    grace_period = datetime.now() + timedelta(hours=24)  # 24-hour grace for offline sync
    
    cursor.execute(
        "INSERT INTO sessions (user_id, device_id, device_name, session_secret, is_main, grace_period_expires) VALUES (?, ?, ?, ?, ?, ?)",
        (
            uid,
            normalized_device_id,
            user.device_name,
            session_secret,
            1 if effective_is_main else 0,
            grace_period.isoformat(),
        )
    )

    conn.commit()

    if bootstrap_allowed:
        _fresh_registration_main_bootstrap.pop(uid, None)

    conn.close()

    # Return session_secret to client for token derivation
    # Client uses this to compute: HMAC(session_secret, nonce:timestamp:user_id) for each message
    return {
        "status": "ok",
        "session_secret": session_secret,
        "user_id": uid,
        "is_main": bool(effective_is_main),
    }


@app.post("/chat/link_submit")
async def link_submit(request: Request, req: LinkSubmit, session_secret: str = Header(default=None, alias="session-secret")):
    check_rate_limit(request, 10)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_main FROM sessions WHERE session_secret=?", (session_secret,))
    sess = cursor.fetchone()
    if not sess or not sess["is_main"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Only main device can authorize links")
    linking_blobs[req.link_id] = {"b": req.encrypted_blob, "t": time.time()}
    conn.close()
    return {"status": "ok"}

@app.post("/chat/link_handshake")
async def link_handshake(request: Request, session_secret: str = Header(default=None, alias="session-secret")):
    """Secondary device requests linking blob from temporary link ID"""
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)
    
    try:
        body = await request.json()
        temp_id = body.get("temp_id")
        if not temp_id:
            raise HTTPException(status_code=400, detail="Missing temp_id")
        
        if temp_id in linking_blobs:
            data = linking_blobs[temp_id]["b"]
            # Don't delete yet - keep it for link_submit to verify
            return {"status": "ok", "blob": data}
        return {"status": "pending"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/chat/link_fetch/{link_id}")
async def link_fetch(link_id: str):
    if link_id in linking_blobs:
        data = linking_blobs[link_id]["b"]
        del linking_blobs[link_id] 
        return {"status": "ok", "blob": data}
    return {"status": "pending"}

@app.get("/chat/sender_certificate")
async def get_sender_certificate(session_secret: str = Header(default=None, alias="session-secret")):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM sessions WHERE session_secret=?", (session_secret,))
    sess = cursor.fetchone()
    if not sess:
        conn.close()
        raise HTTPException(status_code=401)
    uid = sess["user_id"]
    cursor.execute("SELECT public_key FROM users WHERE user_id=?", (uid,))
    pk = cursor.fetchone()["public_key"]
    conn.close()
    expiry = (datetime.now() + timedelta(days=30)).isoformat()
    cert_payload = json.dumps({"user_id": uid, "public_key": pk, "expiry": expiry}, sort_keys=True)
    signature = _private_key.sign(cert_payload.encode())
    encoded_cert = base64.b64encode(cert_payload.encode()).decode()
    encoded_sig = base64.b64encode(signature).decode()
    return {"status": "ok", "certificate": f"{encoded_cert}.{encoded_sig}"}

@app.get("/chat/list_devices")
async def list_devices(session_secret: str = Header(default=None, alias="session-secret")):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM sessions WHERE session_secret=?", (session_secret,))
    sess = cursor.fetchone()
    if not sess:
        conn.close(); raise HTTPException(status_code=401)
    cursor.execute("SELECT device_id, device_name, is_main, created_at FROM sessions WHERE user_id=?", (sess["user_id"],))
    rows = cursor.fetchall()
    conn.close()
    return {"status": "ok", "devices": [dict(r) for r in rows]}

@app.post("/chat/promote_main_device")
async def promote_main_device(session_secret: str = Header(default=None, alias="session-secret")):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, device_id FROM sessions WHERE session_secret=?",
        (session_secret,),
    )
    requester = cursor.fetchone()
    if not requester:
        conn.close(); raise HTTPException(status_code=401)

    uid = requester["user_id"]
    requester_device_id = requester["device_id"]

    cursor.execute("UPDATE sessions SET is_main=0 WHERE user_id=?", (uid,))
    cursor.execute(
        "UPDATE sessions SET is_main=1 WHERE user_id=? AND device_id=?",
        (uid, requester_device_id),
    )
    conn.commit()

    # Collect all other sessions so we can notify them of their demotion.
    cursor.execute(
        "SELECT session_secret, device_id FROM sessions WHERE user_id=? AND device_id != ?",
        (uid, requester_device_id),
    )
    demoted_sessions = [
        {
            "session_secret": row["session_secret"],
            "device_id": row["device_id"],
        }
        for row in cursor.fetchall()
    ]

    # Offline fallback for role convergence across reconnect/restart. The
    # client applies only events that match its local device_unique_id.
    role_events = [
        {
            "type": "device_role_update",
            "is_main": True,
            "target_device_id": requester_device_id,
        }
    ]
    role_events.extend(
        {
            "type": "device_role_update",
            "is_main": False,
            "target_device_id": row["device_id"],
        }
        for row in demoted_sessions
        if row["device_id"]
    )
    for event in role_events:
        cursor.execute(
            "INSERT INTO offline_messages (receiver_id, payload) VALUES (?, ?)",
            (uid, json.dumps(event)),
        )

    cursor.execute(
        "SELECT device_id, device_name, is_main, created_at FROM sessions WHERE user_id=?",
        (uid,),
    )
    rows = cursor.fetchall()
    conn.commit()
    conn.close()

    # Notify any demoted devices that are currently connected so they refresh
    # their role immediately without requiring a restart or manual navigation.
    for demoted in demoted_sessions:
        demoted_secret = demoted["session_secret"]
        demoted_device_id = demoted["device_id"]
        demotion_payload = json.dumps(
            {
                "type": "device_role_update",
                "is_main": False,
                "target_device_id": demoted_device_id,
            }
        )
        if connection_registry.has(uid, demoted_secret):
            sock = connection_registry.get_socket(uid, demoted_secret)
            if sock is not None:
                try:
                    await sock.send_text(demotion_payload)
                except Exception:
                    pass

    return {
        "status": "ok",
        "main_device_id": requester_device_id,
        "devices": [dict(r) for r in rows],
    }

@app.post("/chat/kill_device")
async def kill_device(target_device_id: str, session_secret: str = Header(default=None, alias="session-secret")):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, is_main FROM sessions WHERE session_secret=?", (session_secret,))
    requester = cursor.fetchone()
    if not requester or not requester["is_main"]:
        conn.close(); raise HTTPException(status_code=403)
    uid = requester["user_id"]
    cursor.execute("SELECT session_secret FROM sessions WHERE user_id=? AND device_id=?", (uid, target_device_id))
    target = cursor.fetchone()
    if target:
        target_session_secret = target["session_secret"]
        if connection_registry.has(uid, target_session_secret):
            socket = connection_registry.get_socket(uid, target_session_secret)
            if socket is not None:
                await socket.send_text(json.dumps({"type": "remote_wipe"}))
        cursor.execute("DELETE FROM sessions WHERE session_secret=?", (target_session_secret,))
        session_cache_invalidate(target_session_secret)
        conn.commit()
    conn.close()
    return {"status": "ok"}

class _FcmTokenUpdate(BaseModel):
    fcm_token: str
    platform: Optional[str] = None


@app.post("/chat/update_fcm_token")
def update_fcm_token_endpoint(
    req: _FcmTokenUpdate,
    request: Request,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    """Disabled: custom backends must not store or forward push tokens."""
    check_rate_limit(request, 40)
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)
    return {"status": "ok", "reason": "custom_backend_token_registry_disabled"}


def _sqlite_ts_to_iso8601(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", ""))
    except Exception:
        try:
            parsed = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/internal/token_stats")
def internal_token_stats(_: bool = Depends(verify_admin_access)):
    return {
        "status": "ok",
        "total_tokens": 0,
        "tokens_by_platform": {"android": 0, "ios": 0, "unknown": 0},
        "newest_token_timestamp": None,
        "oldest_token_timestamp": None,
        "tokens_updated_last_24h": 0,
        "tokens_updated_last_1h": 0,
        "reason": "custom_backend_token_registry_disabled",
    }

@app.get("/blocked_users")
async def blocked_users(
    session_secret: str = Header(default=None, alias="session-secret"),
):
    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT blocked_id FROM user_blocks WHERE user_id=? ORDER BY created_at DESC, blocked_id ASC",
        (result,),
    )
    blocked_ids = [row["blocked_id"] for row in cursor.fetchall() if row["blocked_id"]]
    conn.close()
    return {"status": "ok", "blocked_ids": blocked_ids}

@app.post("/block_user")
async def block_user(
    req: BlockUserRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = (req.user_id or "").strip()
    blocked_id = (req.blocked_id or "").strip()
    if not user_id or not blocked_id:
        raise HTTPException(status_code=400, detail="Missing block metadata")
    if user_id != result:
        raise HTTPException(status_code=403, detail="user_id does not match session")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO user_blocks (user_id, blocked_id) VALUES (?, ?)",
        (user_id, blocked_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/unblock_user")
async def unblock_user(
    req: BlockUserRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = (req.user_id or "").strip()
    blocked_id = (req.blocked_id or "").strip()
    if not user_id or not blocked_id:
        raise HTTPException(status_code=400, detail="Missing unblock metadata")
    if user_id != result:
        raise HTTPException(status_code=403, detail="user_id does not match session")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_blocks WHERE user_id=? AND blocked_id=?",
        (user_id, blocked_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/chat/logout")
async def logout(session_secret: str = Header(default=None, alias="session-secret")):
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    session_cache_invalidate(session_secret)
    # Soft-delete: blank the session_secret so the session can no longer be
    # used for authentication, but keep the row so that is_main status is
    # preserved. On re-login the SELECT in the login handler finds the row
    # and correctly restores was_main_on_this_device = True for devices that
    # were previously the main device. The login handler's own DELETE (which
    # runs before INSERTing the new session) will clean up the soft-deleted row.
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE sessions SET session_secret='' WHERE session_secret=?", (session_secret,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/chat/ws_ticket")
async def issue_ws_ticket(request: Request, session_secret: str = Header(default=None, alias="session-secret")):
    check_rate_limit(request, 20)
    storm_guard.maybe_heal_global(get_db)
    if not session_secret:
        _metric_inc("ws_ticket_401_terminal_failures")
        raise HTTPException(status_code=401)

    valid, result = validate_session_token(session_secret)
    if not valid:
        _metric_inc("ws_ticket_401_terminal_failures")
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    allowed, retry_after, reason = storm_guard.allow_heavy_endpoint(
        "ws_ticket",
        user_id,
        session_secret,
    )
    if not allowed:
        return _storm_throttled_response(retry_after=retry_after, reason=reason)

    cached = storm_guard.get_cached_ws_ticket(session_secret)
    if cached is not None:
        cached_ticket, cached_expires_at = cached
        return {
            "status": "ok",
            "ws_ticket": cached_ticket,
            "expires_at": cached_expires_at,
            "transport": _resolve_transport_descriptor(),
            "deduped": True,
        }

    storm_guard.maybe_heal_user_state(get_db, user_id)

    ticket = secrets.token_urlsafe(32)
    ticket_hash = hashlib.sha256(ticket.encode()).hexdigest()
    expires_at = datetime.now() + timedelta(seconds=60)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO ws_tickets (ticket_hash, user_id, session_secret, expires_at, used) VALUES (?, ?, ?, ?, 0)",
        (ticket_hash, user_id, session_secret, expires_at)
    )
    conn.commit()
    conn.close()

    storm_guard.cache_ws_ticket(
        session_secret,
        user_id,
        ticket,
        expires_at.timestamp(),
    )

    return {
        "status": "ok",
        "ws_ticket": ticket,
        "expires_at": expires_at.isoformat(),
        "transport": _resolve_transport_descriptor(),
    }

@app.get("/chat/lookup")
async def lookup_id(
    request: Request,
    h: str,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    check_lookup_rate_limit(request, session_secret)
    conn = get_db()
    cursor = conn.cursor()
    identity = get_secure_identity(h)
    cursor.execute("SELECT user_id, public_key FROM users WHERE username_hash=?", (identity,))
    row = cursor.fetchone()
    conn.close()
    if row: 
        # ⭐ FIXED: Include this server's domain so client knows where user is registered
        return {
            "status": "ok",
            "user_id": row["user_id"],
            "public_key": row["public_key"],
            "server_domain": PUBLIC_HUB_URL if IS_PUBLIC_HUB else "local",
        }
    return {"status": "error", "msg": "not found"}

# --- Storage Endpoints ---
@app.post("/storage/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    token: str = Form(...),
    storage_label: Optional[str] = Form(default=None),
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT u.user_id, u.storage_used FROM users u JOIN sessions s ON u.user_id = s.user_id WHERE s.session_secret=?", (token,))
    user = cursor.fetchone()
    if not user:
        conn.close(); raise HTTPException(status_code=401)
    file_content = await file.read()
    file_size = len(file_content)
    effective_limit = _get_effective_storage_limit(cursor, user["user_id"])
    if user["storage_used"] + file_size > effective_limit:
        conn.close(); return {"status": "error", "msg": "Limit exceeded"}
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, file_id)
    with open(file_path, "wb") as buffer:
        buffer.write(file_content)
    expiry = datetime.now() + timedelta(days=FILE_RETENTION_DAYS)
    label = (storage_label or "").strip().lower()
    if label == "temporary_image":
        stored_filename = "Temporary Image"
    else:
        stored_filename = "Unknown Encrypted File"

    cursor.execute("INSERT INTO file_registry (file_id, owner_id, filename, file_size, expires_at) VALUES (?, ?, ?, ?, ?)",
                   (file_id, user["user_id"], stored_filename, file_size, expiry))
    download_token = secrets.token_urlsafe(32)
    token_expiry = datetime.now() + timedelta(minutes=FILE_TOKEN_TTL_MINUTES)
    cursor.execute("INSERT INTO file_download_tokens (token, file_id, expires_at) VALUES (?, ?, ?)",
                   (download_token, file_id, token_expiry))
    cursor.execute("UPDATE users SET storage_used = storage_used + ? WHERE user_id = ?", (file_size, user["user_id"]))
    conn.commit(); conn.close()
    background_tasks.add_task(cleanup_system)
    return {"status": "ok", "file_id": file_id, "size": file_size, "download_token": download_token}

@app.get("/storage/download/{file_id}")
async def download_file(file_id: str, session_secret: str = Header(default=None, alias="session-secret"), token: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now()

    if token:
        cursor.execute(
            "SELECT t.file_id FROM file_download_tokens t JOIN file_registry f ON f.file_id = t.file_id "
            "WHERE t.token=? AND t.file_id=? AND t.expires_at > ? AND f.expires_at > ?",
            (token, file_id, now, now)
        )
        token_row = cursor.fetchone()
        if token_row:
            file_path = os.path.join(UPLOAD_DIR, file_id)
            if not os.path.exists(file_path):
                cursor.execute("DELETE FROM file_registry WHERE file_id = ?", (file_id,))
                cursor.execute("DELETE FROM file_download_tokens WHERE file_id = ?", (file_id,))
                conn.commit()
                conn.close()
                raise HTTPException(status_code=404, detail="File expired or deleted")
            conn.close()
            return FileResponse(file_path)
        conn.close()
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    if not session_secret:
        conn.close()
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        conn.close()
        raise HTTPException(status_code=401, detail=result)
    user_id = result

    # Verify file exists AND user owns it
    cursor.execute("SELECT owner_id, filename, expires_at FROM file_registry WHERE file_id=?", (file_id,))
    file_row = cursor.fetchone()
    if not file_row:
        conn.close(); raise HTTPException(status_code=404)
    if file_row["expires_at"] is not None and datetime.fromisoformat(str(file_row["expires_at"])) <= now:
        cursor.execute("DELETE FROM file_registry WHERE file_id = ?", (file_id,))
        cursor.execute("DELETE FROM file_download_tokens WHERE file_id = ?", (file_id,))
        conn.commit()
        conn.close(); raise HTTPException(status_code=404, detail="File expired")
    if file_row["owner_id"] != user_id:
        conn.close(); raise HTTPException(status_code=403, detail="Access denied")

    file_path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(file_path):
        cursor.execute("DELETE FROM file_registry WHERE file_id = ?", (file_id,))
        cursor.execute("DELETE FROM file_download_tokens WHERE file_id = ?", (file_id,))
        conn.commit()
        conn.close(); raise HTTPException(status_code=404, detail="File expired or deleted")

    conn.close()
    return FileResponse(file_path)

@app.get("/storage/list")
async def list_files(session_secret: str = Header(default=None, alias="session-secret")):
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)
    
    user_id = result
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT file_id, filename, file_size, upload_at, expires_at "
        "FROM file_registry WHERE owner_id=? AND expires_at > ?",
        (user_id, datetime.now())
    )
    files = cursor.fetchall()
    conn.close()
    return [dict(f) for f in files]


@app.get("/storage/summary")
async def storage_summary(session_secret: str = Header(default=None, alias="session-secret")):
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(storage_used, 0) AS storage_used FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    used = int(row["storage_used"] or 0)
    limit = _get_effective_storage_limit(cursor, user_id)
    conn.close()

    return {
        "status": "ok",
        "storage_used": used,
        "storage_limit": limit,
        "retention_days": FILE_RETENTION_DAYS,
    }

@app.delete("/storage/delete/{file_id}")
async def delete_file(file_id: str, session_secret: str = Header(default=None, alias="session-secret")):
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)
    
    user_id = result
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id, file_size FROM file_registry WHERE file_id=?", (file_id,))
    file_row = cursor.fetchone()
    if not file_row or file_row["owner_id"] != user_id:
        conn.close(); raise HTTPException(status_code=403)
    file_path = os.path.join(UPLOAD_DIR, file_id)
    if os.path.exists(file_path):
        os.remove(file_path)
    cursor.execute("UPDATE users SET storage_used = storage_used - ? WHERE user_id = ?", (file_row["file_size"], user_id))
    cursor.execute("DELETE FROM file_registry WHERE file_id = ?", (file_id,))
    cursor.execute("DELETE FROM file_download_tokens WHERE file_id = ?", (file_id,))
    conn.commit(); conn.close()
    return {"status": "ok"}

# --- Admin Endpoints ---
@app.post("/admin/set_storage")
def set_user_storage(req: StorageUpdate, _: bool = Depends(verify_admin_access)):
    conn = get_db(); cursor = conn.cursor()
    identity = get_secure_identity(req.username_hash)
    cursor.execute("UPDATE users SET storage_limit = ? WHERE username_hash = ?", (req.new_limit_mb * 1024 * 1024, identity))
    conn.commit(); conn.close()
    return {"status": "ok"}

def _normalize_domain(domain: str) -> str:
    cleaned = (domain or "").strip().split('#')[0]
    if cleaned.startswith("https://"):
        cleaned = cleaned.replace("https://", "", 1)
    if cleaned.startswith("http://"):
        cleaned = cleaned.replace("http://", "", 1)
    cleaned = cleaned.split('/')[0]
    cleaned = cleaned.split(':')[0]
    return cleaned

def _normalize_fingerprint(fingerprint: str) -> Optional[str]:
    raw = (fingerprint or "").strip().upper().replace('-', ':')
    if not raw:
        return None

    # Accept sha256/<64 HEX> and normalize to colon-delimited SHA-256.
    if raw.startswith("SHA256/"):
        raw = raw[len("SHA256/"):]

    if ':' not in raw and len(raw) == 64 and all(c in "0123456789ABCDEF" for c in raw):
        raw = ':'.join(raw[i:i+2] for i in range(0, 64, 2))

    parts = raw.split(':')
    if len(parts) != 32:
        return None
    for p in parts:
        if len(p) != 2 or any(c not in "0123456789ABCDEF" for c in p):
            return None
    return raw

@app.post("/admin/add_rotation_pin")
def add_rotation_pin(req: RotationPinUpdate, _: bool = Depends(verify_admin_access)):
    domain = _normalize_domain(req.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Invalid domain")

    normalized_fp = _normalize_fingerprint(req.fingerprint)
    if not normalized_fp:
        raise HTTPException(status_code=400, detail="Invalid fingerprint format")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO rotation_pins (domain, fingerprint) VALUES (?, ?)",
        (domain, normalized_fp),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "domain": domain, "fingerprint": normalized_fp}


# ============================================================
# Identity Key Rotation Endpoints
# ============================================================

@app.get("/identity/rotation")
def get_rotation_announcement():
    """Public endpoint (no auth required).

    Returns the active rotation announcement when the server is in a grace period,
    so clients can verify the legitimacy of a signing-key change before trusting it.
    Returns 404 when no active rotation announcement exists.
    """
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT old_fingerprint, new_fingerprint, new_public_key, "
            "valid_from, grace_period_ends_at, signature "
            "FROM identity_key_rotation_announcements "
            "WHERE grace_period_ends_at > ? "
            "ORDER BY id DESC LIMIT 1",
            (time.time(),),
        )
        ann = cursor.fetchone()
    finally:
        conn.close()

    if ann is None:
        raise HTTPException(status_code=404, detail="No active rotation announcement")

    return {
        "status": "ok",
        "old_fingerprint": ann["old_fingerprint"],
        "new_fingerprint": ann["new_fingerprint"],
        "new_public_key": ann["new_public_key"],
        "valid_from": ann["valid_from"],
        "grace_period_ends_at": ann["grace_period_ends_at"],
        "signature": ann["signature"],
    }


@app.get("/admin/identity_rotation_config")
def get_identity_rotation_config(_: bool = Depends(verify_admin_access)):
    """Return identity rotation configuration and current status.

    Simple Mode  (IS_PUBLIC_HUB=True): rotation is always disabled – long-term key.
    Professional Mode (custom server): rotation may be enabled by the administrator.
    """
    current_fp = _compute_identity_fingerprint(_server_public_key)
    in_grace = _grace_period_ends_at is not None and _grace_period_ends_at > time.time()

    if IS_PUBLIC_HUB:
        return {
            "status": "ok",
            "mode": "simple",
            "rotation_enabled": False,
            "current_fingerprint": current_fp,
            "note": (
                "Public hub uses a permanent long-term identity key. "
                "Automatic rotation is not available."
            ),
        }

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM identity_key_rotation_config LIMIT 1")
        config = cursor.fetchone()
        cursor.execute(
            "SELECT old_fingerprint, new_fingerprint, valid_from, grace_period_ends_at, created_at "
            "FROM identity_key_rotation_announcements ORDER BY id DESC LIMIT 1"
        )
        last_ann = cursor.fetchone()
    finally:
        conn.close()

    result: dict = {
        "status": "ok",
        "mode": "professional",
        "rotation_enabled": bool(config["rotation_enabled"]) if config else False,
        "rotation_interval_days": config["rotation_interval_days"] if config else ROTATION_INTERVAL_DAYS,
        "grace_period_days": config["grace_period_days"] if config else ROTATION_GRACE_PERIOD_DAYS,
        "next_rotation_at": config["next_rotation_at"] if config else None,
        "current_fingerprint": current_fp,
        "in_grace_period": in_grace,
        "grace_period_ends_at": _grace_period_ends_at if in_grace else None,
    }
    if last_ann:
        result["last_rotation"] = {
            "old_fingerprint": last_ann["old_fingerprint"],
            "new_fingerprint": last_ann["new_fingerprint"],
            "valid_from": last_ann["valid_from"],
            "grace_period_ends_at": last_ann["grace_period_ends_at"],
            "created_at": last_ann["created_at"],
        }
    return result


@app.post("/admin/identity_rotation_config")
def set_identity_rotation_config(
    req: IdentityRotationConfigUpdate,
    _: bool = Depends(verify_admin_access),
):
    """Update identity key rotation settings for a custom server.

    Blocked on the public hub (IS_PUBLIC_HUB=True).
    """
    if IS_PUBLIC_HUB:
        raise HTTPException(
            status_code=403,
            detail=(
                "Public hub uses a permanent long-term identity key. "
                "Rotation configuration is not permitted."
            ),
        )
    if req.rotation_interval_days < 1:
        raise HTTPException(status_code=400, detail="rotation_interval_days must be >= 1")
    if req.grace_period_days < 1:
        raise HTTPException(status_code=400, detail="grace_period_days must be >= 1")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO identity_key_rotation_config
                (id, rotation_enabled, rotation_interval_days, grace_period_days, updated_at)
            VALUES (1, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                rotation_enabled       = excluded.rotation_enabled,
                rotation_interval_days = excluded.rotation_interval_days,
                grace_period_days      = excluded.grace_period_days,
                updated_at             = excluded.updated_at
            """,
            (int(req.rotation_enabled), req.rotation_interval_days, req.grace_period_days),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok"}


@app.post("/admin/rotate_now")
def rotate_identity_key_now(_: bool = Depends(verify_admin_access)):
    """Immediately rotate the server identity key (custom servers only).

    Generates a new Ed25519 keypair, creates a rotation announcement signed by the
    old private key, and begins a grace period during which both the old and new
    keys are considered valid.

    Blocked on the public hub.
    """
    if IS_PUBLIC_HUB:
        raise HTTPException(
            status_code=403,
            detail=(
                "Public hub uses a permanent long-term identity key. "
                "Manual rotation is not permitted."
            ),
        )
    try:
        return _perform_identity_key_rotation()
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rotation failed: {exc}")


@app.get("/chat/pin_list")
async def get_signed_pin_list(
    session_secret: str = Header(default=None, alias="session-secret"),
    domain: Optional[str] = None,
):
    storm_guard.maybe_heal_global(get_db)
    if not session_secret:
        raise HTTPException(status_code=401)

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    effective_domain = _normalize_domain(domain or PUBLIC_HUB_URL)
    if not effective_domain:
        raise HTTPException(status_code=400, detail="Invalid domain")

    safe_retry = storm_guard.get_safe_mode_retry_after(result)
    if safe_retry > 0:
        cached_safe = storm_guard.get_cached_pin_list_response(result, effective_domain)
        if cached_safe is not None:
            cached_safe["safe_mode"] = True
            cached_safe["retry_after"] = safe_retry
            return cached_safe
        return _storm_throttled_response(retry_after=safe_retry, reason="safe_mode")

    allowed, retry_after, reason = storm_guard.allow_heavy_endpoint(
        "pin_list",
        result,
        session_secret,
    )
    if not allowed:
        cached_throttled = storm_guard.get_cached_pin_list_response(result, effective_domain)
        if cached_throttled is not None:
            cached_throttled["throttled"] = True
            cached_throttled["retry_after"] = retry_after
            return cached_throttled
        return _storm_throttled_response(retry_after=retry_after, reason=reason)

    # ⭐ PERF FIX: Skip DB round-trip for repeated pin checks within the dedup window.
    cache_key = f"{session_secret}:{effective_domain}"
    if _is_pin_list_too_soon(cache_key):
        hinted_pins = TRANSPORT_PIN_HINTS.get(effective_domain, [])
        fast_pins = [p for p in (_normalize_fingerprint(raw) for raw in hinted_pins) if p]
        fast_issued_at = datetime.now().isoformat()
        fast_expires_at = (datetime.now() + timedelta(days=45)).isoformat()
        fast_payload_obj = {
            "domain": effective_domain,
            "pins": fast_pins,
            "issued_at": fast_issued_at,
            "expires_at": fast_expires_at,
        }
        fast_payload_json = json.dumps(fast_payload_obj, sort_keys=True, separators=(",", ":"))
        fast_signature = _private_key.sign(fast_payload_json.encode())
        fast_signing_key_raw = _server_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        in_grace = _grace_period_ends_at is not None and _grace_period_ends_at > time.time()
        fast_payload = {
            "status": "ok",
            "payload": base64.b64encode(fast_payload_json.encode()).decode(),
            "signature": base64.b64encode(fast_signature).decode(),
            "signing_key": base64.b64encode(fast_signing_key_raw).decode(),
            "pins": dict(TRANSPORT_PIN_HINTS),
            "auto_sync_default": PIN_SYNC_AUTO_DEFAULT,
            "force_manual_sync": PIN_SYNC_FORCE_MANUAL,
            "identity_fingerprint": _compute_identity_fingerprint(_server_public_key),
            "in_grace_period": in_grace,
        }
        storm_guard.cache_pin_list_response(result, effective_domain, fast_payload)
        return fast_payload

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT fingerprint FROM rotation_pins WHERE domain = ? ORDER BY added_at DESC LIMIT 5",
        (effective_domain,),
    )
    db_pins = [r["fingerprint"] for r in cursor.fetchall()]
    conn.close()

    hinted_pins = TRANSPORT_PIN_HINTS.get(effective_domain, [])
    normalized_pins = []
    seen_pins = set()
    for raw_pin in [*db_pins, *hinted_pins]:
        normalized = _normalize_fingerprint(raw_pin)
        if not normalized:
            continue
        if normalized in seen_pins:
            continue
        seen_pins.add(normalized)
        normalized_pins.append(normalized)

    issued_at = datetime.now().isoformat()
    expires_at = (datetime.now() + timedelta(days=45)).isoformat()
    payload_obj = {
        "domain": effective_domain,
        "pins": normalized_pins,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload_json = json.dumps(payload_obj, sort_keys=True, separators=(",", ":"))
    signature = _private_key.sign(payload_json.encode())

    signing_public_key_raw = _server_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Expose the current identity fingerprint so clients can detect rotation.
    # Also signal when we are inside a grace period so clients can fetch the
    # /identity/rotation announcement and verify before trusting the new key.
    in_grace = _grace_period_ends_at is not None and _grace_period_ends_at > time.time()

    response_payload = {
        "status": "ok",
        "payload": base64.b64encode(payload_json.encode()).decode(),
        "signature": base64.b64encode(signature).decode(),
        "signing_key": base64.b64encode(signing_public_key_raw).decode(),
        "pins": dict(TRANSPORT_PIN_HINTS),
        "auto_sync_default": PIN_SYNC_AUTO_DEFAULT,
        "force_manual_sync": PIN_SYNC_FORCE_MANUAL,
        "identity_fingerprint": _compute_identity_fingerprint(_server_public_key),
        "in_grace_period": in_grace,
    }
    storm_guard.cache_pin_list_response(result, effective_domain, response_payload)
    return response_payload

# --- WebSocket Engine (Multicast & Per-Device Mailbox) ---

async def _reject_ws_auth(websocket: WebSocket, conn: Optional[sqlite3.Connection] = None):
    try:
        # Small jitter to reduce auth-probing signal quality
        await asyncio.sleep((120 + secrets.randbelow(120)) / 1000)
    except Exception:
        pass
    try:
        await websocket.close(code=1008)
    except Exception:
        pass
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

@app.websocket("/ws")
@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    if WS_CONNECTION_MODE != "tunnel":
        request_path = (websocket.url.path or "").rstrip("/") or "/"
        host = _normalize_ws_host(websocket.headers.get("host") or "")
        raw_origin = (websocket.headers.get("origin") or "").strip()
        origin = _normalize_ws_origin(raw_origin)
        allowed_hosts = _build_allowed_ws_hosts()
        allowed_origins = _build_allowed_ws_origins()

        if request_path != "/ws":
            await websocket.close(code=4403)
            return

        if host not in allowed_hosts:
            await websocket.close(code=4403)
            return

        # Native mobile WebSocket stacks may omit Origin; validate only when present.
        if raw_origin and origin not in allowed_origins:
            await websocket.close(code=4403)
            return

    await websocket.accept()
    session_secret = None
    uid = None
    ws_registered = False
    disconnect_reason = "unknown"

    await asyncio.sleep(0.2)
    conn = get_db(); cursor = conn.cursor()
    try:
        raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=3)
        auth_msg = json.loads(raw_auth)
        if auth_msg.get("type") != "ws_auth":
            await _reject_ws_auth(websocket, conn); return

        ws_ticket = auth_msg.get("ws_ticket")
        if not ws_ticket:
            await _reject_ws_auth(websocket, conn); return

        ticket_hash = hashlib.sha256(ws_ticket.encode()).hexdigest()
        cursor.execute(
            "SELECT user_id, session_secret, expires_at, used FROM ws_tickets WHERE ticket_hash=?",
            (ticket_hash,)
        )
        ticket_row = cursor.fetchone()

        if not ticket_row:
            await _reject_ws_auth(websocket, conn); return

        if ticket_row["used"] == 1:
            await _reject_ws_auth(websocket, conn); return

        try:
            expiry_time = datetime.fromisoformat(ticket_row["expires_at"])
        except Exception:
            await _reject_ws_auth(websocket, conn); return

        if expiry_time < datetime.now():
            await _reject_ws_auth(websocket, conn); return

        session_secret = ticket_row["session_secret"]
        uid = ticket_row["user_id"]

        cursor.execute("UPDATE ws_tickets SET used=1 WHERE ticket_hash=?", (ticket_hash,))
        conn.commit()

        await websocket.send_text(json.dumps({"type": "ws_auth_ok"}))
    except Exception:
        await _reject_ws_auth(websocket, conn); return

    if uid is None or not session_secret:
        await _reject_ws_auth(websocket, conn); return

    connection_registry.register(uid, session_secret, websocket)
    ws_registered = True
    
    # ⭐ CHANGED: Don't auto-drain offline messages on WebSocket connect
    # This ensures offline_messages table shows accurate unread count for cross-server checks
    # Messages will be fetched on-demand via /chat/get_pending_messages when client switches servers
    # This fixes the issue where unread_count shows 0 even though messages exist
    print(f"✅ WebSocket connected for user {uid}, NOT auto-draining offline messages") 
    
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "delivery_ack":
                delivery_id = (msg.get("delivery_id") or "").strip()
                if delivery_id:
                    await _consume_pending_delivery_ack(delivery_id)
                continue

            if msg.get("type") == "client_presence":
                app_foreground = msg.get("app_foreground") is True
                active_chat_id = str(msg.get("active_chat_id") or "").strip()
                active_group_id = str(msg.get("active_group_id") or "").strip()
                active_peer_user_id = str(msg.get("active_peer_user_id") or "").strip()
                _connection_registry_set_session_state(
                    uid,
                    session_secret,
                    {
                        "app_foreground": app_foreground,
                        "active_chat_id": active_chat_id,
                        "active_group_id": active_group_id,
                        "active_peer_user_id": active_peer_user_id,
                        "updated_at": int(time.time()),
                    },
                )
                continue

            if msg.get("type") == "read_up_to":
                peer_id = str(msg.get("peer_id") or msg.get("to_id") or "").strip()
                msg_id = str(msg.get("msg_id") or "").strip()
                reader_username = str(msg.get("reader_username") or "").strip()
                if not peer_id or not msg_id:
                    continue
                _metric_inc("read_up_to_sent")

                client_timestamp = msg.get("client_timestamp")
                device_id = (msg.get("device_id") or "").strip() or None
                msg_rank = _parse_msg_rank(
                    msg.get("msg_rank"),
                    str(client_timestamp) if client_timestamp is not None else None,
                )
                try:
                    advanced, applied_msg_id, applied_rank = _upsert_read_cursor(
                        conn=conn,
                        cursor=cursor,
                        user_id=uid,
                        peer_id=peer_id,
                        msg_id=msg_id,
                        msg_rank=msg_rank,
                        device_id=device_id,
                    )
                    mark_read_up_to(
                        cursor=cursor,
                        user_id=uid,
                        peer_id=peer_id,
                        msg_rank=applied_rank,
                    )
                    conn.commit()
                except Exception as cursor_write_error:
                    print(
                        "[WARN] read_up_to cursor write failed "
                        f"user={uid} peer={peer_id} msg_id={msg_id} rank={msg_rank} "
                        f"error={cursor_write_error}"
                    )
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    continue

                server_timestamp = datetime.now(timezone.utc).isoformat()
                _metric_inc("read_up_to_ack_received")
                try:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "read_up_to_ack",
                                "peer_id": peer_id,
                                "applied_msg_id": applied_msg_id,
                                "applied_msg_rank": applied_rank,
                                "server_timestamp": server_timestamp,
                            }
                        )
                    )
                except Exception:
                    pass

                read_payload = json.dumps(
                    {
                        "type": "read_up_to",
                        "user_id": uid,
                        "reader_username": reader_username,
                        "peer_id": peer_id,
                        "applied_msg_id": applied_msg_id,
                        "applied_msg_rank": applied_rank,
                        "server_timestamp": server_timestamp,
                        "updated_by_device": device_id,
                        "advanced": advanced,
                    }
                )

                peer_sessions = dict(connection_registry.get_user_sessions(peer_id))
                for _, socket in list(peer_sessions.items()):
                    try:
                        await socket.send_text(read_payload)
                    except Exception:
                        pass

                own_sessions = dict(connection_registry.get_user_sessions(uid))
                for own_session_secret, socket in list(own_sessions.items()):
                    if own_session_secret == session_secret:
                        continue
                    try:
                        await socket.send_text(read_payload)
                    except Exception:
                        pass
                continue
            
            # ⭐ EPHEMERAL TOKEN VERIFICATION: Verify single-use token for this message
            # Expected format:
            # {
            #   "ephemeral_token": "...",
            #   "nonce": "...",
            #   "timestamp": "...",
            #   ... other fields
            # }
            from_id = uid
            ephemeral_token = msg.get("ephemeral_token")
            nonce = msg.get("nonce")
            timestamp = msg.get("timestamp")
            
            # System messages don't need ephemeral tokens (lightweight system messages)
            msg_type = msg.get("type")
            if msg_type in ("delivery_receipt", "delete_message", "delivery_ack", "read_up_to", "read_up_to_ack"):
                # System messages skip ephemeral token validation
                pass
            else:
                # ⭐ FIXED: Pass the session_secret from the WebSocket connection context
                # This ensures we validate against the EXACT session that sent this message,
                # not just any session for the user. Critical for multi-session scenarios.
                # Validate ephemeral token (critical for security)
                if ephemeral_token and nonce and timestamp:
                    if not verify_ephemeral_token(from_id, nonce, timestamp, ephemeral_token, session_secret=session_secret):
                        # Invalid token - reject message
                        _metric_inc("ephemeral_auth_invalid_drops")
                        print(f"[❌] Ephemeral token validation FAILED: user={from_id} session={session_secret[:8] if session_secret else 'none'}... msg_type={msg_type}")
                        continue
                else:
                    # Missing ephemeral token fields - reject
                    _metric_inc("ephemeral_auth_missing_drops")
                    print(f"[❌] Missing ephemeral token fields: user={from_id} session={session_secret[:8] if session_secret else 'none'}... msg_type={msg_type} "
                          f"has_token={bool(ephemeral_token)} has_nonce={bool(nonce)} has_ts={bool(timestamp)}")
                    continue
            
            # ⭐ TRUE SEALED-SENDER: Server does NOT see sender metadata
            # Server only routes based on to_id; recipient decrypts inner envelope to see sender
            is_sealed = msg.get("protocol_version") == 1 or msg.get("type") == "sealed_sender"
            if is_sealed:
                preview_envelope_v1 = msg.get("preview_envelope_v1") if "preview_envelope_v1" in msg else None
                # Sealed message: server sees only to_id and sealed_payload
                # Do NOT inject from_id, do NOT verify sender identity
                # Only recipient can decrypt and verify sender after delivery
                msg = {
                    "protocol_version": 1,
                    "type": "sealed_sender",
                    "to_id": msg.get("to_id"),
                    "sealed_payload": msg.get("sealed_payload"),
                    "sender_certificate": msg.get("sender_certificate"),
                    # Preserve existing outer hints if the client provided them.
                    # This avoids enabling ack-timeout fallback for reaction metadata events.
                    "msg_type": msg.get("msg_type"),
                    "push_type": msg.get("push_type"),
                    "notification_preview_text": msg.get("notification_preview_text") or msg.get("push_preview_text"),
                    # ⭐ Issue 2 fix: preserve msg_id so upsert_unread_message can write
                    # the unread row. The server never reads this field from the outer
                    # sealed envelope; it is only used for unread tracking and push.
                    "msg_id": msg.get("msg_id"),
                }
                if preview_envelope_v1 is not None:
                    msg["preview_envelope_v1"] = preview_envelope_v1
            else:
                # Legacy unsealed messages (system messages, groups): server can see sender
                msg["from_id"] = uid 
            
            # Mark start time for routing latency measurement (send-to-deliver path)
            _t_route_start = time.time()
            payload = json.dumps(msg)
            to_id = msg.get("to_id") 

            if to_id and _is_blockable_direct_message(msg, str(uid), str(to_id)):
                if _recipient_has_blocked_sender(cursor, str(to_id), str(uid)):
                    blocked_msg_id = str(msg.get("msg_id") or "").strip() or None
                    await _send_sender_blocked_notice(
                        websocket,
                        peer_id=str(to_id),
                        msg_id=blocked_msg_id,
                    )
                    print(
                        f"[INFO] Blocked direct message sender={uid} recipient={to_id} "
                        f"msg_id={blocked_msg_id or 'n/a'}"
                    )
                    continue

            if msg_type == "delivery_receipt" and to_id:
                client_version_raw = msg.get("client_version")
                try:
                    client_version = int(client_version_raw) if client_version_raw is not None else 1
                except Exception:
                    client_version = 1

                if client_version < READ_UP_TO_MIN_CLIENT_VERSION:
                    msg_id = str(msg.get("msg_id") or "").strip()
                    if msg_id:
                        msg_rank = _parse_msg_rank(
                            msg.get("msg_rank"),
                            str(msg.get("timestamp") or ""),
                        )
                        _, applied_msg_id, applied_rank = _upsert_read_cursor(
                            conn=conn,
                            cursor=cursor,
                            user_id=uid,
                            peer_id=str(to_id),
                            msg_id=msg_id,
                            msg_rank=msg_rank,
                            device_id="legacy_delivery_receipt_shim",
                        )
                        mark_read_up_to(
                            cursor=cursor,
                            user_id=uid,
                            peer_id=str(to_id),
                            msg_rank=applied_rank,
                        )
                        conn.commit()
                        shim_payload = json.dumps(
                            {
                                "type": "read_up_to",
                                "user_id": uid,
                                "peer_id": str(to_id),
                                "applied_msg_id": applied_msg_id,
                                "applied_msg_rank": applied_rank,
                                "server_timestamp": datetime.now(timezone.utc).isoformat(),
                                "updated_by_device": "legacy_delivery_receipt_shim",
                                "advanced": True,
                            }
                        )
                        peer_sessions = dict(connection_registry.get_user_sessions(str(to_id)))
                        for _, socket in list(peer_sessions.items()):
                            try:
                                await socket.send_text(shim_payload)
                            except Exception:
                                pass

            if to_id:
                if to_id != uid:
                    upsert_unread_message(
                        cursor=cursor,
                        user_id=str(to_id),
                        sender_id=str(uid),
                        msg=msg,
                    )

                # Recipient session rows may be stale/missing; route by live socket first.
                cursor.execute("SELECT session_secret FROM sessions WHERE user_id = ?", (to_id,))
                dest_sessions = cursor.fetchall()
                delivered_to_online_session = False
                delivered_online_session_secrets = set()

                local_sessions = dict(connection_registry.get_user_sessions(to_id))
                for dest_session_secret, socket in list(local_sessions.items()):
                    try:
                        outbound_msg = dict(msg)
                        track_ack = msg.get("type") not in (
                            "delivery_receipt",
                            "read_receipt",
                            "read_up_to",
                            "read_up_to_ack",
                            "delivery_ack",
                            "delete_message",
                        )
                        # MessageType.reaction (msg_type == 9) must not be ack-tracked.
                        # If the delivery_ack never arrives (e.g. iOS backgrounds mid-flight),
                        # _pending_delivery_timeout_worker would re-queue the reaction as an
                        # offline push, producing a second duplicate notification.
                        if track_ack:
                            _raw_inner = msg.get("msg_type")
                            _inner_int: Optional[int] = None
                            if isinstance(_raw_inner, int):
                                _inner_int = _raw_inner
                            elif isinstance(_raw_inner, str):
                                try:
                                    _inner_int = int(_raw_inner.strip())
                                except Exception:
                                    pass
                            _push_type = str(msg.get("push_type") or "").strip().lower()
                            _reaction_emoji = str(msg.get("reaction_emoji") or "").strip()
                            _reaction_target_id = str(msg.get("reaction_target_id") or "").strip()
                            if (
                                _inner_int == 9
                                or _push_type == "reaction"
                                or bool(_reaction_emoji)
                                or bool(_reaction_target_id)
                            ):
                                track_ack = False
                        delivery_id = None
                        if track_ack:
                            delivery_id = uuid.uuid4().hex
                            outbound_msg["_server_delivery_id"] = delivery_id

                        outbound_payload = json.dumps(outbound_msg)
                        await socket.send_text(outbound_payload)
                        delivered_to_online_session = True
                        delivered_online_session_secrets.add(dest_session_secret)
                        latency_tracker.record((time.time() - _t_route_start) * 1000)

                        if track_ack and delivery_id:
                            await _register_pending_delivery_ack(
                                delivery_id=delivery_id,
                                to_id=to_id,
                                sender_id=uid,
                                msg=msg,
                                payload=payload,
                            )
                    except Exception:
                        connection_registry.remove(to_id, dest_session_secret)
                        _metric_inc("ghost_socket_cleanup")
                        print(
                            f"[WARN] Ghost socket cleanup during send: user={to_id} "
                            f"session={dest_session_secret[:8]}..."
                        )

                presence_sessions = connection_registry.get_presence_sessions(to_id)
                for s_row in dest_sessions:
                    dest_session_secret = s_row["session_secret"]
                    if dest_session_secret in delivered_online_session_secrets:
                        continue
                    if dest_session_secret in local_sessions:
                        continue
                    if dest_session_secret in presence_sessions:
                        # Redis fanout publish is best-effort and does not prove remote
                        # websocket delivery; keep offline fallback eligible.
                        fanout_bridge.publish(to_id, dest_session_secret, payload)

                if not delivered_to_online_session:
                    target_server = msg.get("target_server_domain")
                    if target_server and target_server != PUBLIC_HUB_URL:
                        print(f"ℹ️ User {to_id} not active on this server. Message targeted for {target_server}")
                    reason = "missing_session" if not dest_sessions else "no_active_socket"
                    push_job = _queue_offline_with_push(
                        conn=conn,
                        cursor=cursor,
                        to_id=to_id,
                        sender_id=uid,
                        payload=payload,
                        msg=msg,
                        reason=reason,
                        exclude_session_secrets=delivered_online_session_secrets,
                    )
                    conn.commit()
                    if push_job:
                        try:
                            enqueue_push(**push_job)
                        except Exception as exc:
                            print(f"[WARN] Delegated wake enqueue failed after message commit: {exc}")
                else:
                    conn.commit()
                    should_push = (
                        to_id != uid and
                        should_send_push_for_message(msg)
                    )
                    if should_push:
                        try:
                            push_enabled = bool(push_delivery_status().get("enabled"))
                        except Exception as exc:
                            push_enabled = False
                            print(f"[WARN] Push status check failed; skipping delegated wake: {exc}")

                        if push_enabled:
                            push_excluded_session_secrets = _build_online_push_exclusions(
                                cursor=cursor,
                                to_id=str(to_id),
                                sender_id=uid,
                                msg=msg,
                                delivered_session_secrets=delivered_online_session_secrets,
                            )
                            push_data = _build_delegated_push_metadata(msg)
                            try:
                                enqueue_push(
                                    to_id,
                                    GENERIC_PUSH_TITLE,
                                    GENERIC_PUSH_BODY,
                                    data_payload=push_data,
                                    exclude_session_secrets=list(push_excluded_session_secrets),
                                )
                            except Exception as exc:
                                print(f"[WARN] Delegated wake enqueue failed after message commit: {exc}")
                        else:
                            _log_skipped_push_throttled(
                                to_id=to_id,
                                sender_id=uid,
                                delivered_online=delivered_to_online_session,
                                msg_type=str(msg.get("type")),
                                reason="push_disabled",
                            )
                    elif to_id != uid:
                        _log_skipped_push_throttled(
                            to_id=to_id,
                            sender_id=uid,
                            delivered_online=delivered_to_online_session,
                            msg_type=str(msg.get("type")),
                            reason="policy",
                        )

            # ⭐ SELF-SYNC: Always deliver to the sender's other sessions.
            # For unsealed messages the plain payload is broadcast as-is.
            # For sealed messages the client sends an explicit self-addressed
            # sealed copy (to_id == uid), which the server routes through the
            # main delivery block above.  That path handles online delivery.
            # The server-side fallback below covers offline sessions in BOTH
            # cases, ensuring no other-session is silently dropped.
            # ⭐ Issue 7 fix: removed `if not is_sealed:` guard so that the
            # offline-store write runs for sealed messages too.
            cursor.execute("SELECT session_secret FROM sessions WHERE user_id = ? AND session_secret != ?", (uid, session_secret))
            my_other_sessions = cursor.fetchall()
            has_offline_self_session = False
            self_sync_delivered_online = False
            for s_row in my_other_sessions:
                other_session_secret = s_row["session_secret"]
                if connection_registry.has(uid, other_session_secret):
                    if not is_sealed:
                        try:
                            socket = connection_registry.get_socket(uid, other_session_secret)
                            if socket is None:
                                raise RuntimeError("socket_missing")
                            await socket.send_text(payload)
                            self_sync_delivered_online = True
                        except Exception:
                            connection_registry.remove(uid, other_session_secret)
                            has_offline_self_session = True
                    else:
                        self_sync_delivered_online = True
                else:
                    has_offline_self_session = True

            if has_offline_self_session and not self_sync_delivered_online:
                cursor.execute("INSERT INTO offline_messages (receiver_id, payload) VALUES (?, ?)", (uid, payload))

            conn.commit()

            if msg.get("type") != "delivery_receipt":
                try:
                    await websocket.send_text(payload)
                except Exception:
                    # Connection died (e.g. concurrent ping/pong race dropped it)
                    disconnect_reason = "echo_send_failed"
                    break

    except WebSocketDisconnect:
        disconnect_reason = "websocket_disconnect"
    except Exception as ws_error:
        disconnect_reason = f"ws_loop_exception:{type(ws_error).__name__}"
        print(f"[WARN] websocket loop error user={uid}: {ws_error}")
    finally:
        if ws_registered and uid and session_secret:
            was_present = connection_registry.has(uid, session_secret)
            connection_registry.remove(uid, session_secret)
            if disconnect_reason != "websocket_disconnect" and was_present:
                _metric_inc("ghost_socket_cleanup")
                print(
                    f"[INFO] Ghost socket cleanup: user={uid} reason={disconnect_reason}"
                )
        conn.close()

# --- System Management ---
@app.post("/chat/deregister")
def deregister(user: UserLogin):
    conn = get_db(); cursor = conn.cursor()
    identity = get_secure_identity(user.username_hash)
    cursor.execute("SELECT user_id, password FROM users WHERE username_hash=?", (identity,))
    row = cursor.fetchone()
    if row is None or not bcrypt.checkpw(user.password.encode(), row["password"].encode()):
        conn.close(); return {"status": "error", "msg": "invalid"}
    uid = row["user_id"]
    cursor.execute("SELECT file_id FROM file_registry WHERE owner_id=?", (uid,))
    for f in cursor.fetchall():
        fpath = os.path.join(UPLOAD_DIR, f["file_id"])
        if os.path.exists(fpath): os.remove(fpath)

    # Remove any live sockets before deleting session rows.
    for session_key, _ in list(connection_registry.get_user_sessions(uid).items()):
        connection_registry.remove(uid, session_key)

    # Cleanup queued/offline payloads that reference this user as sender and/or target.
    cursor.execute(
        "SELECT id FROM offline_messages WHERE receiver_id=?",
        (uid,),
    )
    owned_offline_ids = [int(r["id"]) for r in cursor.fetchall() if r["id"] is not None]

    cursor.execute(
        "SELECT id FROM offline_messages WHERE payload LIKE ? OR payload LIKE ?",
        (f'%"from_id":"{uid}"%', f'%"to_id":"{uid}"%'),
    )
    referenced_offline_ids = [int(r["id"]) for r in cursor.fetchall() if r["id"] is not None]

    all_offline_ids = sorted(set(owned_offline_ids + referenced_offline_ids))
    if all_offline_ids:
        placeholders = ",".join("?" * len(all_offline_ids))
        cursor.execute(
            f"DELETE FROM pending_message_leases WHERE message_id IN ({placeholders})",
            all_offline_ids,
        )
        cursor.execute(
            f"DELETE FROM offline_messages WHERE id IN ({placeholders})",
            all_offline_ids,
        )

    # Remove unread and read-cursor state tied to this account.
    cursor.execute("DELETE FROM unread_messages WHERE user_id=? OR peer_id=?", (uid, uid))
    cursor.execute("DELETE FROM conversation_read_state WHERE user_id=? OR peer_id=?", (uid, uid))

    # Cleanup residual auth/runtime artifacts.
    cursor.execute("DELETE FROM ws_tickets WHERE user_id=?", (uid,))
    cursor.execute("DELETE FROM pending_message_leases WHERE receiver_id=?", (uid,))
    
    # Corrected DELETE statements with bindings
    cursor.execute("DELETE FROM users WHERE user_id=?", (uid,))
    cursor.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    cursor.execute("DELETE FROM offline_messages WHERE receiver_id=?", (uid,))
    cursor.execute("DELETE FROM file_registry WHERE owner_id=?", (uid,))
    
    conn.commit(); conn.close()
    return {"status": "ok"}


def _collect_user_offline_message_ids(cursor: sqlite3.Cursor, user_id: str) -> list[int]:
    """Collect offline queue row IDs that are owned by or reference the target user."""
    cursor.execute(
        "SELECT id FROM offline_messages WHERE receiver_id=?",
        (user_id,),
    )
    owned_ids = [int(r["id"]) for r in cursor.fetchall() if r["id"] is not None]

    cursor.execute(
        "SELECT id FROM offline_messages WHERE payload LIKE ? OR payload LIKE ?",
        (f'%\"from_id\":\"{user_id}\"%', f'%\"to_id\":\"{user_id}\"%'),
    )
    referenced_ids = [
        int(r["id"]) for r in cursor.fetchall() if r["id"] is not None
    ]

    return sorted(set(owned_ids + referenced_ids))


def _remove_user_owned_files(cursor: sqlite3.Cursor, user_id: str) -> int:
    cursor.execute("SELECT file_id FROM file_registry WHERE owner_id=?", (user_id,))
    file_ids = [str(r["file_id"]) for r in cursor.fetchall() if r["file_id"]]
    if not file_ids:
        return 0

    removed = 0
    for file_id in file_ids:
        fpath = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass
        removed += 1

    placeholders = ",".join("?" * len(file_ids))
    cursor.execute(
        f"DELETE FROM file_download_tokens WHERE file_id IN ({placeholders})",
        file_ids,
    )
    cursor.execute(
        f"DELETE FROM file_registry WHERE file_id IN ({placeholders})",
        file_ids,
    )

    # Ensure storage accounting is not left stale after wiping file rows.
    cursor.execute("UPDATE users SET storage_used = 0 WHERE user_id=?", (user_id,))
    return removed


@app.post("/chat/account_reset")
def account_reset(
    req: AccountResetRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    """Selective reset for corrupted accounts.

    friend_only and account_reset currently share the same server behavior
    because friend/group state is client-managed and not persisted as backend
    relationship tables.
    """
    if not session_secret:
        raise HTTPException(status_code=401, detail="Missing session")

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT password FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if not bcrypt.checkpw(req.password.encode(), row["password"].encode()):
        conn.close()
        raise HTTPException(status_code=403, detail="Invalid password")

    for session_key, _ in list(connection_registry.get_user_sessions(user_id).items()):
        connection_registry.remove(user_id, session_key)

    message_ids = _collect_user_offline_message_ids(cursor, user_id)
    if message_ids:
        placeholders = ",".join("?" * len(message_ids))
        cursor.execute(
            f"DELETE FROM pending_message_leases WHERE message_id IN ({placeholders})",
            message_ids,
        )
        cursor.execute(
            f"DELETE FROM offline_messages WHERE id IN ({placeholders})",
            message_ids,
        )

    cursor.execute(
        "DELETE FROM pending_message_leases WHERE receiver_id=?",
        (user_id,),
    )
    cursor.execute(
        "DELETE FROM unread_messages WHERE user_id=? OR peer_id=?",
        (user_id, user_id),
    )
    cursor.execute(
        "DELETE FROM conversation_read_state WHERE user_id=? OR peer_id=?",
        (user_id, user_id),
    )
    cursor.execute("DELETE FROM ws_tickets WHERE user_id=?", (user_id,))

    files_removed = 0
    if req.purge_files:
        files_removed = _remove_user_owned_files(cursor, user_id)

    cursor.execute("SELECT COUNT(*) AS c FROM sessions WHERE user_id=?", (user_id,))
    sessions_cleared = int((cursor.fetchone() or {"c": 0})["c"] or 0)
    cursor.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    conn.commit()
    conn.close()

    # Explicitly evict this token in case it survives memory cache windows.
    session_cache_invalidate(session_secret)

    return {
        "status": "ok",
        "mode": req.mode,
        "requires_relogin": True,
        "server_reset": {
            "offline_rows_targeted": len(message_ids),
            "sessions_cleared": sessions_cleared,
            "files_removed": files_removed,
            "note": (
                "friend/group relationships are client-side state; "
                "backend reset clears message/session/unread/cursor/ws state"
            ),
        },
    }

@app.post("/chat/reset")
def reset(request: Request, _: bool = Depends(verify_admin_access)):
    # Check IP allow-list (if configured)
    check_admin_ip(request)
    
    # Destructive endpoint: require an explicit opt-in via env var to allow reset
    allow_reset = os.getenv("ALLOW_UNSAFE_RESET", "false").lower() in ("1", "true", "yes")
    if not allow_reset:
        raise HTTPException(status_code=403, detail="Reset endpoint is disabled. Set ALLOW_UNSAFE_RESET environment variable to enable.")

    if os.path.exists("chat.db"):
        os.remove("chat.db")
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR)
    os.makedirs(UPLOAD_DIR)
    initialize_database()
    return {"status": "ok"}

@app.get("/chat/unread_count")
async def get_unread_count(
    session_secret: str = Header(default=None, alias="session-secret"),
    username_hash: str = Header(default=None, alias="username-hash")
):
    # ⭐ FIXED: Cross-server unread checks need careful handling
    # Problem: Each server has its own user_id for the same user (separate DB)
    # Solution: Accept username_hash to look up the correct user_id on THIS server
    #
    # SECURITY: the raw `user-id` header fallback was removed. /chat/lookup
    # returns user_id to any caller, so accepting it here let anyone read an
    # arbitrary user's unread count with no proof of ownership. The supported
    # paths are an authenticated session-secret, or the cross-server
    # username-hash lookup.

    resolved_user_id = None

    if session_secret:
        # Try to validate session_secret first (most common case: current server)
        valid, result = validate_session_token(session_secret)
        if valid:
            resolved_user_id = result  # result is user_id

    # If session_secret validation failed, try the cross-server username_hash path.
    if resolved_user_id is None:
        if username_hash:
            # ⭐ FIXED: Apply SERVER_IDENTITY_SALT to username_hash to match what's in DB
            # The username_hash in DB is: HMAC(SERVER_IDENTITY_SALT, hashUsername(username))
            identity = get_secure_identity(username_hash)

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE username_hash=?", (identity,))
            user_row = cursor.fetchone()
            conn.close()

            if user_row:
                resolved_user_id = user_row["user_id"]

    # If we still don't have a user_id, reject
    if resolved_user_id is None:
        raise HTTPException(status_code=401, detail="Must provide valid session-secret or username-hash header")

    conn = get_db()
    cursor = conn.cursor()
    count = get_user_unread_count(cursor=cursor, user_id=resolved_user_id)
    conn.close()

    return {"status": "ok", "unread_count": count}


@app.get("/chat/read_cursor")
async def get_read_cursor(
    peer_id: str,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    peer = (peer_id or "").strip()
    if not peer:
        raise HTTPException(status_code=400, detail="peer_id is required")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT last_read_msg_id, last_read_at, COALESCE(last_read_rank, 0) AS last_read_rank
        FROM conversation_read_state
        WHERE user_id=? AND peer_id=?
        """,
        (user_id, peer),
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return {
            "status": "ok",
            "peer_id": peer,
            "last_read_msg_id": "0",
            "last_read_rank": 0,
            "last_read_at": None,
        }

    return {
        "status": "ok",
        "peer_id": peer,
        "last_read_msg_id": str(row["last_read_msg_id"]),
        "last_read_rank": int(row["last_read_rank"] or 0),
        "last_read_at": row["last_read_at"],
    }

@app.get("/chat/get_pending_messages")
async def get_pending_messages(session_secret: str = Header(default=None, alias="session-secret")):
    # ⭐ CHANGED: Now DOES delete messages after returning them
    # This is required since WebSocket no longer auto-drains messages
    # Messages are now kept in offline_messages until explicitly fetched for accurate unread counts
    
    if not session_secret:
        raise HTTPException(status_code=401)
    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all offline messages for this user
    cursor.execute("SELECT id, payload FROM offline_messages WHERE receiver_id=? ORDER BY id ASC", (user_id,))
    rows = cursor.fetchall()
    
    messages = []
    message_ids = []
    now_ms = int(datetime.now().timestamp() * 1000)
    for row in rows:
        message_ids.append(row["id"])
        try:
            payload = json.loads(row["payload"])

            expires_at_ms = payload.get("expires_at_ms")
            if isinstance(expires_at_ms, str):
                try:
                    expires_at_ms = int(expires_at_ms)
                except ValueError:
                    expires_at_ms = None

            if isinstance(expires_at_ms, int) and expires_at_ms <= now_ms:
                continue

            messages.append(payload)
        except:
            pass
    
    # ⭐ DELETE messages after fetching them (client is now handling them)
    if message_ids:
        placeholders = ",".join("?" * len(message_ids))
        cursor.execute(f"DELETE FROM offline_messages WHERE id IN ({placeholders})", message_ids)
        conn.commit()
        print(f"📨 Delivered {len(message_ids)} pending messages to user {user_id}, deleted from queue")
    
    conn.close()
    return {"status": "ok", "messages": messages}


class PendingAckRequest(BaseModel):
    lease_token: str
    message_ids: Optional[list[int]] = None
    ack_results: Optional[list[dict]] = None


@app.get("/chat/get_pending_messages_v2")
async def get_pending_messages_v2(
    request: Request,
    session_secret: str = Header(default=None, alias="session-secret"),
    limit: int = 200,
):
    check_rate_limit(request, 40)
    storm_guard.maybe_heal_global(get_db)
    if not session_secret:
        raise HTTPException(status_code=401)

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    safe_limit = max(1, min(limit, 200))

    allowed, retry_after, reason = storm_guard.allow_heavy_endpoint(
        "pending_fetch",
        user_id,
        session_secret,
    )
    if not allowed:
        return _storm_throttled_response(
            retry_after=retry_after,
            include_pending_shape=True,
            reason=reason,
        )

    storm_guard.maybe_heal_user_state(get_db, user_id)

    is_dup_user, dup_retry = storm_guard.is_pending_fetch_duplicate_for_user(
        user_id,
        PENDING_FETCH_MIN_INTERVAL_SECONDS,
    )
    if is_dup_user:
        return _storm_throttled_response(
            retry_after=dup_retry,
            include_pending_shape=True,
            reason="user_pending_dedupe",
        )

    if _is_pending_fetch_too_soon(session_secret):
        return {
            "status": "ok",
            "messages": [],
            "lease_token": None,
            "lease_message_ids": [],
            "lease_ttl_seconds": PENDING_LEASE_SECONDS,
            "throttled": True,
        }

    conn = get_db()
    cursor = conn.cursor()

    now = datetime.utcnow()
    now_iso = now.isoformat()
    now_ms = int(datetime.now().timestamp() * 1000)
    lease_expires_at = (now + timedelta(seconds=PENDING_LEASE_SECONDS)).isoformat()

    # Cleanup stale leases so locked messages can be retried.
    cursor.execute(
        "DELETE FROM pending_message_leases WHERE lease_expires_at <= ?",
        (now_iso,),
    )

    cursor.execute(
        """
        SELECT om.id, om.payload
        FROM offline_messages om
        LEFT JOIN pending_message_leases pl
          ON pl.message_id = om.id
         AND pl.receiver_id = om.receiver_id
         AND pl.lease_expires_at > ?
        WHERE om.receiver_id = ?
          AND pl.message_id IS NULL
        ORDER BY om.id ASC
        LIMIT ?
        """,
        (now_iso, user_id, safe_limit),
    )
    rows = cursor.fetchall()

    lease_ids = []
    stale_ids = []
    messages = []
    for row in rows:
        msg_id = row["id"]
        try:
            payload = json.loads(row["payload"])

            expires_at_ms = payload.get("expires_at_ms")
            if isinstance(expires_at_ms, str):
                try:
                    expires_at_ms = int(expires_at_ms)
                except ValueError:
                    expires_at_ms = None

            if isinstance(expires_at_ms, int) and expires_at_ms <= now_ms:
                stale_ids.append(msg_id)
                continue

            lease_ids.append(msg_id)
            messages.append(payload)
        except Exception:
            stale_ids.append(msg_id)

    if stale_ids:
        placeholders = ",".join("?" * len(stale_ids))
        cursor.execute(
            f"DELETE FROM offline_messages WHERE id IN ({placeholders})",
            stale_ids,
        )

    lease_token = None
    if lease_ids:
        lease_token = uuid.uuid4().hex
        cursor.executemany(
            """
            INSERT OR REPLACE INTO pending_message_leases
            (message_id, receiver_id, lease_token, lease_expires_at)
            VALUES (?, ?, ?, ?)
            """,
            [(mid, user_id, lease_token, lease_expires_at) for mid in lease_ids],
        )

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "messages": messages,
        "lease_token": lease_token,
        "lease_message_ids": lease_ids,
        "lease_ttl_seconds": PENDING_LEASE_SECONDS,
    }


@app.post("/chat/ack_pending_messages")
async def ack_pending_messages(
    req: PendingAckRequest,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    if not session_secret:
        raise HTTPException(status_code=401)

    valid, result = validate_session_token(session_secret)
    if not valid:
        raise HTTPException(status_code=401, detail=result)

    user_id = result
    lease_token = (req.lease_token or "").strip()
    if not lease_token:
        raise HTTPException(status_code=400, detail="Missing lease_token")

    conn = get_db()
    cursor = conn.cursor()
    now_iso = datetime.utcnow().isoformat()

    cursor.execute(
        """
        SELECT message_id
        FROM pending_message_leases
        WHERE receiver_id = ?
          AND lease_token = ?
          AND lease_expires_at > ?
        """,
        (user_id, lease_token, now_iso),
    )
    rows = cursor.fetchall()
    leased_ids = [r["message_id"] for r in rows]

    requested_ids = set()
    if req.ack_results:
        for item in req.ack_results:
            if not isinstance(item, dict):
                continue
            msg_id = item.get("message_id")
            ok_flag = item.get("ok")
            if isinstance(msg_id, int) and msg_id > 0 and ok_flag is True:
                requested_ids.add(msg_id)

    if req.message_ids:
        for v in req.message_ids:
            if isinstance(v, int) and v > 0:
                requested_ids.add(v)

    if requested_ids:
        ack_ids = [mid for mid in leased_ids if mid in requested_ids]
    else:
        ack_ids = leased_ids

    acked = 0
    if ack_ids:
        placeholders = ",".join("?" * len(ack_ids))
        cursor.execute(
            f"DELETE FROM offline_messages WHERE receiver_id=? AND id IN ({placeholders})",
            [user_id, *ack_ids],
        )
        acked = cursor.rowcount

        cursor.execute(
            f"DELETE FROM pending_message_leases WHERE receiver_id=? AND message_id IN ({placeholders})",
            [user_id, *ack_ids],
        )

    # Cleanup lease rows for this token once acked/handled.
    cursor.execute(
        "DELETE FROM pending_message_leases WHERE receiver_id=? AND lease_token=?",
        (user_id, lease_token),
    )

    conn.commit()
    conn.close()
    return {"status": "ok", "acked": acked}


###### for private server
## NOTE(v4): Historical relay-push examples were removed because self-hosted
## deployments no longer interact with central push infrastructure.
#         return resp.json()
#     except Exception:
#         return {"status": "error", "msg": f"Invalid response: {resp.status_code}"}
