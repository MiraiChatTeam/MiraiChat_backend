import queue
import threading
from dataclasses import dataclass
from typing import Any, Optional

from chat_backend.push import send_fcm_push


@dataclass
class PushJob:
    user_id: str
    title: str
    body: str
    data_payload: Optional[dict[str, Any]] = None
    exclude_session_secrets: Optional[list[str]] = None


_PUSH_QUEUE_MAX_SIZE = 20000
_PUSH_QUEUE_WORKERS = 2
_push_queue: queue.Queue[PushJob] = queue.Queue(maxsize=_PUSH_QUEUE_MAX_SIZE)
_worker_threads: list[threading.Thread] = []
_stop_event = threading.Event()
_workers_started = False


def _worker_loop() -> None:
    while not _stop_event.is_set():
        try:
            job = _push_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            send_fcm_push(
                job.user_id,
                job.title,
                job.body,
                data_payload=job.data_payload,
                exclude_session_secrets=job.exclude_session_secrets,
            )
        except Exception as error:
            print(f"[WARN] Async push worker failed: {error}")
        finally:
            _push_queue.task_done()


def start_push_workers() -> None:
    global _workers_started
    if _workers_started:
        return

    _stop_event.clear()
    for index in range(_PUSH_QUEUE_WORKERS):
        thread = threading.Thread(target=_worker_loop, name=f"push-worker-{index}", daemon=True)
        thread.start()
        _worker_threads.append(thread)
    _workers_started = True


def stop_push_workers(timeout_seconds: float = 2.0) -> None:
    global _workers_started
    if not _workers_started:
        return

    _stop_event.set()
    for thread in list(_worker_threads):
        thread.join(timeout=timeout_seconds)
    _worker_threads.clear()
    _workers_started = False


def enqueue_push(
    user_id: str,
    title: str,
    body: str,
    *,
    data_payload: Optional[dict[str, Any]] = None,
    exclude_session_secrets: Optional[list[str]] = None,
) -> bool:
    if not _workers_started:
        send_fcm_push(
            user_id,
            title,
            body,
            data_payload=data_payload,
            exclude_session_secrets=exclude_session_secrets,
        )
        return True

    job = PushJob(
        user_id=user_id,
        title=title,
        body=body,
        data_payload=data_payload,
        exclude_session_secrets=exclude_session_secrets,
    )

    try:
        _push_queue.put_nowait(job)
        return True
    except queue.Full:
        print("[WARN] Push queue full; falling back to synchronous relay push")
        send_fcm_push(
            user_id,
            title,
            body,
            data_payload=data_payload,
            exclude_session_secrets=exclude_session_secrets,
        )
        return False


def push_queue_stats() -> dict[str, int | str | bool]:
    return {
        "enabled": True,
        "queue_size": _push_queue.qsize(),
        "max_size": _PUSH_QUEUE_MAX_SIZE,
        "workers": _PUSH_QUEUE_WORKERS,
        "reason": "Proxy push queue dispatches to central hub relay.",
    }