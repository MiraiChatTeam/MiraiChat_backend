"""
new_main.py

Scalable migration entrypoint that keeps full backward compatibility with legacy
`main.py` while preparing the service for a staged move to PostgreSQL/Redis and
multi-instance deployment.

Design goals:
1) Zero forced user re-registration.
2) Keep existing API/WS behavior by delegating to legacy app.
3) Add readiness/health endpoints and optional async infra pools.
4) Enable safe canary rollout and instant rollback.

Run:
    uvicorn new_main:app --host 0.0.0.0 --port 8000

By default, all existing routes are served by legacy `main.app`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sys
import time
import uuid
import requests
import stat
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from chat_backend.database import get_db
from chat_backend.settings import PUBLIC_HUB_URL, SIGNING_KEY_FILE
from chat_backend.stripe_router import router as stripe_router


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "chat-backend-new")
    service_version: str = os.getenv("SERVICE_VERSION", "3.0.0-migration")

    # Compatibility switch:
    # true  -> delegate all existing APIs/WS to legacy main.py
    # false -> new app only (for future full cutover when legacy is removed)
    legacy_fallback_enabled: bool = os.getenv(
        "LEGACY_FALLBACK_ENABLED", "true"
    ).lower() in ("1", "true", "yes")

    # Optional PostgreSQL read/write pool (future migration target)
    pg_dsn: str = os.getenv("PG_DSN", "").strip()
    pg_pool_min: int = max(1, int(os.getenv("PG_POOL_MIN", "5")))
    pg_pool_max: int = max(5, int(os.getenv("PG_POOL_MAX", "40")))

    # Optional Redis (presence/cache/pubsub)
    redis_url: str = os.getenv("REDIS_URL", "").strip()

    # Safety: if true, fail startup when optional pools cannot initialize.
    strict_pool_startup: bool = os.getenv("STRICT_POOL_STARTUP", "false").lower() in (
        "1",
        "true",
        "yes",
    )


class RuntimeState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.pg_pool: Any = None
        self.redis_client: Any = None
        self.pool_errors: list[str] = []


SETTINGS = Settings()
STATE = RuntimeState()


class PushHubRedeemRequest(BaseModel):
    checkout_session_id: str


class PushHubConfirmRequest(BaseModel):
    license_key: str | None = None
    license_secret: str | None = None
    server_public_url: str | None = None
    # Backward-compat aliases
    license: str | None = None
    secret: str | None = None


def _is_scheme_b_safe_component(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in "._:-" for ch in value)


def _safe_scheme_b_component(value: str, *, prefix: str, max_len: int = 128) -> str:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= max_len and _is_scheme_b_safe_component(normalized):
        return normalized
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _delegated_server_id(license_key: str) -> str:
    configured = os.getenv("PUSH_DELEGATED_SERVER_ID", "").strip()
    if configured:
        return _safe_scheme_b_component(configured, prefix="server")
    seed = (license_key or PUBLIC_HUB_URL or "custom-backend").strip()
    digest = hashlib.sha256(seed.encode()).hexdigest()[:32]
    return f"custom-{digest}"


def _uptime_seconds() -> int:
    return int(time.time() - STATE.started_at)


async def _init_pg_pool() -> None:
    if not SETTINGS.pg_dsn:
        return

    try:
        import asyncpg  # type: ignore

        STATE.pg_pool = await asyncpg.create_pool(
            dsn=SETTINGS.pg_dsn,
            min_size=SETTINGS.pg_pool_min,
            max_size=SETTINGS.pg_pool_max,
            command_timeout=10,
        )
    except Exception as exc:  # pragma: no cover - runtime env dependent
        msg = f"pg_pool_init_failed:{exc}"
        STATE.pool_errors.append(msg)
        if SETTINGS.strict_pool_startup:
            raise


async def _close_pg_pool() -> None:
    pool = STATE.pg_pool
    STATE.pg_pool = None
    if pool is not None:
        await pool.close()


async def _init_redis() -> None:
    if not SETTINGS.redis_url:
        return

    try:
        import redis.asyncio as redis  # type: ignore

        client = redis.from_url(SETTINGS.redis_url, encoding="utf-8", decode_responses=True)
        await client.ping()
        STATE.redis_client = client
    except Exception as exc:  # pragma: no cover - runtime env dependent
        msg = f"redis_init_failed:{exc}"
        STATE.pool_errors.append(msg)
        if SETTINGS.strict_pool_startup:
            raise


async def _close_redis() -> None:
    client = STATE.redis_client
    STATE.redis_client = None
    if client is not None:
        await client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE.started_at = time.time()
    STATE.pool_errors.clear()

    # Initialize optional infra pools in parallel to keep startup fast.
    await asyncio.gather(_init_pg_pool(), _init_redis())

    try:
        yield
    finally:
        await asyncio.gather(_close_pg_pool(), _close_redis())


app = FastAPI(
    title="Chat Backend (Migration Entry)",
    version=SETTINGS.service_version,
    lifespan=lifespan,
)

app.include_router(stripe_router)


def _ensure_pushhub_tables() -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pushhub_checkout_redeems (
            checkout_session_id TEXT PRIMARY KEY,
            license_key TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def _normalize_hub_base_url(raw_url: str) -> str:
    base = (raw_url or "").strip()
    if not base:
        base = "https://public.miraichat.net"
    if "://" not in base:
        base = f"https://{base}"
    return base.rstrip("/")


def _extract_json_dict(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _extract_error_message(payload: dict[str, Any], fallback: str) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("error", "message", "msg"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for key in ("error", "message", "msg"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return fallback


def _store_confirmed_pushhub_license(
    license_key: str,
    expiry_raw: str,
    user_limit: int,
    server_id: str = "",
    pem_fingerprint: str = "",
    server_public_url: str = "",
    central_server_token: str = "",
    token_expires_at: str = "",
) -> None:
    _ensure_pushhub_tables()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT OR REPLACE INTO server_licenses (license_key, user_limit, expires_at, secret, session_token)
            VALUES (?, ?, ?, NULL, NULL)
            """,
            (
                license_key,
                max(int(user_limit), 1),
                (expiry_raw or None),
            ),
        )
        if server_id:
            issued_at = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO licensed_servers
                    (
                        server_id,
                        license_key,
                        server_public_url,
                        pem_fingerprint,
                        key_status,
                        issued_at,
                        last_seen_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    license_key = excluded.license_key,
                    server_public_url = excluded.server_public_url,
                    pem_fingerprint = excluded.pem_fingerprint,
                    key_status = excluded.key_status,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    server_id,
                    license_key,
                    server_public_url,
                    pem_fingerprint,
                    "active",
                    issued_at,
                    issued_at,
                ),
            )
            if central_server_token:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO central_server_tokens
                        (token_hash, server_id, license_key, pem_fingerprint, issued_at, expires_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        hashlib.sha256(central_server_token.encode()).hexdigest(),
                        server_id,
                        license_key,
                        pem_fingerprint,
                        issued_at,
                        token_expires_at or None,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _write_server_signing_key_pem(pem_text: str) -> bool:
    normalized_pem = str(pem_text or "").strip()
    if not normalized_pem:
        return False

    key_path = Path(SIGNING_KEY_FILE).expanduser()
    if key_path.parent != Path(""):
        key_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = key_path.with_name(f"{key_path.name}.tmp")
    tmp_path.write_text(normalized_pem + "\n", encoding="ascii")
    try:
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    os.replace(tmp_path, key_path)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    _refresh_legacy_signing_key_from_disk(key_path)
    return True


def _refresh_legacy_signing_key_from_disk(key_path: Path) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        import chat_backend.legacy_app as legacy_app

        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )
        legacy_app._private_key = private_key
        legacy_app._server_public_key = private_key.public_key()
    except Exception as exc:
        print(f"[pushhub] PEM saved but in-memory signing key refresh failed: {exc}")


