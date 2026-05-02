import datetime
from typing import Any, Optional


_SQLITE_INT64_MIN = -(2 ** 63)
_SQLITE_INT64_MAX = (2 ** 63) - 1


def _clamp_int64(value: int) -> int:
    if value > _SQLITE_INT64_MAX:
        return _SQLITE_INT64_MAX
    if value < _SQLITE_INT64_MIN:
        return _SQLITE_INT64_MIN
    return value


def _coerce_int64(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return _clamp_int64(default)
        if isinstance(value, bool):
            return _clamp_int64(int(value))
        if isinstance(value, int):
            return _clamp_int64(value)
        if isinstance(value, float):
            return _clamp_int64(int(value))
        text = str(value).strip()
        if not text:
            return _clamp_int64(default)
        return _clamp_int64(int(text))
    except Exception:
        return _clamp_int64(default)


def _utc_now_ms() -> int:
    return _coerce_int64(int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000), default=0)


def _normalize_message_type(msg: dict[str, Any]) -> str:
    return str(msg.get("type") or "").strip().lower()


def should_track_unread(msg: dict[str, Any]) -> bool:
    raw_inner_msg_type = msg.get("msg_type")
    inner_msg_type: Optional[int] = None
    if isinstance(raw_inner_msg_type, int):
        inner_msg_type = raw_inner_msg_type
    elif isinstance(raw_inner_msg_type, str):
        try:
            inner_msg_type = int(raw_inner_msg_type.strip())
        except Exception:
            inner_msg_type = None

    # Keep unread badge aligned with user-visible chats only.
    # MessageType.system is encoded as msg_type == 3 on the wire.
    if inner_msg_type == 3:
        return False

    # MessageType.reaction is encoded as msg_type == 9.
    # Reactions are metadata events (emoji on an existing message); they do not
    # produce a new visible conversation entry and must not inflate the unread
    # count or the APNs badge value.
    if inner_msg_type == 9:
        return False

    msg_type = _normalize_message_type(msg)
    if msg_type in {
        "delivery_receipt",
        "read_receipt",
        "read_up_to",
        "read_up_to_ack",
        "delivery_ack",
        "delete_message",
    }:
        return False
    return True


def resolve_peer_id(msg: dict[str, Any], sender_id: str) -> Optional[str]:
    group_id = str(msg.get("group_id") or msg.get("groupId") or "").strip()
    if group_id:
        return group_id

    sender = str(sender_id or "").strip()
    if sender:
        return sender

    return None


def resolve_msg_rank(msg: dict[str, Any]) -> int:
    raw_rank = msg.get("msg_rank")
    parsed_rank = _coerce_int64(raw_rank, default=0)
    if parsed_rank > 0:
        return parsed_rank

    raw_ts = msg.get("timestamp")
    if raw_ts is not None:
        ts_text = str(raw_ts).strip()
        if ts_text:
            if ts_text.isdigit():
                return _coerce_int64(int(ts_text), default=_utc_now_ms())
            try:
                dt = datetime.datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
                return _coerce_int64(int(dt.timestamp() * 1000), default=_utc_now_ms())
            except Exception:
                pass

    return _utc_now_ms()


def upsert_unread_message(
    *,
    cursor,
    user_id: str,
    sender_id: str,
    msg: dict[str, Any],
) -> bool:
    if not should_track_unread(msg):
        return False

    receiver = str(user_id or "").strip()
    if not receiver:
        return False

    msg_id = str(msg.get("msg_id") or "").strip()
    if not msg_id:
        return False

    peer_id = resolve_peer_id(msg, sender_id)
    if not peer_id:
        return False

    rank = resolve_msg_rank(msg)

    cursor.execute(
        """
        INSERT OR IGNORE INTO unread_messages (user_id, peer_id, msg_id, msg_rank)
        VALUES (?, ?, ?, ?)
        """,
        (receiver, peer_id, msg_id, rank),
    )
    return True


def mark_read_up_to(*, cursor, user_id: str, peer_id: str, msg_rank: int) -> int:
    receiver = str(user_id or "").strip()
    peer = str(peer_id or "").strip()
    if not receiver or not peer:
        return 0

    safe_rank = _coerce_int64(msg_rank, default=0)
    if safe_rank <= 0:
        return 0

    cursor.execute(
        """
        DELETE FROM unread_messages
        WHERE user_id=?
          AND peer_id=?
          AND msg_rank <= ?
        """,
        (receiver, peer, safe_rank),
    )
    return int(cursor.rowcount or 0)


def get_user_unread_count(*, cursor, user_id: str) -> int:
    receiver = str(user_id or "").strip()
    if not receiver:
        return 0

    cursor.execute(
        "SELECT COUNT(*) AS c FROM unread_messages WHERE user_id=?",
        (receiver,),
    )
    row = cursor.fetchone()
    if not row:
        return 0

    if isinstance(row, dict):
        return int(row.get("c") or 0)

    try:
        return int(row[0] or 0)
    except Exception:
        return 0
