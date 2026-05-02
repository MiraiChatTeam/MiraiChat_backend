import sqlite3
from pathlib import Path

DB_PATH = Path("chat.db")
SQLITE_INT64_MIN = -(2 ** 63)
SQLITE_INT64_MAX = (2 ** 63) - 1


def clamp_int64(value: int) -> int:
    if value > SQLITE_INT64_MAX:
        return SQLITE_INT64_MAX
    if value < SQLITE_INT64_MIN:
        return SQLITE_INT64_MIN
    return value


def sanitize() -> tuple[int, int]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT user_id, peer_id, last_read_msg_id, COALESCE(last_read_rank, 0) AS last_read_rank
            FROM conversation_read_state
            """
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return 0, 0

    scanned = 0
    updated = 0

    for row in rows:
        scanned += 1
        raw = row["last_read_rank"]
        try:
            rank = int(raw)
        except Exception:
            rank = 0
        clamped = clamp_int64(rank)
        if clamped != rank:
            cur.execute(
                """
                UPDATE conversation_read_state
                SET last_read_rank = ?, last_read_at = CURRENT_TIMESTAMP,
                    updated_by_device = COALESCE(updated_by_device, 'sanitize_read_cursor')
                WHERE user_id = ? AND peer_id = ?
                """,
                (clamped, row["user_id"], row["peer_id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    return scanned, updated


def main() -> None:
    scanned, updated = sanitize()
    print(f"read_cursor sanitize complete. scanned={scanned} updated={updated}")


if __name__ == "__main__":
    main()