def _refresh_pushhub_runtime_state(
    *,
    license_key: str,
    central_server_token: str,
    server_id: str,
) -> None:
    os.environ["HUB_LICENSE_KEY"] = license_key
    if central_server_token:
        os.environ["CENTRAL_SERVER_TOKEN"] = central_server_token
    os.environ["PUSH_DELEGATED_SERVER_ID"] = server_id

    settings_module = sys.modules.get("chat_backend.settings")
    if settings_module is not None:
        setattr(settings_module, "HUB_LICENSE_KEY", license_key)
        if central_server_token:
            setattr(settings_module, "CENTRAL_SERVER_TOKEN", central_server_token)

    push_module = sys.modules.get("chat_backend.push")
    if push_module is not None:
        setattr(push_module, "HUB_LICENSE_KEY", license_key)
        if central_server_token:
            setattr(push_module, "CENTRAL_SERVER_TOKEN", central_server_token)


def _load_server_signing_key():
    key_path = Path(SIGNING_KEY_FILE).expanduser()
    if not key_path.exists():
        raise FileNotFoundError(f"{SIGNING_KEY_FILE} does not exist")
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(key_path.read_bytes(), password=None)


def _bootstrap_server_with_central(
    *,
    hub_base: str,
    server_id: str,
    license_key: str,
) -> dict[str, Any]:
    try:
        private_key = _load_server_signing_key()
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{timestamp}{nonce}{server_id}{license_key}".encode()
        signature = private_key.sign(message).hex()
        response = requests.post(
            f"{hub_base}/central/server/auth/bootstrap",
            json={
                "server_id": server_id,
                "license_key": license_key,
                "timestamp": timestamp,
                "nonce": nonce,
                "signature": signature,
            },
            timeout=20,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to bootstrap server identity: {exc}",
            "central_server_token": "",
            "expires_at": "",
        }

    payload = _extract_json_dict(response)
    if response.status_code >= 400:
        return {
            "ok": False,
            "message": _extract_error_message(payload, "Central server bootstrap failed"),
            "central_server_token": "",
            "expires_at": "",
        }

    central_server_token = str(payload.get("central_server_token") or "").strip()
    expires_at = str(payload.get("expires_at") or "").strip()
    if not central_server_token:
        return {
            "ok": False,
            "message": "Central server bootstrap did not return a token",
            "central_server_token": "",
            "expires_at": expires_at,
        }
    return {
        "ok": True,
        "message": "server_bootstrap_ok",
        "central_server_token": central_server_token,
        "expires_at": expires_at,
        "server_id": str(payload.get("server_id") or server_id).strip(),
        "pem_fingerprint": str(payload.get("pem_fingerprint") or "").strip(),
    }


