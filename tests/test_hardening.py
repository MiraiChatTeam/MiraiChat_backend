"""Tests for the security hardening pass.

Covers two pure/near-pure units:
  - _escape_like(): LIKE wildcard escaping, plus an end-to-end SQLite check that
    a client-controlled '%' no longer over-matches other rows.
  - the NONCE_CACHE size cap in verify_ephemeral_token(): the cache stays bounded
    by MAX_NONCE_CACHE_SIZE even under a burst of distinct valid nonces.

Importing the backend modules requires the runtime deps (fastapi, cryptography,
bcrypt, ...) and initializes a DB, so CHAT_DB_PATH is redirected to a temp file
and the tests skip when deps are unavailable.
"""

import hashlib
import hmac
import os
import sqlite3
import tempfile
import time
import unittest

os.environ.setdefault(
    "CHAT_DB_PATH", os.path.join(tempfile.gettempdir(), "miraichat_test_hardening.db")
)

try:
    from chat_backend import legacy_app
    from chat_backend import auth as auth_module
except Exception as exc:  # pragma: no cover - exercised only without deps
    legacy_app = None
    auth_module = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, f"backend deps unavailable: {IMPORT_ERROR}")
class EscapeLikeTests(unittest.TestCase):
    def test_escapes_each_wildcard(self):
        self.assertEqual(legacy_app._escape_like("%"), "\\%")
        self.assertEqual(legacy_app._escape_like("_"), "\\_")
        self.assertEqual(legacy_app._escape_like("\\"), "\\\\")

    def test_backslash_escaped_before_wildcards(self):
        # A literal backslash must be doubled so it cannot escape a following
        # wildcard the attacker also supplied.
        self.assertEqual(legacy_app._escape_like("\\%"), "\\\\\\%")

    def test_plain_value_unchanged(self):
        self.assertEqual(legacy_app._escape_like("abc-123"), "abc-123")

    def test_end_to_end_like_escape_blocks_overmatch(self):
        """A user_id of '%' must not match other users' offline rows."""
        conn = sqlite3.connect(":memory:")
        c = conn.cursor()
        c.execute("CREATE TABLE offline_messages(id INTEGER PRIMARY KEY, payload TEXT)")
        c.execute(
            "INSERT INTO offline_messages(payload) VALUES (?)",
            ('{"from_id":"victim123","to_id":"bob"}',),
        )
        c.execute(
            "INSERT INTO offline_messages(payload) VALUES (?)",
            ('{"from_id":"%","to_id":"bob"}',),
        )

        attacker = "%"
        escaped = legacy_app._escape_like(attacker)
        c.execute(
            "SELECT id FROM offline_messages WHERE payload LIKE ? ESCAPE '\\'",
            (f'%"from_id":"{escaped}"%',),
        )
        matched = sorted(r[0] for r in c.fetchall())
        conn.close()
        # Only the row whose from_id is literally '%' (id=2) must match.
        self.assertEqual(matched, [2])


@unittest.skipIf(IMPORT_ERROR is not None, f"backend deps unavailable: {IMPORT_ERROR}")
class NonceCacheCapTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(auth_module.NONCE_CACHE)
        auth_module.NONCE_CACHE.clear()

    def tearDown(self):
        auth_module.NONCE_CACHE.clear()
        auth_module.NONCE_CACHE.update(self._saved)

    def _valid_token(self, secret, nonce, ts, from_id):
        message = f"{nonce}:{ts}:{from_id}"
        return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    def test_cache_stays_bounded_under_burst(self):
        secret = "unit-test-session-secret"
        from_id = "user-burst"
        ts = str(int(time.time()))
        cap = auth_module.MAX_NONCE_CACHE_SIZE

        accepted = 0
        for i in range(cap + 250):
            nonce = f"nonce-{i}"
            token = self._valid_token(secret, nonce, ts, from_id)
            ok = auth_module.verify_ephemeral_token(
                from_id, nonce, ts, token, session_secret=secret
            )
            self.assertTrue(ok, f"valid token #{i} should verify")
            accepted += 1
            # Invariant: never exceeds the configured cap after eviction.
            self.assertLessEqual(len(auth_module.NONCE_CACHE), cap)

        self.assertEqual(accepted, cap + 250)
        self.assertLessEqual(len(auth_module.NONCE_CACHE), cap)

    def test_replayed_nonce_is_rejected(self):
        secret = "unit-test-session-secret"
        from_id = "user-replay"
        ts = str(int(time.time()))
        nonce = "replay-nonce"
        token = self._valid_token(secret, nonce, ts, from_id)

        self.assertTrue(
            auth_module.verify_ephemeral_token(from_id, nonce, ts, token, session_secret=secret)
        )
        # Same nonce again must be rejected (replay protection).
        self.assertFalse(
            auth_module.verify_ephemeral_token(from_id, nonce, ts, token, session_secret=secret)
        )


if __name__ == "__main__":
    unittest.main()
