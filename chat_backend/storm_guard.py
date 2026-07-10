import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Callable, Optional


_BUCKET_LOCK = threading.Lock()
_BUCKETS: dict[tuple[str, str, str], tuple[float, float]] = {}

# user_id -> safe_mode_until_epoch
_SAFE_MODE_UNTIL: dict[str, float] = {}
# user_id -> rolling event timestamps
_USER_STORM_EVENTS: dict[str, list[float]] = defaultdict(list)

# user dedupe caches (persistent across session rotation for process lifetime)
_USER_PENDING_FETCH_LAST_AT: dict[str, float] = {}
# session -> (ticket, expires_epoch, user_id)
_WS_TICKET_CACHE: dict[str, tuple[str, float, str]] = {}

# (user_id, domain) -> (response_dict, expires_epoch)
_PIN_LIST_CACHE: dict[tuple[str, str], tuple[dict, float]] = {}

# state-healing cadence
_LAST_GLOBAL_HEAL_AT = 0.0
_LAST_USER_HEAL_AT: dict[str, float] = {}

_SAFE_MODE_SECONDS = int(os.getenv("STORM_SAFE_MODE_SECONDS", "120"))
_STORM_EVENT_WINDOW_SECONDS = int(os.getenv("STORM_EVENT_WINDOW_SECONDS", "120"))
_STORM_EVENT_THRESHOLD = int(os.getenv("STORM_EVENT_THRESHOLD", "24"))
_GLOBAL_HEAL_COOLDOWN_SECONDS = int(os.getenv("STORM_GLOBAL_HEAL_COOLDOWN_SECONDS", "15"))
_USER_HEAL_COOLDOWN_SECONDS = int(os.getenv("STORM_USER_HEAL_COOLDOWN_SECONDS", "45"))

# endpoint -> (user_budget, session_budget)
# budget tuple = (capacity, window_seconds)
_ENDPOINT_BUDGETS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "login": ((10, 60), (6, 60)),
    "ws_ticket": ((30, 60), (16, 60)),
    "pin_list": ((20, 60), (10, 60)),
    "pending_fetch": ((35, 60), (18, 60)),
}


def _norm_key(value: Optional[str], fallback: str) -> str:
    txt = (value or "").strip()
    return txt if txt else fallback


def _token_bucket_allow(scope: str, endpoint: str, key: str, cap: int, window_seconds: int) -> tuple[bool, int]:
    now = time.time()
    if cap <= 0 or window_seconds <= 0:
        return True, 0

    refill_rate = float(cap) / float(window_seconds)
    bucket_key = (scope, endpoint, key)
    with _BUCKET_LOCK:
        tokens, last_ts = _BUCKETS.get(bucket_key, (float(cap), now))
        elapsed = max(0.0, now - last_ts)
        tokens = min(float(cap), tokens + elapsed * refill_rate)

        if tokens >= 1.0:
            _BUCKETS[bucket_key] = (tokens - 1.0, now)
            return True, 0

        needed = 1.0 - tokens
        retry_after = int(max(1.0, needed / refill_rate))
        _BUCKETS[bucket_key] = (tokens, now)
        return False, retry_after


def _record_storm_event(user_id: str) -> None:
    if not user_id:
        return
    now = time.time()
    events = _USER_STORM_EVENTS[user_id]
    cutoff = now - _STORM_EVENT_WINDOW_SECONDS
    events[:] = [ts for ts in events if ts >= cutoff]
    events.append(now)
    if len(events) >= _STORM_EVENT_THRESHOLD:
        _SAFE_MODE_UNTIL[user_id] = now + _SAFE_MODE_SECONDS


def get_safe_mode_retry_after(user_id: str) -> int:
    if not user_id:
        return 0
    now = time.time()
    until = _SAFE_MODE_UNTIL.get(user_id)
    if until is None:
        return 0
    if now >= until:
        _SAFE_MODE_UNTIL.pop(user_id, None)
        _USER_STORM_EVENTS.pop(user_id, None)
        return 0
    return int(max(1.0, until - now))


def allow_heavy_endpoint(endpoint: str, user_id: str, session_key: str) -> tuple[bool, int, str]:
    retry_safe_mode = get_safe_mode_retry_after(user_id)
    if retry_safe_mode > 0:
        return False, retry_safe_mode, "safe_mode"

    user_budget, session_budget = _ENDPOINT_BUDGETS.get(endpoint, ((20, 60), (10, 60)))

    ok_user, retry_user = _token_bucket_allow(
        "user",
        endpoint,
        _norm_key(user_id, "anonymous_user"),
        user_budget[0],
        user_budget[1],
    )
    if not ok_user:
        _record_storm_event(user_id)
        return False, retry_user, "user_budget"

    ok_session, retry_session = _token_bucket_allow(
        "session",
        endpoint,
        _norm_key(session_key, "anonymous_session"),
        session_budget[0],
        session_budget[1],
    )
    if not ok_session:
        _record_storm_event(user_id)
        return False, retry_session, "session_budget"

    return True, 0, "ok"


