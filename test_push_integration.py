import ast
import asyncio
import hashlib
import hmac
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

try:
    from fastapi import HTTPException

    import chat_backend.central_push_service as central_push
    import custom_backend.chat_backend.push as custom_push
except ModuleNotFoundError as exc:
    HTTPException = None
    central_push = None
    custom_push = None
    BACKEND_IMPORT_ERROR = exc
else:
    BACKEND_IMPORT_ERROR = None


class PushArchitectureContractTests(unittest.TestCase):
    def test_register_device_is_documented_as_central_only_registry(self):
        source = Path("chat_backend/central_push_service.py").read_text()

        self.assertIn('@router.post("/push/register_device")', source)
        self.assertIn("INSERT INTO central_device_registrations", source)
        self.assertIn(
            "Legacy token mirrors are disabled; central_device_registrations is canonical.",
            source,
        )

    def test_delegated_push_contract_is_user_id_based_with_pair_quota(self):
        source = Path("chat_backend/central_push_service.py").read_text()
        request_model = source[
            source.index("class DelegatedPushDescriptor"):
            source.index("# ------------------------------------------------------------------------------\n# Database schema")
        ]
        delegate_route = source[source.index('@router.post("/push/delegate_wake")'):]

        self.assertIn("serverId: str", request_model)
        self.assertIn("userId: str", request_model)
        self.assertIn("payload: DelegatedPushDescriptor", request_model)
        self.assertIn("preview_envelope_v1: Optional[Any] = None", request_model)
        self.assertIn("msg_id: Optional[str] = None", request_model)
        self.assertIn("conversation_id: Optional[str] = None", request_model)
        self.assertIn("enc_v: Optional[str] = None", request_model)
        self.assertNotIn("deviceId", request_model)
        self.assertNotIn("fcmToken", request_model)
        self.assertIn("PRIMARY KEY (server_id, user_id)", source)
        self.assertIn("central_delegated_wake_nonces", source)
        self.assertIn("PRIMARY KEY (license_key, server_id, nonce)", source)
        self.assertIn("_validate_central_server_token(_parse_bearer(authorization), conn)", delegate_route)
        self.assertIn('detail="license_token_mismatch"', delegate_route)
        self.assertIn('client_server_id = _validate_delegated_id("serverId", body.serverId)', delegate_route)
        self.assertIn('server_id = _validate_delegated_id("token server_id", token_info["server_id"])', delegate_route)
        self.assertIn("_check_and_increment_delegated_wake_quota(server_id, user_id, conn)", delegate_route)
        self.assertIn("WHERE user_id = ? ORDER BY registered_at DESC", delegate_route)
        self.assertIn("clean_payload = _compile_push_job_payload", delegate_route)
        self.assertIn("require_preview_envelope=False", delegate_route)
        self.assertIn("require_enc_v=False", delegate_route)
        self.assertLess(
            delegate_route.index("_record_delegated_wake_nonce(license_key, server_id, nonce, conn)"),
            delegate_route.index("_check_and_touch_pushhub_active_user("),
        )
        self.assertLess(
            delegate_route.index("_check_and_touch_pushhub_active_user("),
            delegate_route.index("_check_and_increment_delegated_wake_quota(server_id, user_id, conn)"),
        )
        self.assertLess(
            delegate_route.index("_check_and_increment_delegated_wake_quota(server_id, user_id, conn)"),
            delegate_route.index("from chat_backend.push_queue import enqueue_push"),
        )
        self.assertEqual(delegate_route.count("enqueue_push("), 1)

    def test_custom_backend_delegates_push_job_without_token_or_device_payload(self):
        source = Path("custom_backend/chat_backend/push.py").read_text()
        send_fcm_push = source[
            source.index("def send_fcm_push("):
            source.index("def push_delivery_status")
        ]

        self.assertIn("/central/push/delegate_wake", source)
        self.assertIn('"serverId": server_id', send_fcm_push)
        self.assertIn('"userId": target_user_id', send_fcm_push)
        self.assertIn('"payload": _delegated_push_payload(data_payload)', send_fcm_push)
        self.assertNotIn('"fcm_token"', send_fcm_push)
        self.assertNotIn('"deviceId"', send_fcm_push)
        self.assertIn("preview_envelope_v1", source)

    def test_pushhub_validate_requires_secret_and_does_not_return_it(self):
        source = Path("chat_backend/migration_app.py").read_text()
        request_model = source[
            source.index("class PushHubValidateRequest"):
            source.index("class PinSecurityEventRequest")
        ]
        validate_route = source[
            source.index('@app.post("/api/pushhub/validate")'):
            source.index('@app.post("/chat/pin_security_event")')
        ]

        self.assertIn("license_key: str", request_model)
        self.assertIn("license_secret: str", request_model)
        self.assertIn("server_id: str | None = None", request_model)
        self.assertIn("hmac.compare_digest", validate_route)
        self.assertIn('detail="invalid_license_secret"', validate_route)
        self.assertIn('detail="license_bound_to_different_server"', validate_route)
        self.assertIn('"ok": True', validate_route)
        self.assertIn('"session_token": session_token', validate_route)
        self.assertNotIn('"license_secret"', validate_route)

    def test_custom_backend_validate_sends_secret_and_does_not_echo_it(self):
        source = Path("custom_backend/chat_backend/migration_app.py").read_text()
        confirm_call = source[
            source.index("def _confirm_license_with_central("):
            source.index('@app.post("/api/pushhub/redeem")')
        ]
        validate_route = source[
            source.index('@app.post("/api/pushhub/authorize")'):
            source.index('@app.middleware("http")')
        ]

        self.assertIn('"license_key": license_key', confirm_call)
        self.assertIn('"license_secret": license_secret', confirm_call)
        self.assertIn('"server_id": server_id', confirm_call)
        self.assertIn('"license_key": license_key', source)
        self.assertIn('"license_secret": license_secret', source)
        self.assertIn("server_id = str(upstream_payload.get(\"server_id\")", source)
        self.assertNotIn('"license_secret"', validate_route)

    def test_push_conversation_id_is_hmac_opaque_and_stable(self):
        source = Path("chat_backend/push.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        helper = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_derive_push_conversation_id"
        )
        module = ast.Module(body=[helper], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "hmac": hmac,
            "hashlib": hashlib,
            "PUSH_CONVERSATION_ID_HMAC_KEY": "unit-test-hmac-key",
        }
        exec(compile(module, "push_conversation_helper", "exec"), namespace)
        derive = namespace["_derive_push_conversation_id"]

        first = derive("group-raw-1", "user-alice")
        second = derive("group-raw-1", "user-alice")
        different_group = derive("group-raw-2", "user-alice")
        different_user = derive("group-raw-1", "user-bob")

        self.assertEqual(first, second)
        self.assertNotEqual(first, different_group)
        self.assertNotEqual(first, different_user)
        self.assertNotEqual(first, "group-raw-1")
        self.assertRegex(first, r"^[0-9a-f]{32}$")

    def test_push_payload_replaces_raw_conversation_sources(self):
        source = Path("chat_backend/push.py").read_text(encoding="utf-8")
        enrich = source[
            source.index("def _enrich_push_data_with_preview"):
            source.index("def _validate_service_account_payload")
        ]

        self.assertIn('data.get("group_id")', enrich)
        self.assertIn('data.get("groupId")', enrich)
        self.assertIn('data.get("conversation_id")', enrich)
        self.assertIn("_derive_push_conversation_id", enrich)
        self.assertIn('data["conversation_id"] = conversation_id', enrich)
        self.assertIn('data.pop("group_id", None)', enrich)
        self.assertIn('data.pop("groupId", None)', enrich)

    def test_ios_push_payload_does_not_inject_empty_sender_or_content(self):
        source = Path("chat_backend/push.py").read_text(encoding="utf-8")
        sdk_ios_branch = source[
            source.index('if normalized_platform == "ios":\n                ios_data = dict(per_target_data or {})'):
            source.index('message_kwargs = {"token": fcm_token, "data": base_data}')
        ]
        self.assertIn('ios_data["type"]', sdk_ios_branch)
        self.assertNotIn('ios_data["sender"]', sdk_ios_branch)
        self.assertNotIn('ios_data["content"]', sdk_ios_branch)
        self.assertIn('"preview_envelope_v1"', source)
        self.assertIn("messaging.ApsAlert(title=title, body=body)", source)
        self.assertIn("badge=max(0, int(unread_badge_count))", source)
        self.assertIn('sound="default"', source)
        self.assertIn("mutable_content=True", source)

    def test_custom_backend_legacy_token_fields_are_schema_only(self):
        legacy_source = Path("custom_backend/chat_backend/legacy_app.py").read_text(encoding="utf-8")
        push_source = Path("custom_backend/chat_backend/push.py").read_text(encoding="utf-8")
        storm_source = Path("custom_backend/chat_backend/storm_guard.py").read_text(encoding="utf-8")
        database_source = Path("custom_backend/chat_backend/database.py").read_text(encoding="utf-8")

        self.assertIn("custom_backend_token_registry_disabled", legacy_source)
        self.assertIn("Deprecated: custom backends no longer store push tokens", database_source)
        self.assertIn("Deprecated: legacy server-side preview encryption key storage", database_source)
        self.assertNotIn("SELECT COALESCE(fcm_token", legacy_source)
        self.assertNotIn("WHERE fcm_token", legacy_source)
        self.assertNotIn("UPDATE sessions SET fcm_token", legacy_source)
        self.assertNotIn("UPDATE users SET fcm_token", legacy_source)
        self.assertNotIn("notification_key", legacy_source)
        self.assertNotIn("register_push_token_with_central", push_source)
        self.assertNotIn("fcm_token", storm_source)
        self.assertNotIn("notification_key", storm_source)


