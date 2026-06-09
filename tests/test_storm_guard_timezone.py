"""Tests for storm_guard timezone-correct cleanup of ws_tickets vs leases.

ws_tickets.expires_at is written in LOCAL time (issue_ws_ticket) and must be
purged on the same local-time basis; pending_message_leases.lease_expires_at is
written/read in UTC and must keep its UTC basis. A freshly issued local-time
ticket must NOT be purged in timezones behind UTC, while genuinely expired rows
in either table must be removed.

storm_guard imports with the standard library only, so these run unconditionally.
"""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from chat_backend import storm_guard


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE ws_tickets (ticket_hash TEXT PRIMARY KEY, user_id TEXT, "
        "session_secret TEXT, expires_at DATETIME, used INTEGER DEFAULT 0)"
    )
    cur.execute(
        "CREATE TABLE pending_message_leases (message_id INTEGER PRIMARY KEY, "
        "receiver_id TEXT, lease_token TEXT, lease_expires_at DATETIME)"
    )
    conn.commit()
    return conn


class _NonClosingConn:
    """Proxy that forwards everything to the real connection but makes close()
    a no-op, so maybe_heal_global's `finally: conn.close()` doesn't tear down
    our in-memory test DB."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return None


class _DbProvider:
    """Callable returning a non-closing proxy over the same connection."""

    def __init__(self, conn):
        self._conn = conn

    def __call__(self):
        return _NonClosingConn(self._conn)


class MaybeHealGlobalTimezoneTests(unittest.TestCase):
    def setUp(self):
        # Defeat the heal cooldown so each call actually runs.
        storm_guard._LAST_GLOBAL_HEAL_AT = 0.0

    def _heal(self, conn):
        storm_guard._LAST_GLOBAL_HEAL_AT = 0.0
        storm_guard.maybe_heal_global(_DbProvider(conn))

    def test_fresh_local_time_ticket_is_not_purged(self):
        conn = _make_db()
        cur = conn.cursor()
        # Mirror issue_ws_ticket: expires 60s from now in LOCAL time.
        fresh_expiry = (datetime.now() + timedelta(seconds=60)).isoformat()
        cur.execute(
            "INSERT INTO ws_tickets (ticket_hash, user_id, session_secret, expires_at, used) "
            "VALUES ('fresh', 'u1', 's1', ?, 0)",
            (fresh_expiry,),
        )
        conn.commit()

        self._heal(conn)

        remaining = cur.execute(
            "SELECT COUNT(*) AS c FROM ws_tickets WHERE ticket_hash='fresh'"
        ).fetchone()["c"]
        conn.close()
        # The fix: a just-issued ticket survives even when local time < UTC.
        self.assertEqual(remaining, 1)

    def test_expired_ticket_and_used_ticket_are_purged(self):
        conn = _make_db()
        cur = conn.cursor()
        past_local = (datetime.now() - timedelta(seconds=120)).isoformat()
        future_local = (datetime.now() + timedelta(seconds=120)).isoformat()
        cur.executemany(
            "INSERT INTO ws_tickets (ticket_hash, user_id, session_secret, expires_at, used) "
            "VALUES (?, 'u', 's', ?, ?)",
            [
                ("expired", past_local, 0),   # past expiry -> purged
                ("used", future_local, 1),    # used=1 -> purged
                ("live", future_local, 0),    # valid -> kept
            ],
        )
        conn.commit()

        self._heal(conn)

        rows = {r["ticket_hash"] for r in cur.execute("SELECT ticket_hash FROM ws_tickets")}
        conn.close()
        self.assertEqual(rows, {"live"})

    def test_leases_purged_on_utc_basis(self):
        conn = _make_db()
        cur = conn.cursor()
        # Leases are written/read in UTC.
        expired_utc = (datetime.utcnow() - timedelta(seconds=30)).isoformat()
        live_utc = (datetime.utcnow() + timedelta(seconds=120)).isoformat()
        cur.executemany(
            "INSERT INTO pending_message_leases (message_id, receiver_id, lease_token, lease_expires_at) "
            "VALUES (?, 'u', 't', ?)",
            [(1, expired_utc), (2, live_utc)],
        )
        conn.commit()

        self._heal(conn)

        remaining = {r["message_id"] for r in cur.execute("SELECT message_id FROM pending_message_leases")}
        conn.close()
        # Expired UTC lease purged; live one kept.
        self.assertEqual(remaining, {2})

    def test_simulated_negative_utc_offset_keeps_fresh_ticket(self):
        """Direct regression for the original bug.

        Old code compared a local-time expires_at against datetime.utcnow().
        For a host behind UTC, utcnow() > now(), so a 60s ticket was already
        '<= utcnow()' at issue time. Assert the new local-time comparison keeps it.
        """
        conn = _make_db()
        cur = conn.cursor()
        local_now = datetime.now()
        fresh_expiry = (local_now + timedelta(seconds=60)).isoformat()
        cur.execute(
            "INSERT INTO ws_tickets (ticket_hash, user_id, session_secret, expires_at, used) "
            "VALUES ('fresh', 'u', 's', ?, 0)",
            (fresh_expiry,),
        )
        conn.commit()

        # Sanity: in a UTC-behind timezone the OLD logic would have deleted it.
        utc_now_iso = datetime.utcnow().isoformat()
        would_delete_old = cur.execute(
            "SELECT COUNT(*) AS c FROM ws_tickets WHERE expires_at <= ?",
            (utc_now_iso,),
        ).fetchone()["c"]

        self._heal(conn)
        survived = cur.execute(
            "SELECT COUNT(*) AS c FROM ws_tickets WHERE ticket_hash='fresh'"
        ).fetchone()["c"]
        conn.close()

        # Whatever the test host's timezone, the new heal keeps the fresh ticket.
        self.assertEqual(survived, 1)
        # If the host happens to be behind UTC, document that the old logic
        # would have wrongly deleted it.
        if datetime.now() < datetime.utcnow():
            self.assertEqual(would_delete_old, 1)


if __name__ == "__main__":
    unittest.main()
