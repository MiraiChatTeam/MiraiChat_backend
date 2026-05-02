"""chat_backend/reviewer_demo_hook.py

Reviewer account helpers for App Store review accounts.

Provides `is_reviewer_username_hash` used at registration time to let the
reviewer account bypass REGISTRATION_KEY.  Demo conversation and bot-reply
logic has been removed — the client handles everything locally via
ReviewLocalProvider (Phase-R).

Disabled by default; activate with the environment variable:
    REVIEWER_DEMO_ENABLED=true

Reviewer test account:
    REVIEWER_EMAIL = "apple_review@miraichat.net"
"""
import hashlib
import os

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
REVIEWER_DEMO_ENABLED: bool = (
    os.getenv("REVIEWER_DEMO_ENABLED", "false").lower() == "true"
)

# ---------------------------------------------------------------------------
# Reviewer account configuration
# ---------------------------------------------------------------------------
REVIEWER_EMAIL: str = "apple_review@miraichat.net"

# All known reviewer email addresses.
REVIEWER_ACCOUNTS: list[str] = [REVIEWER_EMAIL]

# ---------------------------------------------------------------------------
# Client-side username hashing constants — must match ChatAPI.hashUsername
# in lib/chat_api.dart:
#   String hash = "$sanitized:$_globalSalt";
#   for (int i = 0; i < 5000; i++) { hash = sha256(hash); }
# ---------------------------------------------------------------------------
_CLIENT_SALT: str = "SECURE_ZK_ORCHESTRATOR_V1_@2024_PROD_SALT_99"
_CLIENT_HASH_ROUNDS: int = 5000

# One-time cache of email -> client-side username_hash (5000× SHA-256).
_client_hash_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client_hash(email: str) -> str:
    """Compute the username_hash as the Flutter client would (5000× SHA-256)."""
    if email in _client_hash_cache:
        return _client_hash_cache[email]
    h: str = f"{email.strip().lower()}:{_CLIENT_SALT}"
    for _ in range(_CLIENT_HASH_ROUNDS):
        h = hashlib.sha256(h.encode()).hexdigest()
    _client_hash_cache[email] = h
    return h


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_reviewer_username_hash(username_hash: str) -> bool:
    """Return True when *username_hash* (client-side hash) belongs to a reviewer.

    Used at registration time to bypass REGISTRATION_KEY for reviewer accounts.
    """
    if not REVIEWER_DEMO_ENABLED:
        return False
    for email in REVIEWER_ACCOUNTS:
        if username_hash == _client_hash(email):
            return True
    return False
