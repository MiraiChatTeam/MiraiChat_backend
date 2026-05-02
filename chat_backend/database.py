import os
import sqlite3

from chat_backend.settings import DEFAULT_STORAGE_LIMIT


DB_PATH = os.getenv("CHAT_DB_PATH", "chat.db")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE,
            username_hash TEXT UNIQUE,
            password TEXT,
            public_key TEXT,
            storage_limit INTEGER DEFAULT {DEFAULT_STORAGE_LIMIT},
            storage_used INTEGER DEFAULT 0,
            -- Deprecated: custom backends no longer store push tokens.
            fcm_token TEXT,
            fcm_token_updated_at DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id TEXT,
            device_id TEXT,
            device_name TEXT,
            session_secret TEXT NOT NULL UNIQUE,
            is_main INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            -- Deprecated: custom backends no longer store push tokens.
            fcm_token TEXT,
            fcm_platform TEXT,
            token_updated_at DATETIME,
            -- Deprecated: legacy server-side preview encryption key storage.
            -- Safe Preview v1 uses client-built preview_envelope_v1 instead.
            notification_key TEXT,
            notification_key_version INTEGER DEFAULT 1,
            grace_period_expires DATETIME,
            PRIMARY KEY (user_id, device_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offline_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver_id TEXT,
            payload TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_read_state (
            user_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            last_read_msg_id TEXT NOT NULL,
            last_read_rank INTEGER NOT NULL DEFAULT 0,
            last_read_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_by_device TEXT,
            CHECK(last_read_rank >= -9223372036854775808 AND last_read_rank <= 9223372036854775807),
            PRIMARY KEY (user_id, peer_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unread_messages (
            user_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            msg_id TEXT NOT NULL,
            msg_rank INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CHECK(msg_rank >= -9223372036854775808 AND msg_rank <= 9223372036854775807),
            PRIMARY KEY (user_id, msg_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reaction_push_idempotency_registry (
            registry_key TEXT PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_blocks (
            user_id TEXT NOT NULL,
            blocked_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, blocked_id)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_offline_messages_receiver_id ON offline_messages(receiver_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_offline_messages_receiver_id_id ON offline_messages(receiver_id, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_read_state_user_peer ON conversation_read_state(user_id, peer_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_unread_messages_user_id ON unread_messages(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_unread_messages_user_peer_rank ON unread_messages(user_id, peer_id, msg_rank)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reaction_push_idempotency_expiry ON reaction_push_idempotency_registry(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_blocks_user_id ON user_blocks(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_session_secret ON sessions(session_secret)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_message_leases (
            message_id INTEGER PRIMARY KEY,
            receiver_id TEXT NOT NULL,
            lease_token TEXT NOT NULL,
            lease_expires_at DATETIME NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_leases_receiver_expiry ON pending_message_leases(receiver_id, lease_expires_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_registry (
            file_id TEXT PRIMARY KEY,
            owner_id TEXT,
            filename TEXT,
            file_size INTEGER,
            upload_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_download_tokens (
            token TEXT PRIMARY KEY,
            file_id TEXT,
            expires_at DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ws_tickets (
            ticket_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_secret TEXT NOT NULL,
            expires_at DATETIME NOT NULL,
            used INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ws_tickets_user_id ON ws_tickets(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ws_tickets_expires_used ON ws_tickets(expires_at, used)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_licenses (
            license_key TEXT PRIMARY KEY,
            user_limit INTEGER DEFAULT 1000,
            expires_at DATETIME,
            secret TEXT,
            session_token TEXT,
            license_secret_hash TEXT,
            status TEXT,
            issued_at DATETIME,
            revoked_at DATETIME,
            plan TEXT,
            purchaser_ref TEXT,
            stripe_checkout_session_id TEXT,
            stripe_payment_intent_id TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licensed_servers (
            server_id TEXT PRIMARY KEY,
            license_key TEXT,
            server_public_url TEXT,
            pem_public_key TEXT,
            pem_fingerprint TEXT,
            key_status TEXT,
            issued_at DATETIME,
            last_bootstrap_at DATETIME,
            last_seen_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_licensed_servers_license_key "
        "ON licensed_servers (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_licensed_servers_pem_fingerprint "
        "ON licensed_servers (pem_fingerprint)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS central_server_tokens (
            token_hash TEXT PRIMARY KEY,
            server_id TEXT,
            license_key TEXT,
            pem_fingerprint TEXT,
            issued_at DATETIME,
            expires_at DATETIME,
            revoked_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_server_id "
        "ON central_server_tokens (server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_license_key "
        "ON central_server_tokens (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_expires_at "
        "ON central_server_tokens (expires_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_bootstrap_challenges (
            challenge_id TEXT PRIMARY KEY,
            license_key TEXT,
            server_id TEXT,
            nonce TEXT,
            expires_at DATETIME,
            used_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_server_bootstrap_challenges_server "
        "ON server_bootstrap_challenges (license_key, server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_server_bootstrap_challenges_expires_at "
        "ON server_bootstrap_challenges (expires_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS license_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            server_id TEXT,
            event_type TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_license_key "
        "ON license_audit_events (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_server_id "
        "ON license_audit_events (server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_created_at "
        "ON license_audit_events (created_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS license_usage (
            license_key TEXT,
            -- Deprecated: legacy token accounting; retained for DB compatibility only.
            fcm_token TEXT,
            PRIMARY KEY (license_key, fcm_token)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rotation_pins (
            domain TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (domain, fingerprint)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_key_rotation_config (
            id INTEGER PRIMARY KEY DEFAULT 1,
            rotation_enabled INTEGER DEFAULT 0,
            rotation_interval_days INTEGER DEFAULT 90,
            grace_period_days INTEGER DEFAULT 30,
            next_rotation_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_key_rotation_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_fingerprint TEXT NOT NULL,
            new_fingerprint TEXT NOT NULL,
            new_public_key TEXT NOT NULL,
            valid_from REAL NOT NULL,
            grace_period_ends_at REAL NOT NULL,
            signature TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS iap_donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            product_id TEXT NOT NULL,
            purchase_id TEXT NOT NULL UNIQUE,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_hash TEXT NOT NULL,
            donation_type TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            verification_data TEXT,
            verification_source TEXT,
            transaction_date TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cursor.execute("ALTER TABLE offline_messages DROP COLUMN receiver_token")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE offline_messages DROP COLUMN timestamp")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN session_secret TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN grace_period_expires DATETIME")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only.
        cursor.execute("ALTER TABLE sessions ADD COLUMN fcm_token TEXT")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only.
        cursor.execute("ALTER TABLE sessions ADD COLUMN fcm_platform TEXT")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only.
        cursor.execute("ALTER TABLE sessions ADD COLUMN token_updated_at DATETIME")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only. The server no
        # longer encrypts notification previews or writes notification keys.
        cursor.execute("ALTER TABLE sessions ADD COLUMN notification_key TEXT")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only.
        cursor.execute("ALTER TABLE sessions ADD COLUMN notification_key_version INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        # Deprecated: retained for existing DB compatibility only.
        cursor.execute("ALTER TABLE users ADD COLUMN fcm_token_updated_at DATETIME")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE offline_messages ADD COLUMN created_at DATETIME")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE conversation_read_state ADD COLUMN last_read_rank INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE conversation_read_state ADD COLUMN updated_by_device TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE server_licenses ADD COLUMN secret TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE server_licenses ADD COLUMN session_token TEXT")
    except Exception:
        pass
    for column_name, column_type in (
        ("license_secret_hash", "TEXT"),
        ("status", "TEXT"),
        ("issued_at", "DATETIME"),
        ("revoked_at", "DATETIME"),
        ("plan", "TEXT"),
        ("purchaser_ref", "TEXT"),
        ("stripe_checkout_session_id", "TEXT"),
        ("stripe_payment_intent_id", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE server_licenses ADD COLUMN {column_name} {column_type}")
        except Exception:
            pass

    cursor.execute(
        "UPDATE offline_messages SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''"
    )

    cursor.execute(
        "UPDATE users SET storage_limit = ? WHERE storage_limit IS NULL OR storage_limit <= 0",
        (DEFAULT_STORAGE_LIMIT,),
    )

    conn.commit()
    conn.close()
    print("✅ Database initialized with Per-Device Mailbox support.")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def migrate_iap_donations_schema() -> None:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE iap_donations ADD COLUMN receipt_id TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE iap_donations ADD COLUMN receipt_hash TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def migrate_license_architecture_schema() -> None:
    conn = get_db()
    cursor = conn.cursor()
    for column_name, column_type in (
        ("license_secret_hash", "TEXT"),
        ("status", "TEXT"),
        ("issued_at", "DATETIME"),
        ("revoked_at", "DATETIME"),
        ("plan", "TEXT"),
        ("purchaser_ref", "TEXT"),
        ("stripe_checkout_session_id", "TEXT"),
        ("stripe_payment_intent_id", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE server_licenses ADD COLUMN {column_name} {column_type}")
            conn.commit()
        except Exception:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licensed_servers (
            server_id TEXT PRIMARY KEY,
            license_key TEXT,
            server_public_url TEXT,
            pem_public_key TEXT,
            pem_fingerprint TEXT,
            key_status TEXT,
            issued_at DATETIME,
            last_bootstrap_at DATETIME,
            last_seen_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_licensed_servers_license_key "
        "ON licensed_servers (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_licensed_servers_pem_fingerprint "
        "ON licensed_servers (pem_fingerprint)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS central_server_tokens (
            token_hash TEXT PRIMARY KEY,
            server_id TEXT,
            license_key TEXT,
            pem_fingerprint TEXT,
            issued_at DATETIME,
            expires_at DATETIME,
            revoked_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_server_id "
        "ON central_server_tokens (server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_license_key "
        "ON central_server_tokens (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_server_tokens_expires_at "
        "ON central_server_tokens (expires_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_bootstrap_challenges (
            challenge_id TEXT PRIMARY KEY,
            license_key TEXT,
            server_id TEXT,
            nonce TEXT,
            expires_at DATETIME,
            used_at DATETIME
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_server_bootstrap_challenges_server "
        "ON server_bootstrap_challenges (license_key, server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_server_bootstrap_challenges_expires_at "
        "ON server_bootstrap_challenges (expires_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS license_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            server_id TEXT,
            event_type TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_license_key "
        "ON license_audit_events (license_key)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_server_id "
        "ON license_audit_events (server_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_license_audit_events_created_at "
        "ON license_audit_events (created_at)"
    )
    conn.commit()
    conn.close()


def initialize_database() -> None:
    init_db()
    migrate_license_architecture_schema()
    migrate_iap_donations_schema()