def _confirm_license_with_central(
    license_key: str,
    license_secret: str,
    server_public_url: str,
) -> dict[str, Any]:
    # Basic format check only; central is the source of truth.
    if not license_key or not license_secret:
        return {
            "ok": False,
            "message": "license_key and license_secret are required",
            "session_token": "",
            "expiry": "",
            "limit": 0,
            "server_id": "",
            "pem_fingerprint": "",
            "pem_saved": False,
        }

    hub_base = _normalize_hub_base_url(PUBLIC_HUB_URL)
    request_payload = {
        "license_key": license_key,
        "license_secret": license_secret,
        "server_public_url": server_public_url,
    }

    print(f"[pushhub] confirm -> central authorize request for license={license_key[:10]}...")
    try:
        upstream_res = requests.post(
            f"{hub_base}/central/license/authorize",
            json=request_payload,
            timeout=20,
        )
    except Exception as exc:
        print(f"[pushhub] central authorize request failed: {exc}")
        return {
            "ok": False,
            "message": f"Failed to reach central validation service: {exc}",
            "session_token": "",
            "expiry": "",
            "limit": 0,
            "server_id": "",
            "pem_fingerprint": "",
            "pem_saved": False,
        }

    upstream_payload = _extract_json_dict(upstream_res)
    if upstream_res.status_code >= 400:
        message = _extract_error_message(upstream_payload, "Central license authorization failed")
        print(
            f"[pushhub] central authorize -> ok=false http={upstream_res.status_code} message={message}"
        )
        return {
            "ok": False,
            "message": message,
            "session_token": "",
            "expiry": "",
            "limit": 0,
            "server_id": "",
            "pem_fingerprint": "",
            "pem_saved": False,
        }

    server_id = str(upstream_payload.get("server_id") or "").strip()
    pem_fingerprint = str(upstream_payload.get("pem_fingerprint") or "").strip()
    if not server_id or not pem_fingerprint:
        message = _extract_error_message(upstream_payload, "Central authorization response missing server identity")
        print(f"[pushhub] central authorize -> ok=false message={message}")
        return {
            "ok": False,
            "message": message,
            "session_token": "",
            "expiry": "",
            "limit": 0,
            "server_id": server_id,
            "pem_fingerprint": pem_fingerprint,
            "pem_saved": False,
        }

    expiry = str(upstream_payload.get("expiry") or "").strip()
    user_limit = int(upstream_payload.get("limit") or 1000)
    pem_saved = _write_server_signing_key_pem(
        str(upstream_payload.get("server_signing_key_pem") or "")
    )
    bootstrap_result = _bootstrap_server_with_central(
        hub_base=hub_base,
        server_id=server_id,
        license_key=license_key,
    )
    if not bootstrap_result.get("ok"):
        return {
            "ok": False,
            "message": str(bootstrap_result.get("message") or "Central server bootstrap failed"),
            "session_token": "",
            "central_server_token": "",
            "token_expires_at": str(bootstrap_result.get("expires_at") or ""),
            "expiry": expiry,
            "limit": user_limit,
            "server_id": server_id,
            "pem_fingerprint": pem_fingerprint,
            "pem_saved": pem_saved,
            "bootstrap_ok": False,
        }
    central_server_token = str(bootstrap_result.get("central_server_token") or "").strip()
    token_expires_at = str(bootstrap_result.get("expires_at") or "").strip()
    bootstrap_pem_fingerprint = str(bootstrap_result.get("pem_fingerprint") or "").strip()
    if bootstrap_pem_fingerprint:
        pem_fingerprint = bootstrap_pem_fingerprint

    _store_confirmed_pushhub_license(
        license_key=license_key,
        expiry_raw=expiry,
        user_limit=user_limit,
        server_id=server_id,
        pem_fingerprint=pem_fingerprint,
        server_public_url=server_public_url,
        central_server_token=central_server_token,
        token_expires_at=token_expires_at,
    )
    _refresh_pushhub_runtime_state(
        license_key=license_key,
        central_server_token=central_server_token,
        server_id=server_id,
    )

    print(
        "[pushhub] central authorize -> ok=true "
        f"server_id={server_id} pem_fingerprint={pem_fingerprint} "
        f"pem_saved={pem_saved} bootstrap_ok=True"
    )
    return {
        "ok": True,
        "message": "license_valid",
        "central_server_token": central_server_token,
        "token_expires_at": token_expires_at,
        "expiry": expiry,
        "limit": user_limit,
        "server_id": server_id,
        "pem_fingerprint": pem_fingerprint,
        "pem_saved": pem_saved,
        "bootstrap_ok": True,
    }


