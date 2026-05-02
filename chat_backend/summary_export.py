"""
Plain-text summary renderer for canary/load-test review snapshots.

Takes the data dicts produced by latency_tracker.get_summary() and the fanout
delivery rate helper, and renders a human-readable multi-line string suitable
for quick shell-level inspection without needing to parse JSON.
"""

from __future__ import annotations

from typing import Any


def render_text(load_summary: dict[str, Any], fanout_rate: dict[str, Any]) -> str:
    """
    Render a plain-text snapshot of load-test latency and fanout delivery metrics.

    Arguments:
        load_summary: output of latency_tracker.get_summary()
        fanout_rate:  output of _get_fanout_delivery_data() in legacy_app.py

    Returns:
        Multi-line string ready to be served as text/plain.
    """
    lat = load_summary.get("latency_ms", {})
    lw = load_summary.get("window_seconds", 0)
    fw = fanout_rate.get("window_seconds", 0)
    fw_note = fanout_rate.get("window_note", "")
    dr = fanout_rate.get("delivery_rate", {})

    def _fmt(v: object, suffix: str = "") -> str:
        try:
            return f"{float(v):.3f}{suffix}"
        except (TypeError, ValueError):
            return "n/a"

    lines = [
        f"Load Test Summary (last {lw}s)",
        "-" * 31,
        f"Samples       : {load_summary.get('samples', 0)}",
        f"p50 latency   : {_fmt(lat.get('p50', 0.0), ' ms')}",
        f"p95 latency   : {_fmt(lat.get('p95', 0.0), ' ms')}",
        f"p99 latency   : {_fmt(lat.get('p99', 0.0), ' ms')}",
        f"Max latency   : {_fmt(lat.get('max', 0.0), ' ms')}",
        f"Last updated  : {load_summary.get('last_updated') or 'n/a'}",
        "",
        f"Fanout Delivery ({fw_note or f'last {fw}s'})",
        "-" * 31,
        f"Fanout active : {fanout_rate.get('fanout_active', False)}",
        f"Published     : {fanout_rate.get('published', 0)}",
        f"Received      : {fanout_rate.get('received', 0)}",
        f"Delivered     : {fanout_rate.get('delivered', 0)}",
        f"Dropped       : {fanout_rate.get('dropped', 0)}",
        f"Errors        : {fanout_rate.get('errors', 0)}",
        f"Del/Published : {_fmt(dr.get('delivered_over_published', 0.0))}",
        f"Del/Received  : {_fmt(dr.get('delivered_over_received', 0.0))}",
        f"Last updated  : {fanout_rate.get('last_updated') or 'n/a'}",
    ]
    return "\n".join(lines)
