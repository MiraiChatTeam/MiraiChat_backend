from __future__ import annotations

from collections.abc import Iterator
from threading import Lock
from typing import Any, Optional


class _PresenceBackend:
    name = "memory"

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def mark_online(self, user_id: str, session_secret: str) -> None:
        return

    def mark_offline(self, user_id: str, session_secret: str) -> None:
        return

    def online_sessions(self, user_id: str) -> set[str]:
        return set()

    def status(self) -> dict[str, Any]:
        return {"backend": self.name}


class _MemoryPresenceBackend(_PresenceBackend):
    name = "memory"

    def __init__(self) -> None:
        self._presence: dict[str, set[str]] = {}

    def mark_online(self, user_id: str, session_secret: str) -> None:
        self._presence.setdefault(user_id, set()).add(session_secret)

    def mark_offline(self, user_id: str, session_secret: str) -> None:
        sessions = self._presence.get(user_id)
        if not sessions:
            return
        sessions.discard(session_secret)
        if not sessions:
            self._presence.pop(user_id, None)

    def online_sessions(self, user_id: str) -> set[str]:
        return set(self._presence.get(user_id, set()))

    def status(self) -> dict[str, Any]:
        active_users = len(self._presence)
        active_sessions = sum(len(v) for v in self._presence.values())
        return {
            "backend": self.name,
            "active_users": active_users,
            "active_sessions": active_sessions,
        }


class _RedisPresenceBackend(_PresenceBackend):
    name = "redis"

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._client: Optional[Any] = None
        self._error: str = ""

    def start(self) -> None:
        if self._client is not None:
            return
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(self._redis_url, decode_responses=True)
            client.ping()
            self._client = client
            self._error = ""
        except Exception as error:
            self._client = None
            self._error = str(error)
            raise

    def stop(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass

    def _key(self, user_id: str) -> str:
        return f"presence:user:{user_id}"

    def mark_online(self, user_id: str, session_secret: str) -> None:
        client = self._client
        if client is None:
            return
        key = self._key(user_id)
        client.sadd(key, session_secret)
        client.expire(key, self._ttl_seconds)

    def mark_offline(self, user_id: str, session_secret: str) -> None:
        client = self._client
        if client is None:
            return
        key = self._key(user_id)
        client.srem(key, session_secret)
        if client.scard(key) == 0:
            client.delete(key)

    def online_sessions(self, user_id: str) -> set[str]:
        client = self._client
        if client is None:
            return set()
        key = self._key(user_id)
        values = client.smembers(key)
        client.expire(key, self._ttl_seconds)
        return {str(v) for v in values}

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "connected": self._client is not None,
            "error": self._error,
            "ttl_seconds": self._ttl_seconds,
        }


class ConnectionRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._connections: dict[str, dict[str, Any]] = {}
        self._session_state: dict[str, dict[str, dict[str, Any]]] = {}
        self._presence_backend: _PresenceBackend = _MemoryPresenceBackend()

    def configure_backend(
        self,
        *,
        backend: str,
        redis_url: str = "",
        ttl_seconds: int = 180,
    ) -> None:
        selected = (backend or "memory").strip().lower()
        if selected == "redis" and redis_url:
            try:
                redis_backend = _RedisPresenceBackend(redis_url=redis_url, ttl_seconds=ttl_seconds)
                redis_backend.start()
                self._presence_backend = redis_backend
                print("[INFO] Presence backend configured: redis")
                return
            except Exception as error:
                print(f"[WARN] Presence backend redis unavailable, falling back to memory: {error}")

        self._presence_backend = _MemoryPresenceBackend()
        print("[INFO] Presence backend configured: memory")

    def stop(self) -> None:
        self._presence_backend.stop()

    def status(self) -> dict[str, Any]:
        with self._lock:
            local_users = len(self._connections)
            local_sessions = sum(len(v) for v in self._connections.values())
        return {
            "local_users": local_users,
            "local_sessions": local_sessions,
            **self._presence_backend.status(),
        }

    def get_user_sessions(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            return self._connections.get(user_id, {})

    def iter_user_sessions(self, user_id: str) -> Iterator[tuple[str, Any]]:
        with self._lock:
            sessions = self._connections.get(user_id, {})
            return iter(list(sessions.items()))

    def get_presence_sessions(self, user_id: str) -> set[str]:
        return self._presence_backend.online_sessions(user_id)

    def register(self, user_id: str, session_secret: str, websocket: Any) -> None:
        with self._lock:
            self._connections.setdefault(user_id, {})[session_secret] = websocket
            self._session_state.setdefault(user_id, {}).setdefault(session_secret, {})
        self._presence_backend.mark_online(user_id, session_secret)

    def set_session_state(self, user_id: str, session_secret: str, state: dict[str, Any]) -> None:
        with self._lock:
            user_state = self._session_state.setdefault(user_id, {})
            merged = dict(user_state.get(session_secret, {}))
            merged.update(state)
            user_state[session_secret] = merged

    def get_session_state(self, user_id: str, session_secret: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._session_state.get(user_id, {}).get(session_secret, {}))

    def has(self, user_id: str, session_secret: str) -> bool:
        with self._lock:
            return session_secret in self._connections.get(user_id, {})

    def get_socket(self, user_id: str, session_secret: str) -> Any:
        with self._lock:
            return self._connections.get(user_id, {}).get(session_secret)

    def remove(self, user_id: str, session_secret: str) -> None:
        with self._lock:
            sessions = self._connections.get(user_id)
            if sessions:
                sessions.pop(session_secret, None)
                if not sessions:
                    self._connections.pop(user_id, None)
            user_state = self._session_state.get(user_id)
            if user_state:
                user_state.pop(session_secret, None)
                if not user_state:
                    self._session_state.pop(user_id, None)
        self._presence_backend.mark_offline(user_id, session_secret)


connection_registry = ConnectionRegistry()