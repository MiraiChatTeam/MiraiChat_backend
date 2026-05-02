import hashlib
import hmac
import secrets
import time
from typing import Any, Optional

import requests

from chat_backend.database import get_db
from chat_backend.settings import (
    HUB_LICENSE_KEY,
    HUB_LICENSE_SECRET,
    HUB_SESSION_TOKEN,
    PUBLIC_HUB_URL,
)
from chat_backend.unread_state import get_user_unread_count


def _iter_target_tokens(cursor, user_id: str, excluded_sessions: set[str]) -> list[str]:
    cursor.execute(
        "SELECT session_secret, fcm_token FROM sessions "
        "WHERE user_id = ? AND fcm_token IS NOT NULL AND fcm_token != '' "
        "ORDER BY datetime(COALESCE(token_updated_at, created_at)) DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        session_secret = str(row["session_secret"] or "").strip()
        if session_secret and session_secret in excluded_sessions:
            continue
        token = str(row["fcm_token"] or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)

    if tokens:
        return tokens

    cursor.execute("SELECT fcm_token FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    fallback = str(row["fcm_token"] or "").strip() if row else ""
    return [fallback] if fallback else []


def _central_relay_url() -> str:
    base = (PUBLIC_HUB_URL or "").strip() or "https://public.miraichat.net"
    if "://" not in base:
        base = f"https://{base}"
    return f"{base.rstrip('/')}/chat/relay_push"


def _central_proxy_push_url() -> str:
    base = (PUBLIC_HUB_URL or "").strip() or "https://public.miraichat.net"
    if "://" not in base:
        base = f"https://{base}"
    return f"{base.rstrip('/')}/chat/proxy_push"


def _central_register_token_url() -> str:
    base = (PUBLIC_HUB_URL or "").strip() or "https://public.miraichat.net"
    if "://" not in base:
        base = f"https://{base}"
    return f"{base.rstrip('/')}/chat/register_custom_push_token"


def register_push_token_with_central(
    user_id: str,
    fcm_token: str,
    platform: str,
) -> str:
    """Register an FCM token in central's per-custom-backend registry.

    Returns one of: "registered", "existing", "cap_exceeded", "error", "disabled".
    """
    if not HUB_LICENSE_KEY or not HUB_LICENSE_SECRET or not HUB_SESSION_TOKEN:
        return "disabled"

    token = str(fcm_token or "").strip()
    uid = str(user_id or "").strip()
    if not token or not uid:
        return "error"

    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sign_message = f"{uid}{token}{timestamp}{nonce}"
    signature = hmac.new(
        HUB_LICENSE_SECRET.encode(),
        sign_message.encode(),
        hashlib.sha256,
    ).hexdigest()

    payload = {
        "license_key": HUB_LICENSE_KEY,
        "user_id": uid,
        "fcm_token": token,
        "platform": str(platform or "").strip().lower(),
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
    }

    try:
        response = requests.post(
            _central_register_token_url(),
            json=payload,
            headers={"Authorization": f"Bearer {HUB_SESSION_TOKEN}"},
            timeout=5,
        )
        if response.status_code >= 400:
            print(f"[WARN] register_custom_push_token HTTP {response.status_code}: {response.text[:200]}")
            return "error"
        result = response.json()
        outcome = str(result.get("result") or "error")
        print(
            f"[INFO] register_custom_push_token: {outcome} "
            f"license={HUB_LICENSE_KEY[:10]}... user={uid} token=*{token[-10:]}"
        )
        return outcome
    except Exception as exc:
        print(f"[WARN] register_custom_push_token request error: {exc}")
        return "error"


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
    if not HUB_LICENSE_KEY or not HUB_LICENSE_SECRET or not HUB_SESSION_TOKEN:
        print("[WARN] Proxy push disabled: missing HUB_LICENSE_KEY/HUB_LICENSE_SECRET/HUB_SESSION_TOKEN")
        return

    normalized_data = {
        str(key): str(value)
        for key, value in (data_payload or {}).items()
        if value is not None
    }
    normalized_data.setdefault("enc_v", "1")

    conn = get_db()
    cursor = conn.cursor()
    unread_count = get_user_unread_count(cursor=cursor, user_id=str(user_id))
    conn.close()

    # --- Primary path: proxy_push (central holds token registry per backend) ---
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sign_message = f"{user_id}{timestamp}{nonce}"
    signature = hmac.new(
        HUB_LICENSE_SECRET.encode(),
        sign_message.encode(),
        hashlib.sha256,
    ).hexdigest()

    proxy_payload = {
        "license_key": HUB_LICENSE_KEY,
        "user_id": str(user_id),
        "title": title,
        "body": body,
        "data": normalized_data,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "unread_count": unread_count,
    }

    try:
        response = requests.post(
            _central_proxy_push_url(),
            json=proxy_payload,
            headers={"Authorization": f"Bearer {HUB_SESSION_TOKEN}"},
            timeout=5,
        )
        if response.status_code < 400:
            try:
                result = response.json()
            except Exception:
                result = {}
            if isinstance(result, dict) and result.get("status") == "ok":
                dispatched = result.get("dispatched", 0)
                reason = result.get("reason", "")
                if reason == "no_device_registered":
                    print(f"[INFO] proxy_push: no tokens registered for user {user_id}; falling back")
                else:
                    print(f"[INFO] proxy_push: dispatched={dispatched} for user {user_id}")
                    return
        else:
            print(f"[WARN] proxy_push HTTP {response.status_code}: {response.text[:300]}")
    except Exception as exc:
        print(f"[WARN] proxy_push request error: {exc}")

    # --- Fallback path: relay per local token (backward compat) ---
    excluded_sessions = {
        str(session_secret).strip()
        for session_secret in (exclude_session_secrets or [])
        if str(session_secret).strip()
    }

    conn = get_db()
    cursor = conn.cursor()
    tokens = _iter_target_tokens(cursor, str(user_id), excluded_sessions)
    conn.close()

    if not tokens:
        # No tokens registered in central per-backend registry or locally.
        # /central/push/trigger requires an official central token (central_official_tokens)
        # which custom backends cannot obtain. Nothing to dispatch.
        print(f"[INFO] send_fcm_push: no registered tokens for user {user_id}; no dispatch")
        return

    relay_url = _central_relay_url()
    print(f"[INFO] relay fallback for user {user_id}; license={HUB_LICENSE_KEY[:10]}...")

    for fcm_token in tokens:
        ts = str(int(time.time()))
        n = secrets.token_hex(16)
        sig = hmac.new(
            HUB_LICENSE_SECRET.encode(),
            f"{fcm_token}{ts}{n}".encode(),
            hashlib.sha256,
        ).hexdigest()

        relay_body = {
            "license_key": HUB_LICENSE_KEY,
            "fcm_token": fcm_token,
            "title": title,
            "body": body,
            "data": normalized_data,
            "timestamp": ts,
            "nonce": n,
            "signature": sig,
            "unread_count": unread_count,
        }

        try:
            response = requests.post(
                relay_url,
                json=relay_body,
                headers={"Authorization": f"Bearer {HUB_SESSION_TOKEN}"},
                timeout=5,
            )
        except Exception as exc:
            print(f"[WARN] relay_push request error: {exc}")
            continue

        if response.status_code >= 400:
            print(f"[WARN] relay_push failed ({response.status_code}): {response.text[:300]}")
            continue

        try:
            result = response.json()
            if isinstance(result, dict) and result.get("status") != "ok":
                print(f"[WARN] relay_push returned non-ok: {result}")
        except Exception:
            print(f"[WARN] relay_push returned non-JSON response: {response.text[:200]}")


def push_delivery_status() -> dict[str, str | bool]:
    enabled = bool(HUB_LICENSE_KEY and HUB_LICENSE_SECRET and HUB_SESSION_TOKEN)
    return {
        "enabled": enabled,
        "reason": "Proxy push dispatches to central hub relay endpoint.",
        "relay_url": _central_relay_url(),
    }