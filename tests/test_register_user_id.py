"""Tests for registration user_id validation (_resolve_registration_user_id).

Clients may pick their own identifier (e.g. a preferred username) as long as it
uses only safe characters [A-Za-z0-9._-] within a length bound. Injection
characters — SQL LIKE wildcards, quotes, backslashes, whitespace — are rejected,
and an absent value yields a fresh server UUID.

Importing chat_backend.legacy_app pulls the full backend runtime (fastapi,
cryptography, ...) and initializes a database, so the test points CHAT_DB_PATH
at a throwaway file and is skipped when the deps are unavailable.
"""

import os
import tempfile
import unittest
import uuid

# Isolate any DB side effects of importing the app module.
_TMP_DB = os.path.join(tempfile.gettempdir(), "miraichat_test_register.db")
os.environ.setdefault("CHAT_DB_PATH", _TMP_DB)

try:
    from chat_backend import legacy_app
except Exception as exc:  # pragma: no cover - exercised only without deps
    legacy_app = None
    APP_IMPORT_ERROR = exc
else:
    APP_IMPORT_ERROR = None


@unittest.skipIf(
    APP_IMPORT_ERROR is not None,
    f"backend runtime dependencies unavailable: {APP_IMPORT_ERROR}",
)
class ResolveRegistrationUserIdTests(unittest.TestCase):
    def test_absent_value_generates_server_uuid(self):
        for supplied in ("", "   ", None):
            user_id, err = legacy_app._resolve_registration_user_id(supplied)
            self.assertIsNone(err)
            # Must be a valid canonical UUID string.
            self.assertEqual(str(uuid.UUID(user_id)), user_id)

    def test_custom_usernames_are_allowed(self):
        # The whole point: users may choose their own identifier.
        for name in ("shen_yi", "Alice.99", "cool-user", "user_2026", "a", "A1b2C3", "x" * 64):
            user_id, err = legacy_app._resolve_registration_user_id(name)
            self.assertIsNone(err, f"{name!r} should be accepted")
            self.assertEqual(user_id, name)

    def test_surrounding_whitespace_is_trimmed(self):
        user_id, err = legacy_app._resolve_registration_user_id("  shen_yi  ")
        self.assertIsNone(err)
        self.assertEqual(user_id, "shen_yi")

    def test_like_wildcards_are_rejected(self):
        # '%' is rejected outright. ('_' is a normal username character and is
        # allowed; the cleanup queries escape LIKE metacharacters — see the
        # hardening PR's _escape_like — so '_' cannot widen a match.)
        for malicious in ("%", "%admin%", "user%", "50%off"):
            user_id, err = legacy_app._resolve_registration_user_id(malicious)
            self.assertIsNone(user_id, f"{malicious!r} should be rejected")
            self.assertEqual(err, "Invalid user_id")

    def test_quote_backslash_and_injection_fragments_are_rejected(self):
        for malicious in (
            '","to_id":"',
            "../../etc/passwd",
            "' OR '1'='1",
            "back\\slash",
            'quote"inside',
            "has space",
            "emoji😀",
        ):
            user_id, err = legacy_app._resolve_registration_user_id(malicious)
            self.assertIsNone(user_id, f"{malicious!r} should be rejected")
            self.assertEqual(err, "Invalid user_id")

    def test_overlong_value_is_rejected(self):
        user_id, err = legacy_app._resolve_registration_user_id("a" * 65)
        self.assertIsNone(user_id)
        self.assertEqual(err, "Invalid user_id")

    def test_accepted_ids_never_contain_dangerous_metachars(self):
        # Defense-in-depth contract: a returned (non-None) user_id can never
        # carry a quote / backslash / % wildcard / whitespace into the cleanup
        # queries. ('_' is permitted; LIKE escaping handles it at the call site.)
        samples = ("%", "x_y", "a%b", 'q"q', "back\\x", "shen_yi", "Alice.99", "", "ok-name")
        for value in samples:
            user_id, _ = legacy_app._resolve_registration_user_id(value)
            if user_id is not None:
                for bad in ("%", '"', "\\", "'", " "):
                    self.assertNotIn(bad, user_id, f"{value!r} -> {user_id!r} leaked {bad!r}")


if __name__ == "__main__":
    unittest.main()
