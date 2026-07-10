"""Tests for /chat/unread_count after removing the unauthenticated user-id IDOR.

Verifies:
  - a bare `user-id` header no longer resolves a user (the IDOR is closed);
  - the authenticated `session-secret` path still works;
  - the cross-server `username-hash` path still works (compatibility preserved).

Drives the real route via FastAPI's TestClient against a throwaway SQLite DB.
Requires backend runtime deps + httpx; skipped when unavailable.
"""

import os
import tempfile
import unittest

# Point the app's DB at a throwaway file BEFORE importing (init_db runs at import
# and get_db() reads CHAT_DB_PATH at call time).
_DB_PATH = os.path.join(tempfile.gettempdir(), "miraichat_test_unread.db")
if os.path.exists(_DB_PATH):
    os.remove(_DB_PATH)
os.environ["CHAT_DB_PATH"] = _DB_PATH

try:
    from fastapi.testclient import TestClient

    from chat_backend import legacy_app
    from chat_backend.database import get_db
    from chat_backend.unread_state import upsert_unread_message
except Exception as exc:  # pragma: no cover - exercised only without deps
    legacy_app = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, f"backend deps unavailable: {IMPORT_ERROR}")
class UnreadCountAuthTests(unittest.TestCase):
    USERNAME_HASH = "client-side-username-hash"
    PUBLIC_KEY = "pk"
    SESSION_SECRET = "a" * 64
    DEVICE_ID = "dev-1"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(legacy_app.app)
        # Identity stored in DB is HMAC(SERVER_IDENTITY_SALT, username_hash).
        cls.identity = legacy_app.get_secure_identity(cls.USERNAME_HASH)
        cls.user_id = "11111111-1111-4111-8111-111111111111"

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO users (user_id, username_hash, password, public_key, storage_limit) "
            "VALUES (?, ?, ?, ?, ?)",
            (cls.user_id, cls.identity, "x", cls.PUBLIC_KEY, 100 * 1024 * 1024),
        )
        cur.execute(
            "INSERT OR REPLACE INTO sessions (user_id, device_id, device_name, session_secret, is_main) "
            "VALUES (?, ?, ?, ?, ?)",
            (cls.user_id, cls.DEVICE_ID, "Device", cls.SESSION_SECRET, 1),
        )
        # Seed one unread message from a peer.
        upsert_unread_message(
            cursor=cur,
            user_id=cls.user_id,
            sender_id="peer-9",
            msg={"type": "chat", "msg_id": "m1", "msg_rank": 1000},
        )
        conn.commit()
        conn.close()

    def test_bare_user_id_header_is_rejected(self):
        # The removed IDOR: previously `user-id: <victim>` returned their count.
        resp = self.client.get("/chat/unread_count", headers={"user-id": self.user_id})
        self.assertEqual(resp.status_code, 401)

    def test_no_credentials_rejected(self):
        resp = self.client.get("/chat/unread_count")
        self.assertEqual(resp.status_code, 401)

    def test_session_secret_path_works(self):
        resp = self.client.get(
            "/chat/unread_count", headers={"session-secret": self.SESSION_SECRET}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreaterEqual(body["unread_count"], 1)

    def test_username_hash_cross_server_path_works(self):
        resp = self.client.get(
            "/chat/unread_count", headers={"username-hash": self.USERNAME_HASH}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreaterEqual(body["unread_count"], 1)

    def test_bogus_user_id_with_real_username_hash_still_uses_hash(self):
        # Even if an attacker also sends a user-id, only the username-hash path
        # is consulted; the user-id header is ignored entirely.
        resp = self.client.get(
            "/chat/unread_count",
            headers={"user-id": "attacker-controlled", "username-hash": self.USERNAME_HASH},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
