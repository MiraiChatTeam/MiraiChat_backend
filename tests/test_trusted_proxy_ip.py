"""Tests for TRUSTED_PROXY_MODE client-IP resolution (get_real_ip).

These verify that client-supplied forwarding headers cannot be used to spoof
the IP used for rate limiting and the admin IP allowlist, and that each mode
resolves the address from the trusted source.

Backend runtime deps (fastapi/bcrypt/pyotp) are required to import
chat_backend.auth; the test is skipped when they are unavailable.
"""

import unittest
from unittest import mock

try:
    from chat_backend import auth as auth_module
except Exception as exc:  # pragma: no cover - exercised only without deps
    auth_module = None
    AUTH_IMPORT_ERROR = exc
else:
    AUTH_IMPORT_ERROR = None


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimal stand-in for starlette.Request for get_real_ip()."""

    def __init__(self, headers=None, peer="198.51.100.7"):
        # starlette Headers are case-insensitive; emulate that.
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = _FakeClient(peer)

    @property
    def headers(self):
        store = self._headers

        class _H:
            def get(self, name, default=None):
                return store.get(name.lower(), default)

        return _H()


@unittest.skipIf(
    AUTH_IMPORT_ERROR is not None,
    f"backend runtime dependencies unavailable: {AUTH_IMPORT_ERROR}",
)
class TrustedProxyIpResolutionTests(unittest.TestCase):
    # An attacker forges X-Real-IP and the first X-Forwarded-For entry while the
    # trusted edge (Cloudflare) sets CF-Connecting-IP / appends the real address.
    PEER = "198.51.100.7"          # socket peer (e.g. CF edge)
    REAL = "203.0.113.99"          # real client, set by the trusted edge
    SPOOF = "1.2.3.4"              # attacker-controlled value

    def _attacker_request(self):
        return _FakeRequest(
            headers={
                "X-Real-IP": self.SPOOF,
                "X-Forwarded-For": f"{self.SPOOF}, {self.REAL}",
                "CF-Connecting-IP": self.REAL,
            },
            peer=self.PEER,
        )

    def test_direct_mode_ignores_all_forwarding_headers(self):
        with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", "direct"):
            ip = auth_module.get_real_ip(self._attacker_request())
        self.assertEqual(ip, self.PEER)
        self.assertNotEqual(ip, self.SPOOF)

    def test_cloudflare_mode_uses_cf_connecting_ip(self):
        with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", "cloudflare"):
            ip = auth_module.get_real_ip(self._attacker_request())
        self.assertEqual(ip, self.REAL)
        self.assertNotEqual(ip, self.SPOOF)

    def test_cloudflare_mode_falls_back_to_peer_without_cf_header(self):
        req = _FakeRequest(headers={"X-Real-IP": self.SPOOF}, peer=self.PEER)
        with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", "cloudflare"):
            ip = auth_module.get_real_ip(req)
        self.assertEqual(ip, self.PEER)
        self.assertNotEqual(ip, self.SPOOF)

    def test_xforwarded_mode_trusts_last_hop_not_first(self):
        with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", "xforwarded"):
            ip = auth_module.get_real_ip(self._attacker_request())
        # The last hop is the address appended by our own proxy, not the
        # client-controlled first entry.
        self.assertEqual(ip, self.REAL)
        self.assertNotEqual(ip, self.SPOOF)

    def test_no_mode_ever_returns_the_spoofed_value(self):
        for mode in ("direct", "cloudflare", "xforwarded"):
            with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", mode):
                ip = auth_module.get_real_ip(self._attacker_request())
            self.assertNotEqual(ip, self.SPOOF, f"mode={mode} returned spoofed IP")

    def test_peer_ip_handles_missing_client(self):
        req = _FakeRequest(peer=None)
        with mock.patch.object(auth_module, "TRUSTED_PROXY_MODE", "direct"):
            ip = auth_module.get_real_ip(req)
        self.assertEqual(ip, "unknown")


if __name__ == "__main__":
    unittest.main()
