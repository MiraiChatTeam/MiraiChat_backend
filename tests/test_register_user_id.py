"""Tests for registration user_id validation (_resolve_registration_user_id).

A client-supplied user_id is honored only when it is a canonical UUID; anything
else (SQL LIKE wildcards, JSON-injection fragments, arbitrary strings) is
rejected, and an absent value yields a fresh server UUID.

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

    def test_canonical_uuid_is_accepted_verbatim(self):
        canonical = "550e8400-e29b-41d4-a716-446655440000"
        user_id, err = legacy_app._resolve_registration_user_id(canonical)
        self.assertIsNone(err)
        self.assertEqual(user_id, canonical)

    def test_uuid_is_normalized(self):
        # Uppercase / surrounding whitespace is accepted and normalized.
        user_id, err = legacy_app._resolve_registration_user_id(
            "  550E8400-E29B-41D4-A716-446655440000  "
        )
        self.assertIsNone(err)
        self.assertEqual(user_id, "550e8400-e29b-41d4-a716-446655440000")

    def test_like_wildcards_are_rejected(self):
        for malicious in ("%", "_", "%admin%"):
            user_id, err = legacy_app._resolve_registration_user_id(malicious)
            self.assertIsNone(user_id, f"{malicious!r} should be rejected")
            self.assertEqual(err, "Invalid user_id")

    def test_injection_fragments_are_rejected(self):
        for malicious in ('","to_id":"', "../../etc/passwd", "admin", "' OR '1'='1"):
            user_id, err = legacy_app._resolve_registration_user_id(malicious)
            self.assertIsNone(user_id, f"{malicious!r} should be rejected")
            self.assertEqual(err, "Invalid user_id")

    def test_rejected_ids_never_contain_sql_wildcards(self):
        # Defense-in-depth contract: a returned (non-None) user_id can never
        # carry a LIKE wildcard into the offline-message cleanup queries.
        for value in ("%", "_", "x_y", "a%b", "550e8400-e29b-41d4-a716-446655440000", ""):
            user_id, _ = legacy_app._resolve_registration_user_id(value)
            if user_id is not None:
                self.assertNotIn("%", user_id)
                self.assertNotIn("_", user_id)


if __name__ == "__main__":
    unittest.main()
