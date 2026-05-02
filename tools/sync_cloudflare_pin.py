import argparse
import base64
import hashlib
import importlib
import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Optional
from urllib import error, request


def to_colon_hex(data: bytes) -> str:
    return ':'.join(f'{b:02X}' for b in data)


def fetch_tls_fingerprint(domain: str, port: int = 443, timeout: int = 8) -> str:
    context = ssl.create_default_context()
    with socket.create_connection((domain, port), timeout=timeout) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=domain) as tls_sock:
            cert_der = tls_sock.getpeercert(binary_form=True)
            if not cert_der:
                raise RuntimeError('No peer certificate returned by server')
            digest = hashlib.sha256(cert_der).digest()
            return to_colon_hex(digest)


def build_basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f'{user}:{password}'.encode('utf-8')).decode('ascii')
    return f'Basic {token}'


def maybe_make_totp(secret: Optional[str]) -> Optional[str]:
    if not secret:
        return None
    try:
        pyotp = importlib.import_module('pyotp')  # optional dependency
    except Exception as exc:
        raise RuntimeError('pyotp is required to generate TOTP from secret. Install with: pip install pyotp') from exc
    return pyotp.TOTP(secret).now()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')


def post_rotation_pin(admin_base: str, domain: str, fingerprint: str, auth_header: str, totp_code: Optional[str]) -> dict:
    url = admin_base.rstrip('/') + '/admin/add_rotation_pin'
    payload = json.dumps({'domain': domain, 'fingerprint': fingerprint}).encode('utf-8')

    req = request.Request(url=url, method='POST', data=payload)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', auth_header)
    if totp_code:
        req.add_header('x-admin-totp', totp_code)

    with request.urlopen(req, timeout=12) as resp:
        body = resp.read().decode('utf-8', errors='replace')
        return json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Sync current TLS edge certificate fingerprint into /admin/add_rotation_pin when it changes.'
    )
    parser.add_argument('--domain', required=True, help='Public domain to inspect (e.g. chat.example.com)')
    parser.add_argument('--port', type=int, default=443, help='TLS port for certificate fetch (default: 443)')
    parser.add_argument('--admin-base', help='Admin API base URL (default: https://<domain>)')
    parser.add_argument('--admin-user', default=os.getenv('ADMIN_DOC_USER', 'admin'), help='Admin username')
    parser.add_argument('--admin-pass', default=os.getenv('ADMIN_DOC_PASS'), help='Admin password for this request only; do not persist it in GUI config')
    parser.add_argument('--admin-totp', default=os.getenv('ADMIN_TOTP_CODE'), help='Current TOTP code (optional)')
    parser.add_argument('--admin-totp-secret', default=os.getenv('ADMIN_TOTP_SECRET'), help='TOTP secret to auto-generate current code (optional)')
    parser.add_argument('--state-file', default=str(Path(__file__).resolve().parent / '.pin_sync_state.json'), help='Path to local sync state file')
    parser.add_argument('--force', action='store_true', help='Force POST even if fingerprint did not change')
    parser.add_argument('--print-only', action='store_true', help='Only print current fingerprint; do not call admin endpoint')

    args = parser.parse_args()

    domain = args.domain.strip()
    admin_base = (args.admin_base or f'https://{domain}').strip()
    state_file = Path(args.state_file)

    try:
        current_fp = fetch_tls_fingerprint(domain=domain, port=args.port)
    except Exception as exc:
        print(f'[ERROR] Failed to fetch TLS fingerprint for {domain}: {exc}')
        return 1

    print(f'[INFO] Domain: {domain}')
    print(f'[INFO] Current fingerprint: {current_fp}')

    if args.print_only:
        return 0

    state = load_state(state_file)
    previous_fp = state.get(domain)

    if previous_fp == current_fp and not args.force:
        print('[INFO] Fingerprint unchanged; no sync needed.')
        return 0

    if not args.admin_pass:
        print('[ERROR] Missing admin password. Pass --admin-pass interactively or provide ADMIN_DOC_PASS only for this process.')
        return 1

    totp_code = args.admin_totp
    if not totp_code and args.admin_totp_secret:
        try:
            totp_code = maybe_make_totp(args.admin_totp_secret)
        except Exception as exc:
            print(f'[ERROR] Could not generate TOTP code: {exc}')
            return 1

    auth_header = build_basic_auth(args.admin_user, args.admin_pass)

    try:
        result = post_rotation_pin(
            admin_base=admin_base,
            domain=domain,
            fingerprint=current_fp,
            auth_header=auth_header,
            totp_code=totp_code,
        )
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        print(f'[ERROR] Admin endpoint returned HTTP {exc.code}: {detail}')
        return 1
    except Exception as exc:
        print(f'[ERROR] Failed to call admin endpoint: {exc}')
        return 1

    if result.get('status') != 'ok':
        print(f"[ERROR] Server rejected update: {result}")
        return 1

    state[domain] = current_fp
    state[f'{domain}__synced_at'] = int(time.time())
    save_state(state_file, state)

    print('[OK] Rotation pin synced successfully.')
    if previous_fp:
        print(f'[INFO] Previous: {previous_fp}')
    print(f'[INFO] Synced:   {current_fp}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