@app.post("/api/pushhub/redeem")
async def pushhub_redeem(payload: PushHubRedeemRequest) -> dict[str, str]:
    checkout_session_id = str(payload.checkout_session_id or "").strip()
    if not checkout_session_id:
        raise HTTPException(status_code=400, detail="checkout_session_id is required")

    _ensure_pushhub_tables()
    hub_base = _normalize_hub_base_url(PUBLIC_HUB_URL)

    try:
        upstream_res = requests.post(
            f"{hub_base}/api/pushhub/redeem",
            json={"checkout_session_id": checkout_session_id},
            timeout=20,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach central push hub: {exc}")

    upstream_payload = _extract_json_dict(upstream_res)
    if upstream_res.status_code >= 400:
        detail = upstream_payload.get("detail") if isinstance(upstream_payload, dict) else None
        raise HTTPException(status_code=upstream_res.status_code, detail=detail or "Push hub redeem failed")

    license_key = str(upstream_payload.get("license") or "").strip()
    license_secret = str(upstream_payload.get("license_secret") or upstream_payload.get("secret") or "").strip()

    if not license_key:
        raise HTTPException(status_code=502, detail="Central push hub did not return a license")

    conn = get_db()
    cursor = conn.cursor()
    try:
        expiry = datetime.now() + timedelta(days=365)
        cursor.execute(
            """
            INSERT OR REPLACE INTO server_licenses (license_key, user_limit, expires_at, secret, session_token)
            VALUES (?, ?, ?, NULL, NULL)
            """,
            (license_key, 1000, expiry),
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO pushhub_checkout_redeems (checkout_session_id, license_key)
            VALUES (?, ?)
            """,
            (checkout_session_id, license_key),
        )
        conn.commit()
        return {
            "license": license_key,
            "license_secret": license_secret,
        }
    finally:
        conn.close()


@app.post("/api/pushhub/authorize")
async def pushhub_authorize(payload: PushHubConfirmRequest) -> dict[str, Any]:
    license_key = str(payload.license_key or payload.license or "").strip()
    license_secret = str(payload.license_secret or payload.secret or "").strip()
    server_public_url = str(payload.server_public_url or "").strip()
    result = _confirm_license_with_central(
        license_key=license_key,
        license_secret=license_secret,
        server_public_url=server_public_url,
    )
    return {
        **result,
        "license": license_key,
    }


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["x-request-id"] = req_id
    return response


@app.get("/_new/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SETTINGS.service_name,
        "version": SETTINGS.service_version,
        "uptime_seconds": _uptime_seconds(),
    }


@app.get("/_new/ready")
async def ready() -> JSONResponse:
    checks = {
        "legacy_fallback_enabled": SETTINGS.legacy_fallback_enabled,
        "pg_pool": "ready" if STATE.pg_pool is not None or not SETTINGS.pg_dsn else "not_ready",
        "redis": "ready" if STATE.redis_client is not None or not SETTINGS.redis_url else "not_ready",
    }

    ready_ok = all(v == "ready" for k, v in checks.items() if k in ("pg_pool", "redis"))
    http_status = 200 if ready_ok else 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if ready_ok else "degraded",
            "checks": checks,
            "pool_errors": STATE.pool_errors,
            "uptime_seconds": _uptime_seconds(),
        },
    )


@app.get("/_new/config")
async def config_summary() -> dict[str, Any]:
    return {
        "service_name": SETTINGS.service_name,
        "service_version": SETTINGS.service_version,
        "legacy_fallback_enabled": SETTINGS.legacy_fallback_enabled,
        "pg_enabled": bool(SETTINGS.pg_dsn),
        "pg_pool_min": SETTINGS.pg_pool_min,
        "pg_pool_max": SETTINGS.pg_pool_max,
        "redis_enabled": bool(SETTINGS.redis_url),
        "strict_pool_startup": SETTINGS.strict_pool_startup,
    }


@app.get("/_new/migration/status")
async def migration_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "strategy": "legacy_fallback",
        "notes": [
            "Existing endpoints and websocket behavior are currently served by legacy main.py.",
            "This guarantees account/session continuity with no forced re-registration.",
            "Use canary rollout by switching uvicorn target to new_main:app.",
        ],
        "next_steps": [
            "Introduce PostgreSQL dual-write for users/sessions/offline_messages.",
            "Move websocket session/presence to Redis-backed coordination.",
            "Cut read paths to PostgreSQL after data parity validation.",
        ],
    }


# ---------------------------
# Legacy fallback delegation
# ---------------------------
# Keep this mounted LAST so /_new/* routes above still work.
if SETTINGS.legacy_fallback_enabled:
    from chat_backend.legacy_app import app as legacy_app  # noqa: WPS433

    app.mount("/", legacy_app)
else:

    @app.get("/")
    async def root_without_legacy() -> dict[str, str]:
        return {
            "status": "ok",
            "msg": "new_main is running without legacy fallback."
        }
