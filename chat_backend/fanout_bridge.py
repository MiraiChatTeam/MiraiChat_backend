from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from chat_backend.settings import FANOUT_BACKEND, FANOUT_CHANNEL, FANOUT_NODE_ID, FANOUT_REDIS_URL


DeliverCallback = Callable[[str, str, str], Awaitable[bool]]


@dataclass
class FanoutStats:
    backend: str = "memory"
    enabled: bool = False
    published: int = 0
    received: int = 0
    delivered: int = 0
    dropped: int = 0
    errors: int = 0
    last_error: str = ""
    started_at: float = 0.0


class FanoutBridge:
    def __init__(self) -> None:
        self._enabled = False
        self._backend = FANOUT_BACKEND
        self._redis_url = FANOUT_REDIS_URL
        self._channel = FANOUT_CHANNEL
        self._node_id = FANOUT_NODE_ID

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._deliver_callback: Optional[DeliverCallback] = None
        self._publisher: Any = None
        self._subscriber: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stats = FanoutStats()

    def configure(self) -> None:
        self._backend = FANOUT_BACKEND
        self._redis_url = FANOUT_REDIS_URL
        self._channel = FANOUT_CHANNEL
        self._node_id = FANOUT_NODE_ID

    def start(self, loop: asyncio.AbstractEventLoop, deliver_callback: DeliverCallback) -> None:
        self._loop = loop
        self._deliver_callback = deliver_callback
        self._stats.started_at = time.time()

        if self._backend != "redis" or not self._redis_url:
            self._enabled = False
            self._stats.backend = "memory"
            self._stats.enabled = False
            return

        try:
            import redis  # type: ignore

            self._publisher = redis.Redis.from_url(self._redis_url, decode_responses=True)
            self._publisher.ping()

            self._subscriber = redis.Redis.from_url(self._redis_url, decode_responses=True)
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._listen_loop, name="fanout-subscriber", daemon=True)
            self._thread.start()

            self._enabled = True
            self._stats.backend = "redis"
            self._stats.enabled = True
            self._stats.last_error = ""
            print(f"[INFO] Fanout backend configured: redis (channel={self._channel}, node={self._node_id})")
        except Exception as error:
            self._enabled = False
            self._stats.backend = "memory"
            self._stats.enabled = False
            self._stats.errors += 1
            self._stats.last_error = str(error)
            print(f"[WARN] Fanout backend redis unavailable, fallback to memory: {error}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        subscriber = self._subscriber
        self._subscriber = None
        if subscriber is not None:
            try:
                subscriber.close()
            except Exception:
                pass

        publisher = self._publisher
        self._publisher = None
        if publisher is not None:
            try:
                publisher.close()
            except Exception:
                pass

        self._enabled = False
        self._stats.enabled = False

    def publish(self, user_id: str, session_secret: str, payload: str) -> bool:
        if not self._enabled:
            return False

        pub = self._publisher
        if pub is None:
            return False

        try:
            body = {
                "node_id": self._node_id,
                "user_id": user_id,
                "session_secret": session_secret,
                "payload": payload,
            }
            pub.publish(self._channel, json.dumps(body, separators=(",", ":")))
            self._stats.published += 1
            return True
        except Exception as error:
            self._stats.errors += 1
            self._stats.last_error = str(error)
            return False

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self._stats.backend,
            "enabled": self._stats.enabled,
            "channel": self._channel,
            "node_id": self._node_id,
            "published": self._stats.published,
            "received": self._stats.received,
            "delivered": self._stats.delivered,
            "dropped": self._stats.dropped,
            "errors": self._stats.errors,
            "last_error": self._stats.last_error,
            "uptime_seconds": int(time.time() - self._stats.started_at) if self._stats.started_at else 0,
        }

    def _listen_loop(self) -> None:
        sub_client = self._subscriber
        if sub_client is None:
            return

        pubsub = sub_client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(self._channel)
        try:
            while not self._stop_event.is_set():
                message = pubsub.get_message(timeout=1.0)
                if not message:
                    continue

                data = message.get("data")
                if not isinstance(data, str):
                    continue

                try:
                    body = json.loads(data)
                    node_id = str(body.get("node_id") or "")
                    if node_id == self._node_id:
                        continue

                    user_id = str(body.get("user_id") or "")
                    session_secret = str(body.get("session_secret") or "")
                    payload = str(body.get("payload") or "")
                    if not user_id or not session_secret or not payload:
                        self._stats.dropped += 1
                        continue

                    self._stats.received += 1

                    loop = self._loop
                    callback = self._deliver_callback
                    if loop is None or callback is None:
                        self._stats.dropped += 1
                        continue

                    future = asyncio.run_coroutine_threadsafe(
                        callback(user_id, session_secret, payload),
                        loop,
                    )
                    delivered = bool(future.result(timeout=2.0))
                    if delivered:
                        self._stats.delivered += 1
                    else:
                        self._stats.dropped += 1
                except Exception as error:
                    self._stats.errors += 1
                    self._stats.last_error = str(error)
        finally:
            try:
                pubsub.close()
            except Exception:
                pass


fanout_bridge = FanoutBridge()