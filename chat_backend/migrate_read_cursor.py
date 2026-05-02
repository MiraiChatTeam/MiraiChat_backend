import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path("chat.db")


SQLITE_INT64_MIN = -(2 ** 63)
SQLITE_INT64_MAX = (2 ** 63) - 1


def _clamp_int64(value: int) -> int:
    if value > SQLITE_INT64_MAX:
        return SQLITE_INT64_MAX
    if value < SQLITE_INT64_MIN:
        return SQLITE_INT64_MIN
    return value


def _parse_rank(payload: dict) -> int:
    raw_rank = payload.get("msg_rank")
    if raw_rank is not None:
        try:
            return _clamp_int64(int(raw_rank))
        except Exception:
            pass

    ts = payload.get("timestamp")
    if ts is not None:
        try:
            ts_str = str(ts).strip()
            if ts_str.isdigit():
                return _clamp_int64(int(ts_str))
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return _clamp_int64(int(dt.timestamp() * 1000))
        except Exception:
            pass

    return _clamp_int64(int(time.time() * 1000))


def ensure_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_read_state (
            user_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            last_read_msg_id TEXT NOT NULL,
            last_read_rank INTEGER NOT NULL DEFAULT 0,
            last_read_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_by_device TEXT,
            PRIMARY KEY (user_id, peer_id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_read_state_user_peer ON conversation_read_state(user_id, peer_id)"
    )
    conn.commit()


def backfill_from_legacy_delivery_receipts(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT payload FROM offline_messages")
    rows = cur.fetchall()

    best: dict[tuple[str, str], tuple[str, int]] = {}
    for (payload_raw,) in rows:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            continue

        if payload.get("type") != "delivery_receipt":
            continue

        user_id = str(payload.get("to_id") or "").strip()
        peer_id = str(payload.get("from_id") or "").strip()
        msg_id = str(payload.get("msg_id") or "").strip()
        if not user_id or not peer_id or not msg_id:
            continue

        rank = _parse_rank(payload)
        key = (user_id, peer_id)
        prev = best.get(key)
        if prev is None or rank >= prev[1]:
            best[key] = (msg_id, rank)

    upserts = 0
    for (user_id, peer_id), (msg_id, rank) in best.items():
        cur.execute(
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
                updated_by_device = excluded.updated_by_device
            """,
            (user_id, peer_id, msg_id, rank, "migration_delivery_receipt_backfill"),
        )
        upserts += 1

    conn.commit()
    return upserts


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        updated = backfill_from_legacy_delivery_receipts(conn)
        print(f"conversation_read_state migration complete. rows_upserted={updated}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
