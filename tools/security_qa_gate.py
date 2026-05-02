from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def check_contains(name: str, content: str, patterns: list[str]) -> list[str]:
    failures: list[str] = []
    for pattern in patterns:
        if re.search(pattern, content, flags=re.MULTILINE) is None:
            failures.append(f"{name}: missing pattern -> {pattern}")
    return failures


def main() -> int:
    failures: list[str] = []

    main_py = read("main.py")
    ws_mgr = read("lib/websocket_connection_manager.dart")
    out_queue = read("lib/outgoing_message_queue.dart")
    sec_mode = read("lib/security_mode_manager.dart")
    cert_mgr = read("lib/certificate_pinning_manager.dart")
    chat_list = read("lib/chat_list_page.dart")

    failures += check_contains(
        "transport",
        main_py,
        [
            r"@app\.post\(\"/chat/ws_ticket\"\)",
            r"type\"\) != \"ws_auth\"",
            r"ws_tickets",
            r"ws_auth_ok",
            r"_reject_ws_auth",
        ],
    )

    failures += check_contains(
        "high-risk-fail-closed",
        out_queue,
        [
            r"mustUseSealedSender",
            r"_isHighRiskSealedRequiredType",
            r"High-Risk mode blocked unsealed",
        ],
    )

    failures += check_contains(
        "metadata-controls",
        ws_mgr,
        [
            r"_allowReconnectAttempt",
            r"_allowPendingFetch",
            r"nextReconnectAllowedAt|_nextReconnectAllowedAt",
            r"nextPendingFetchAllowedAt|_nextPendingFetchAllowedAt",
        ],
    )

    failures += check_contains(
        "security-mode-policy",
        sec_mode,
        [
            r"getBatchWindowMs",
            r"getJitterMinMs",
            r"getJitterMaxMs",
        ],
    )

    failures += check_contains(
        "cert-audit-trail",
        cert_mgr,
        [
            r"recordTrustEvent",
            r"getTrustAuditTrail",
            r"setRotationPlannedAt",
            r"getRotationStatus",
        ],
    )

    failures += check_contains(
        "privacy-ux",
        chat_list,
        [
            r"server_trust_audit",
            r"cert_rotation_readiness",
            r"plan_plus_30d",
        ],
    )

    if failures:
        print("❌ Security QA Gate: FAILED")
        for item in failures:
            print(f" - {item}")
        return 1

    print("✅ Security QA Gate: PASSED")
    print("All required hardening markers were found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