@unittest.skipIf(
    BACKEND_IMPORT_ERROR is not None,
    f"backend runtime dependencies unavailable: {BACKEND_IMPORT_ERROR}",
)
class PushArchitectureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "push_integration.db")
        self.old_get_db = central_push.get_db
        self.old_quota_limit = central_push._PUSH_QUOTA_LIMIT
        self.old_push_queue = sys.modules.get("chat_backend.push_queue")
        self.nonce_counter = 0
        central_push.get_db = self._connect
        self._init_db()

    def tearDown(self):
        central_push.get_db = self.old_get_db
        central_push._PUSH_QUOTA_LIMIT = self.old_quota_limit
        if self.old_push_queue is None:
            sys.modules.pop("chat_backend.push_queue", None)
        else:
            sys.modules["chat_backend.push_queue"] = self.old_push_queue
        self.tmp.cleanup()

    def test_register_device_writes_only_central_registry(self):
        token = self._issue_official_token("user-alice")

        result = asyncio.run(
            central_push.register_device(
                central_push.RegisterDeviceRequest(
                    device_token="fcm-token-1",
                    platform="ios",
                    push_caps="signal_v2",
                ),
                authorization=f"Bearer {token}",
            )
        )

        self.assertEqual(result["status"], "ok")
        with self._connect() as conn:
            central_rows = conn.execute(
                "SELECT user_id, device_token, platform, push_caps "
                "FROM central_device_registrations"
            ).fetchall()
            self.assertEqual(len(central_rows), 1)
            self.assertEqual(dict(central_rows[0])["user_id"], "user-alice")
            self.assertEqual(dict(central_rows[0])["device_token"], "fcm-token-1")
            self.assertEqual(dict(central_rows[0])["platform"], "ios")
            self.assertEqual(dict(central_rows[0])["push_caps"], "signal_v2")

            session_row = conn.execute(
                "SELECT fcm_token, fcm_platform FROM sessions WHERE user_id = ?",
                ("user-alice",),
            ).fetchone()
            self.assertIsNone(session_row["fcm_token"])
            self.assertEqual(session_row["fcm_platform"], "")
            user_row = conn.execute(
                "SELECT fcm_token FROM users WHERE user_id = ?",
                ("user-alice",),
            ).fetchone()
            self.assertIsNone(user_row["fcm_token"])
            custom_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM custom_backend_push_tokens"
            ).fetchone()
            self.assertEqual(custom_rows["c"], 0)

    def test_custom_push_sends_delegated_push_job_without_tokens_or_devices(self):
        with mock.patch.object(custom_push, "HUB_LICENSE_KEY", "lic-1"), \
                mock.patch.object(custom_push, "HUB_LICENSE_SECRET", "secret-1"), \
                mock.patch.object(custom_push, "HUB_SESSION_TOKEN", "session-1"), \
                mock.patch.object(custom_push, "CENTRAL_SERVER_TOKEN", "server-token-1"), \
                mock.patch.object(custom_push, "PUBLIC_HUB_URL", "https://central.example"), \
                mock.patch.object(custom_push.requests, "post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"status": "ok"}

            custom_push.send_fcm_push(
                "user-alice",
                "ignored title",
                "ignored body",
                data_payload={"preview_envelope_v1": {"opaque": True}},
            )

        self.assertEqual(post.call_count, 1)
        url = post.call_args.kwargs["url"] if "url" in post.call_args.kwargs else post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertEqual(url, "https://central.example/central/push/delegate_wake")
        self.assertEqual(body["userId"], "user-alice")
        self.assertIn("serverId", body)
        self.assertNotIn("fcm_token", body)
        self.assertNotIn("deviceId", body)
        self.assertEqual(body["payload"]["preview_envelope_v1"], {"opaque": True})
        self.assertEqual(body["payload"]["type"], "chat_message")

    def test_delegated_push_uses_user_id_registry_and_quota_key(self):
        self._insert_license("lic-1", "secret-1")
        self._insert_central_device("server-official", "user-alice", "token-1", "ios")
        self._insert_central_device("server-official", "user-alice", "token-2", "android")
        enqueued = []
        sys.modules["chat_backend.push_queue"] = types.SimpleNamespace(
            enqueue_push=lambda user_id, title, body, *, data_payload=None, **_: enqueued.append(
                (user_id, title, body, data_payload)
            ) or True
        )
        central_push._PUSH_QUOTA_LIMIT = 1

        result = asyncio.run(
            central_push.delegate_wake(
                self._delegated_wake_request(
                    license_key="lic-1",
                    secret="secret-1",
                    server_id="custom-a",
                    user_id="user-alice",
                )
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dispatched"])
        self.assertEqual(enqueued, [(
            "user-alice",
            "MiraiChat",
            "A message from MiraiChat",
            {
                "type": "chat_message",
                "msg_id": "msg-1",
                "conversation_id": "conv-1",
                "enc_v": "2",
                "preview_envelope_v1": {"opaque": True},
            },
        )])

        with self._connect() as conn:
            token_count = conn.execute(
                "SELECT COUNT(*) AS c FROM central_device_registrations WHERE user_id = ?",
                ("user-alice",),
            ).fetchone()["c"]
            self.assertEqual(token_count, 2)
            quota_row = conn.execute(
                "SELECT server_id, user_id, push_count FROM central_delegated_wake_quota"
            ).fetchone()
            self.assertEqual(dict(quota_row), {
                "server_id": "custom-a",
                "user_id": "user-alice",
                "push_count": 1,
            })

        with self.assertRaises(HTTPException) as exhausted:
            asyncio.run(
                central_push.delegate_wake(
                    self._delegated_wake_request(
                        license_key="lic-1",
                        secret="secret-1",
                        server_id="custom-a",
                        user_id="user-alice",
                    )
                )
        )
        self.assertEqual(exhausted.exception.status_code, 429)
        self.assertEqual(exhausted.exception.detail, "delegated_wake_quota_exceeded")
        self.assertEqual(len(enqueued), 1)

        with self.assertRaises(HTTPException) as forged_server_exhausted:
            asyncio.run(
                central_push.delegate_wake(
                    self._delegated_wake_request(
                        license_key="lic-1",
                        secret="secret-1",
                        server_id="custom-b",
                        signed_server_id="custom-a",
                        user_id="user-alice",
                    )
                )
            )
        self.assertEqual(forged_server_exhausted.exception.status_code, 429)
        self.assertEqual(forged_server_exhausted.exception.detail, "delegated_wake_quota_exceeded")
        self.assertEqual(len(enqueued), 1)

    def test_delegated_wake_accepts_minimal_wake_only_payload(self):
        self._insert_license("lic-1", "secret-1")
        self._insert_central_device("server-official", "user-alice", "token-1", "ios")
        enqueued = []
        sys.modules["chat_backend.push_queue"] = types.SimpleNamespace(
            enqueue_push=lambda user_id, title, body, *, data_payload=None, **_: enqueued.append(
                (user_id, title, body, data_payload)
            ) or True
        )

        result = asyncio.run(
            central_push.delegate_wake(
                self._delegated_wake_request(
                    license_key="lic-1",
                    secret="secret-1",
                    server_id="custom-a",
                    user_id="user-alice",
                    payload=central_push.DelegatedPushDescriptor(type="chat_message"),
                )
            )
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dispatched"])
        self.assertEqual(enqueued, [(
            "user-alice",
            "MiraiChat",
            "A message from MiraiChat",
            {"type": "chat_message"},
        )])

    def test_delegated_wake_replay_is_rejected_without_consuming_quota(self):
        self._insert_license("lic-1", "secret-1")
        self._insert_central_device("server-official", "user-alice", "token-1", "ios")
        enqueued = []
        sys.modules["chat_backend.push_queue"] = types.SimpleNamespace(
            enqueue_push=lambda user_id, title, body, *, data_payload=None, **_: enqueued.append(
                (user_id, title, body, data_payload)
            ) or True
        )

        request = self._delegated_wake_request(
            license_key="lic-1",
            secret="secret-1",
            server_id="custom-a",
            user_id="user-alice",
            nonce="replay-nonce-1",
        )

        first_result = asyncio.run(central_push.delegate_wake(request))
        self.assertEqual(first_result["status"], "ok")
        self.assertTrue(first_result["dispatched"])

        with self.assertRaises(HTTPException) as replayed:
            asyncio.run(central_push.delegate_wake(request))

        self.assertEqual(replayed.exception.status_code, 409)
        self.assertEqual(replayed.exception.detail, "delegated_wake_replay_detected")
        self.assertEqual(len(enqueued), 1)

        with self._connect() as conn:
            nonce_row = conn.execute(
                "SELECT license_key, server_id, nonce, expires_at "
                "FROM central_delegated_wake_nonces"
            ).fetchone()
            self.assertEqual(nonce_row["license_key"], "lic-1")
            self.assertEqual(nonce_row["server_id"], "custom-a")
            self.assertEqual(nonce_row["nonce"], "replay-nonce-1")
            self.assertTrue(nonce_row["expires_at"])

            quota_row = conn.execute(
                "SELECT server_id, user_id, push_count FROM central_delegated_wake_quota"
            ).fetchone()
            self.assertEqual(dict(quota_row), {
                "server_id": "custom-a",
                "user_id": "user-alice",
                "push_count": 1,
            })

    def test_delegated_wake_rejects_unbound_license(self):
        self._insert_license("lic-1", "secret-1", server_id="")
        self._insert_central_device("server-official", "user-alice", "token-1", "ios")

        with self.assertRaises(HTTPException) as unbound:
            asyncio.run(
                central_push.delegate_wake(
                    self._delegated_wake_request(
                        license_key="lic-1",
                        secret="secret-1",
                        server_id="custom-a",
                        user_id="user-alice",
                    )
                )
            )

        self.assertEqual(unbound.exception.status_code, 403)
        self.assertEqual(unbound.exception.detail, "license_not_bound_to_server")
        with self._connect() as conn:
            quota_count = conn.execute(
                "SELECT COUNT(*) AS c FROM central_delegated_wake_quota"
            ).fetchone()["c"]
            self.assertEqual(quota_count, 0)

    def test_pushhub_user_limit_is_license_wide_and_separate_from_wake_quota(self):
        self._insert_license("lic-1", "secret-1", user_limit=1)
        self._insert_central_device("server-official", "user-alice", "token-1", "ios")
        central_push._PUSH_QUOTA_LIMIT = 100
        enqueued = []
        sys.modules["chat_backend.push_queue"] = types.SimpleNamespace(
            enqueue_push=lambda user_id, title, body, *, data_payload=None, **_: enqueued.append(
                (user_id, title, body, data_payload)
            ) or True
        )

        first_result = asyncio.run(
            central_push.delegate_wake(
                self._delegated_wake_request(
                    license_key="lic-1",
                    secret="secret-1",
                    server_id="custom-a",
                    user_id="user-alice",
                )
            )
        )
        self.assertEqual(first_result["status"], "ok")

        with self.assertRaises(HTTPException) as exceeded:
            asyncio.run(
                central_push.delegate_wake(
                    self._delegated_wake_request(
                        license_key="lic-1",
                        secret="secret-1",
                        server_id="custom-b",
                        signed_server_id="custom-a",
                        user_id="user-bob",
                    )
                )
            )
        self.assertEqual(exceeded.exception.status_code, 403)
        self.assertEqual(exceeded.exception.detail, "pushhub_user_limit_exceeded")
        self.assertEqual(len(enqueued), 1)

        with self._connect() as conn:
            active_rows = conn.execute(
                "SELECT server_id, user_hash FROM central_pushhub_active_users "
                "WHERE license_key = ?",
                ("lic-1",),
            ).fetchall()
            self.assertEqual(len(active_rows), 1)
            self.assertEqual(active_rows[0]["server_id"], "custom-a")
            self.assertNotIn("user-alice", active_rows[0]["user_hash"])

            quota_rows = conn.execute(
                "SELECT server_id, user_id, push_count FROM central_delegated_wake_quota"
            ).fetchall()
            self.assertEqual(len(quota_rows), 1)
            self.assertEqual(dict(quota_rows[0]), {
                "server_id": "custom-a",
                "user_id": "user-alice",
                "push_count": 1,
            })

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE users (user_id TEXT PRIMARY KEY, fcm_token TEXT)"
            )
            conn.execute(
                "CREATE TABLE sessions ("
                "user_id TEXT, session_secret TEXT, fcm_token TEXT, "
                "fcm_platform TEXT DEFAULT '')"
            )
            conn.execute(
                "CREATE TABLE custom_backend_push_tokens ("
                "license_key TEXT, fcm_token TEXT, user_id TEXT, platform TEXT)"
            )
            conn.execute(
                "CREATE TABLE server_licenses ("
                "license_key TEXT PRIMARY KEY, user_limit INTEGER, "
                "expires_at TEXT, secret TEXT, server_id TEXT, server_public_url TEXT)"
            )
            conn.execute(
                "INSERT INTO users (user_id, fcm_token) VALUES (?, NULL)",
                ("user-alice",),
            )
            conn.execute(
                "INSERT INTO sessions "
                "(user_id, session_secret, fcm_token, fcm_platform) "
                "VALUES (?, ?, NULL, '')",
                ("user-alice", "session-alice"),
            )
            conn.commit()

    def _issue_official_token(self, user_id):
        with self._connect() as conn:
            central_push._ensure_central_tables(conn)
            return central_push._issue_token(user_id, conn)["official_token"]

    def _insert_license(self, license_key, secret, user_limit=1000, server_id="custom-a"):
        expires_at = (datetime.utcnow() + timedelta(days=1)).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO server_licenses "
                "(license_key, user_limit, expires_at, secret, server_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (license_key, user_limit, expires_at, secret, server_id),
            )
            conn.commit()

    def _insert_central_device(self, server_id, user_id, token, platform):
        with self._connect() as conn:
            central_push._ensure_central_tables(conn)
            conn.execute(
                "INSERT INTO central_device_registrations "
                "(server_id, user_id, device_token, platform, registered_at, push_caps) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (server_id, user_id, token, platform, central_push._utc_iso(), "signal_v2"),
            )
            conn.commit()

    def _delegated_wake_request(
        self,
        *,
        license_key,
        secret,
        server_id,
        user_id,
        nonce=None,
        signed_server_id=None,
        payload=None,
    ):
        timestamp = str(int(time.time()))
        if nonce is None:
            self.nonce_counter += 1
            nonce = f"nonce-{self.nonce_counter}"
        server_id_for_signature = signed_server_id or server_id
        signature = hmac.new(
            secret.encode(),
            f"{license_key}{server_id_for_signature}{user_id}{timestamp}{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return central_push.DelegatedWakeRequest(
            licenseKey=license_key,
            serverId=server_id,
            userId=user_id,
            payload=payload or central_push.DelegatedPushDescriptor(
                type="chat_message",
                msg_id="msg-1",
                conversation_id="conv-1",
                enc_v="2",
                preview_envelope_v1={"opaque": True},
            ),
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
        )


if __name__ == "__main__":
    unittest.main()
