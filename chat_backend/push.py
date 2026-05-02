import hashlib
import os
import time
from typing import Any, Optional

import requests

from chat_backend.database import get_db
from chat_backend.settings import (
    CENTRAL_SERVER_TOKEN,
    HUB_LICENSE_KEY,
    PUBLIC_HUB_URL,
)
from chat_backend.unread_state import get_user_unread_count


def _is_scheme_b_safe_component(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in "._:-" for ch in value)


def _safe_scheme_b_component(value: str, *, prefix: str, max_len: int = 128) -> str:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= max_len and _is_scheme_b_safe_component(normalized):
        return normalized
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _delegated_server_id() -> str:
    configured = os.getenv("PUSH_DELEGATED_SERVER_ID", "").strip()
    if configured:
        return _safe_scheme_b_component(configured, prefix="server")
    seed = (HUB_LICENSE_KEY or PUBLIC_HUB_URL or "custom-backend").strip()
    digest = hashlib.sha256(seed.encode()).hexdigest()[:32]
    return f"custom-{digest}"


def _iter_target_devices(cursor, user_id: str, excluded_sessions: set[str]) -> list[dict[str, str]]:
    return []


def _central_delegate_push_url() -> str:
    base = (PUBLIC_HUB_URL or "").strip() or "https://public.miraichat.net"
    if "://" not in base:
        base = f"https://{base}"
    return f"{base.rstrip('/')}/central/push/delegate_wake"


_ANDROID_RENDERABLE_TYPES = {
    "new_message",
    "sealed_sender",
    "chat_message",
    "friend_request",
    "group_invitation",
}


def _delegated_push_type(data_payload: Optional[dict[str, Any]]) -> str:
    raw_type = str((data_payload or {}).get("type") or "").strip().lower()
    if raw_type in _ANDROID_RENDERABLE_TYPES:
        return raw_type
    if raw_type in {"friend_response", "reaction", "message", "call", "system", ""}:
        return "chat_message"
    return raw_type


def _delegated_push_payload(data_payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    source = data_payload or {}
    payload: dict[str, Any] = {
        "type": _delegated_push_type(source),
    }
    for key in ("msg_id", "conversation_id", "enc_v"):
        value = str(source.get(key) or "").strip()
        if value:
            payload[key] = value
    if "preview_envelope_v1" in source:
        preview_envelope = source.get("preview_envelope_v1")
        if preview_envelope not in (None, ""):
            payload["preview_envelope_v1"] = preview_envelope
    for key in ("reaction_event_key", "sender_ref"):
        value = str(source.get(key) or "").strip()
        if value:
            payload[key] = value[:128]
    return payload


def should_send_push_for_message(msg: dict) -> bool:
    if not isinstance(msg, dict):
        return False

    raw_no_push = msg.get("no_push")
    no_push = raw_no_push is True or str(raw_no_push).strip().lower() == "true"
    if no_push:
        return False

    msg_type = str(msg.get("type") or "").strip().lower()
    push_type = str(msg.get("push_type") or msg_type or "").strip().lower()

    if msg_type in {
        "delivery_receipt",
        "read_receipt",
        "read_up_to",
        "read_up_to_ack",
        "delivery_ack",
        "delete_message",
    }:
        return False

    return push_type in {
        "chat_message",
        "new_message",
        "friend_request",
        "friend_response",
        "group_invitation",
        "reaction",
        "sealed_sender",
    }


def send_fcm_push(
    user_id: str,
    title: str,
    body: str,
    data_payload: Optional[dict[str, Any]] = None,
    exclude_session_secrets: Optional[list[str]] = None,
) -> None:
    if not push_delivery_status()["enabled"]:
        print("[WARN] Delegated push disabled: missing HUB_LICENSE_KEY/CENTRAL_SERVER_TOKEN")
        return

    target_user_id = str(user_id or "").strip()
    if not target_user_id:
        return

    server_id = _delegated_server_id()
    ts = str(int(time.time()))
    n = os.urandom(16).hex()

    delegate_body = {
        "licenseKey": HUB_LICENSE_KEY,
        "serverId": server_id,
        "userId": target_user_id,
        "payload": _delegated_push_payload(data_payload),
        "timestamp": ts,
        "nonce": n,
        "signature": "",
    }

    try:
        response = requests.post(
            _central_delegate_push_url(),
            json=delegate_body,
            headers={"Authorization": f"Bearer {CENTRAL_SERVER_TOKEN}"},
            timeout=5,
        )
    except Exception as exc:
        print(f"[WARN] delegate_wake request error: {exc}")
        return

    if response.status_code >= 400:
        print(f"[WARN] delegated wake failed (central error, status={response.status_code})")
        return

    try:
        result = response.json()
        if isinstance(result, dict) and result.get("status") == "ok":
            print(f"[INFO] delegated push: accepted for user {target_user_id}")
        elif isinstance(result, dict):
            print("[WARN] delegated push failed (central non-ok response)")
    except Exception:
        print("[WARN] delegated push failed (central non-JSON response)")


def push_delivery_status() -> dict[str, str | bool]:
    enabled = bool(HUB_LICENSE_KEY and CENTRAL_SERVER_TOKEN)
    return {
        "enabled": enabled,
        "reason": "Token-based delegated push jobs dispatch through the central hub.",
        "delegate_url": _central_delegate_push_url(),
    }