def note_endpoint_storm(user_id: str) -> None:
    _record_storm_event(user_id)


def is_pending_fetch_duplicate_for_user(user_id: str, min_interval_seconds: float) -> tuple[bool, int]:
    now = time.time()
    key = _norm_key(user_id, "anonymous_user")
    last = _USER_PENDING_FETCH_LAST_AT.get(key)
    _USER_PENDING_FETCH_LAST_AT[key] = now
    if last is None:
        return False, 0
    delta = now - last
    if delta >= min_interval_seconds:
        return False, 0
    return True, int(max(1.0, min_interval_seconds - delta))


def get_cached_ws_ticket(session_secret: str) -> Optional[tuple[str, str]]:
    key = _norm_key(session_secret, "")
    if not key:
        return None
    entry = _WS_TICKET_CACHE.get(key)
    if not entry:
        return None
    ticket, expires_epoch, _ = entry
    if time.time() >= expires_epoch:
        _WS_TICKET_CACHE.pop(key, None)
        return None
    expires_at_iso = datetime.fromtimestamp(expires_epoch).isoformat()
    return ticket, expires_at_iso


def cache_ws_ticket(session_secret: str, user_id: str, ticket: str, expires_epoch: float) -> None:
    key = _norm_key(session_secret, "")
    if not key:
        return
    _WS_TICKET_CACHE[key] = (ticket, max(expires_epoch, time.time() + 1.0), _norm_key(user_id, "unknown_user"))


def cache_pin_list_response(user_id: str, domain: str, response: dict, ttl_seconds: int = 180) -> None:
    if not user_id or not domain:
        return
    _PIN_LIST_CACHE[(user_id.strip(), domain.strip().lower())] = (dict(response), time.time() + max(10, ttl_seconds))


def get_cached_pin_list_response(user_id: str, domain: str) -> Optional[dict]:
    if not user_id or not domain:
        return None
    key = (user_id.strip(), domain.strip().lower())
    entry = _PIN_LIST_CACHE.get(key)
    if not entry:
        return None
    payload, expires_at = entry
    if time.time() >= expires_at:
        _PIN_LIST_CACHE.pop(key, None)
        return None
    return dict(payload)


def maybe_heal_global(get_db: Callable[[], object]) -> None:
    global _LAST_GLOBAL_HEAL_AT
    now = time.time()
    if (now - _LAST_GLOBAL_HEAL_AT) < _GLOBAL_HEAL_COOLDOWN_SECONDS:
        return

    _LAST_GLOBAL_HEAL_AT = now
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        # ws_tickets.expires_at is written in LOCAL time (issue_ws_ticket uses
        # datetime.now()) and read authoritatively in local time (the /ws auth
        # path and cleanup_system). pending_message_leases.lease_expires_at is
        # written and read in UTC. Using one basis for both would purge fresh
        # tickets in any timezone behind UTC — keep each cleanup on its own basis.
        ws_now_iso = datetime.now().isoformat()
        utc_now_iso = datetime.utcnow().isoformat()
        cursor.execute("DELETE FROM ws_tickets WHERE used = 1 OR expires_at <= ?", (ws_now_iso,))
        cursor.execute("DELETE FROM pending_message_leases WHERE lease_expires_at <= ?", (utc_now_iso,))
        conn.commit()
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def maybe_heal_user_state(
    get_db: Callable[[], object],
    user_id: str,
    *,
    device_id: Optional[str] = None,
    keep_session_secret: Optional[str] = None,
) -> None:
    if not user_id:
        return

    now = time.time()
    key = user_id.strip()
    last = _LAST_USER_HEAL_AT.get(key)
    if last is not None and (now - last) < _USER_HEAL_COOLDOWN_SECONDS:
        return
    _LAST_USER_HEAL_AT[key] = now

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        if device_id:
            cursor.execute(
                "SELECT session_secret FROM sessions WHERE user_id=? AND device_id=? ORDER BY created_at DESC",
                (key, device_id),
            )
            rows = cursor.fetchall()
            stale = []
            for row in rows[1:]:
                secret = row["session_secret"]
                if secret:
                    stale.append(secret)
            if stale:
                placeholders = ",".join("?" * len(stale))
                cursor.execute(
                    f"DELETE FROM sessions WHERE user_id=? AND session_secret IN ({placeholders})",
                    [key, *stale],
                )

        cursor.execute(
            "DELETE FROM ws_tickets WHERE user_id=? AND (used = 1 OR expires_at <= ?)",
            (key, datetime.now().isoformat()),
        )

        conn.commit()
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
