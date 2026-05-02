"""
Lightweight rolling-window latency tracker for message delivery path.

Design notes:
- Samples are stored as (unix_timestamp, latency_ms) tuples in a fixed-size deque.
- `maxlen=_MAX_SAMPLES` gives an O(1) hard-cap: once full the oldest entry is dropped.
- Percentiles are computed at query time from a snapshot so the hot record() path
  does only one lock acquire + one append, keeping WS dispatch impact minimal.
- Thread-safe for use from both async tasks and background threads.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Hard cap: keep at most this many samples to prevent unbounded memory growth.
# At 1000 msgs/s this covers ~10 s worth of samples, which is enough for percentiles.
# For a 300-second window simply increase this if you need longer history.
_MAX_SAMPLES = 50_000

_lock = threading.Lock()
# Each entry: (unix_timestamp_float, latency_ms_float)
_samples: deque[tuple[float, float]] = deque(maxlen=_MAX_SAMPLES)
_last_recorded_at: float = 0.0


def record(latency_ms: float) -> None:
    """Record one observed delivery latency sample (thread-safe, non-blocking)."""
    global _last_recorded_at
    now = time.time()
    with _lock:
        _samples.append((now, latency_ms))
        _last_recorded_at = now


def get_summary(window_seconds: int = 300) -> dict[str, Any]:
    """
    Return latency statistics for samples within the last *window_seconds*.

    If window_seconds <= 0 all accumulated samples are included.
    Percentiles use nearest-rank method on a snapshot to avoid holding the lock
    during the sort.
    """
    now = time.time()
    cutoff = (now - window_seconds) if window_seconds > 0 else 0.0

    with _lock:
        snapshot = list(_samples)
        last_at = _last_recorded_at

    # Filter to the requested time window
    recent = [ms for ts, ms in snapshot if ts >= cutoff]

    last_updated = (
        datetime.fromtimestamp(last_at, tz=timezone.utc).isoformat()
        if last_at
        else None
    )

    if not recent:
        return {
            "window_seconds": window_seconds,
            "samples": 0,
            "latency_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0},
            "last_updated": last_updated,
        }

    recent_sorted = sorted(recent)
    n = len(recent_sorted)

    def _pct(p: float) -> float:
        # Nearest-rank percentile (0-indexed).
        idx = max(0, min(int(p / 100.0 * n + 0.5) - 1, n - 1))
        return round(recent_sorted[idx], 3)

    return {
        "window_seconds": window_seconds,
        "samples": n,
        "latency_ms": {
            "p50": _pct(50),
            "p95": _pct(95),
            "p99": _pct(99),
            "max": round(recent_sorted[-1], 3),
        },
        "last_updated": last_updated,
    }
