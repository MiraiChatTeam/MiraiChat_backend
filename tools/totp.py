import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

EXIT_AUTH_REQUIRED = 77

try:
    import pyotp
except Exception:
    print("ERROR: pyotp is not installed. Install with: pip install pyotp")
    raise SystemExit(1)


def _print_ascii_qr(data: str) -> bool:
    try:
        import qrcode
    except Exception:
        print("[WARN] qrcode package is not installed, skipping ASCII QR output.")
        print("[INFO] Install with: pip install qrcode[pil]")
        return False

    try:
        qr = qrcode.QRCode(border=2)
        qr.add_data(data)
        qr.make(fit=True)
        print("\n=== Scan This ASCII QR ===")
        qr.print_ascii(invert=True)
        print("=== End ASCII QR ===\n")
        return True
    except Exception as exc:
        print(f"[WARN] Failed to render ASCII QR: {exc}")
        return False


def _maybe_save_png_qr(data: str, default_path: str) -> None:
    if not _prompt_yes_no("Save PNG QR file for phone scan? [y/N]: "):
        return

    try:
        import qrcode
    except Exception:
        print("[WARN] qrcode package is not installed, cannot save PNG.")
        print("[INFO] Install with: pip install qrcode[pil]")
        return

    path_input = _prompt_code(f"PNG output path [{default_path}]: ")
    output_path = Path(path_input or default_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        image = qrcode.make(data)
        image.save(output_path)
        print(f"[OK] QR PNG saved to: {output_path}")
    except Exception as exc:
        print(f"[WARN] Could not save PNG QR: {exc}")


def _prompt_code(prompt: str) -> str:
    env_code = os.getenv("ADMIN_TOTP_RESTART_CODE", "").strip()
    if env_code:
        return env_code

    if not sys.stdin.isatty():
        print("[ERROR] Non-interactive restart requires ADMIN_TOTP_RESTART_CODE.")
        return ""

    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _prompt_yes_no(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _get_approval_file() -> Path:
    return Path(os.getenv("ADMIN_TOTP_APPROVAL_FILE", "/home/.admin_totp_restart_ok"))


def _get_approval_ttl() -> int:
    raw = os.getenv("ADMIN_TOTP_APPROVAL_TTL", "120").strip()
    try:
        value = int(raw)
    except Exception:
        value = 120
    if value < 1:
        return 120
    return value


def _grant_restart_approval() -> None:
    approval_file = _get_approval_file()
    approval_file.parent.mkdir(parents=True, exist_ok=True)
    approval_file.write_text(str(int(time.time())), encoding="utf-8")
    try:
        os.chmod(approval_file, 0o600)
    except Exception:
        pass
    print(f"[OK] One-time restart approval granted for {_get_approval_ttl()}s.")


def _consume_restart_approval() -> bool:
    approval_file = _get_approval_file()
    if not approval_file.exists():
        return False

    try:
        issued_at = int(approval_file.read_text(encoding="utf-8").strip())
    except Exception:
        try:
            approval_file.unlink()
        except Exception:
            pass
        return False

    now = int(time.time())
    ttl = _get_approval_ttl()
    valid = (now - issued_at) <= ttl

    try:
        approval_file.unlink()
    except Exception:
        pass

    return valid


def _validate_code(secret: str, code: str) -> bool:
    if not code:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code, valid_window=1))


def _load_secret(secret_file: Path) -> Optional[str]:
    env_secret = os.getenv("ADMIN_TOTP_SECRET", "").strip()
    if env_secret:
        return env_secret
    if not secret_file.exists():
        return None
    secret = secret_file.read_text(encoding="utf-8").strip()
    return secret or None


def _save_secret(secret_file: Path, secret: str) -> None:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret + "\n", encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except Exception:
        pass


def _run_auto_pin_sync() -> None:
    auto_run = os.getenv("PIN_SYNC_AUTO_RUN", "true").strip().lower()
    if auto_run not in {"1", "true", "yes", "on"}:
        print("[INFO] PIN_SYNC_AUTO_RUN disabled; skipping pin sync.")
        return

    script_path = Path(os.getenv("PIN_SYNC_SCRIPT", "/home/tools/sync_cloudflare_pin.py"))
    domain = os.getenv("PIN_SYNC_DOMAIN", "").strip()
    admin_base = os.getenv("PIN_SYNC_ADMIN_BASE", "").strip()
    admin_user = os.getenv("PIN_SYNC_ADMIN_USER", os.getenv("ADMIN_DOC_USER", "admin")).strip()
    admin_pass = os.getenv("PIN_SYNC_ADMIN_PASS", os.getenv("ADMIN_DOC_PASS", "")).strip()
    totp_secret = os.getenv("PIN_SYNC_TOTP_SECRET", os.getenv("ADMIN_TOTP_SECRET", "")).strip()
    state_file = os.getenv("PIN_SYNC_STATE_FILE", "/home/tools/.pin_sync_state.json").strip()

    if not script_path.exists():
        print(f"[WARN] Pin sync script not found at {script_path}; skipping pin sync.")
        return
    if not domain:
        print("[WARN] PIN_SYNC_DOMAIN is not set; skipping pin sync.")
        return
    if not admin_pass:
        print("[WARN] Runtime admin password is not set for pin sync; skipping pin sync.")
        return

    cmd = [
        sys.executable,
        str(script_path),
        "--domain",
        domain,
        "--admin-user",
        admin_user,
        "--admin-pass",
        admin_pass,
        "--state-file",
        state_file,
    ]
    if admin_base:
        cmd.extend(["--admin-base", admin_base])
    if totp_secret:
        cmd.extend(["--admin-totp-secret", totp_secret])

    print("[INFO] Running automatic pin sync before backend startup...")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("[WARN] Pin sync failed. Backend startup will continue.")
    else:
        print("[OK] Pin sync completed.")


def _is_port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _start_legacy_backend_background() -> None:
    legacy_host = os.getenv("LEGACY_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    legacy_port_raw = os.getenv("LEGACY_BACKEND_PORT", "8001").strip()
    try:
        legacy_port = int(legacy_port_raw)
    except Exception:
        legacy_port = 8001

    if _is_port_in_use(legacy_host, legacy_port):
        print(f"[WARN] Legacy backend already appears to be running on {legacy_host}:{legacy_port}; skipping launch.")
        return

    project_dir = Path(os.getenv("BACKEND_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
    uvicorn_bin = os.getenv("LEGACY_UVICORN_BIN", "/usr/local/bin/uvicorn").strip() or "/usr/local/bin/uvicorn"

    cmd = [
        uvicorn_bin,
        "chat_backend.legacy_app:app",
        "--host",
        legacy_host,
        "--port",
        str(legacy_port),
        "--ws",
        "wsproto",
    ]

    try:
        subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("Legacy backend started on port 8001")
    except Exception as exc:
        print(f"[WARN] Failed to start legacy backend on {legacy_host}:{legacy_port}: {exc}")


def _start_backend() -> int:
    uvicorn_bin = os.getenv("BACKEND_UVICORN_BIN", "/usr/local/bin/uvicorn").strip()
    app_target = os.getenv("BACKEND_APP", "main:app").strip()
    host = os.getenv("BACKEND_HOST", "127.0.0.1").strip()
    port = os.getenv("BACKEND_PORT", "8000").strip()

    print(f"[INFO] Starting backend: {uvicorn_bin} {app_target} --host {host} --port {port}")
    os.execv(uvicorn_bin, [uvicorn_bin, app_target, "--host", host, "--port", port, "--ws", "wsproto"])
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TOTP restart gate: setup on first run, verify on restart, then auto-sync pins and start backend.",
    )
    parser.add_argument(
        "--secret-file",
        default=os.getenv("ADMIN_TOTP_SECRET_FILE", "/home/.admin_totp_secret"),
        help="Path to persisted admin TOTP secret (used if ADMIN_TOTP_SECRET is not set)",
    )
    parser.add_argument(
        "--account",
        default=os.getenv("ADMIN_TOTP_ACCOUNT", "admin@chatapp-server"),
        help="Authenticator account label",
    )
    parser.add_argument(
        "--issuer",
        default=os.getenv("ADMIN_TOTP_ISSUER", "ChatApp Admin"),
        help="Authenticator issuer label",
    )
    parser.add_argument(
        "--start-backend",
        action="store_true",
        help="Run pin sync and start backend after successful TOTP setup/verification",
    )
    args = parser.parse_args()

    secret_file = Path(args.secret_file)
    secret = _load_secret(secret_file)
    is_interactive = bool(sys.stdin.isatty())

    if not secret:
        if not is_interactive:
            print("[ERROR] Admin TOTP is not initialized yet.")
            print("Run interactive setup first:")
            print("  /usr/bin/python3 /home/tools/totp.py")
            print("Then restart backend.service.")
            return EXIT_AUTH_REQUIRED

        print("=== Admin TOTP Bootstrap ===")
        has_auth_app = _prompt_yes_no("Have you already set an authenticator app? [y/N]: ")

        if has_auth_app:
            existing_secret = _prompt_code("Enter your existing TOTP secret: ").strip()
            if not existing_secret:
                print("[ERROR] Missing secret. Bootstrap failed.")
                return 1
            code = _prompt_code("Enter current TOTP code: ")
            if not _validate_code(existing_secret, code):
                print("[ERROR] Invalid code for provided secret. Bootstrap failed.")
                return 1
            _save_secret(secret_file, existing_secret)
            secret = existing_secret
            print(f"[OK] Existing authenticator verified. Secret saved to {secret_file}")
        else:
            secret = pyotp.random_base32()
            uri = pyotp.TOTP(secret).provisioning_uri(name=args.account, issuer_name=args.issuer)

            print("=== First-time Admin TOTP Setup ===")
            print(f"SECRET={secret}")
            print(f"PROVISIONING_URI={uri}")
            _print_ascii_qr(uri)
            _maybe_save_png_qr(uri, "/home/admin_totp_qr.png")
            print("Add this to your authenticator app, then enter the current 6-digit code.")

            code = _prompt_code("Enter TOTP code to finish setup: ")
            if not _validate_code(secret, code):
                print("[ERROR] Invalid code. Setup not completed; backend start blocked.")
                return 1

            _save_secret(secret_file, secret)
            print(f"[OK] TOTP setup completed. Secret saved to {secret_file}")
    else:
        print("=== Admin TOTP Verification Required ===")
        if args.start_backend and not is_interactive:
            env_code = os.getenv("ADMIN_TOTP_RESTART_CODE", "").strip()
            if env_code:
                if not _validate_code(secret, env_code):
                    print("[ERROR] Invalid ADMIN_TOTP_RESTART_CODE. Backend restart blocked.")
                    return EXIT_AUTH_REQUIRED
                print("[OK] TOTP verified via ADMIN_TOTP_RESTART_CODE.")
            elif _consume_restart_approval():
                print("[OK] Consumed one-time restart approval token.")
            else:
                print("[ERROR] Non-interactive restart requires ADMIN_TOTP_RESTART_CODE")
                print("        or an active one-time approval from: /usr/bin/python3 /home/tools/totp.py")
                print("[ERROR] Backend restart blocked.")
                return EXIT_AUTH_REQUIRED
        else:
            code = _prompt_code("Enter current TOTP code to restart backend: ")
            if not _validate_code(secret, code):
                print("[ERROR] Invalid code. Backend restart blocked.")
                return 1
            print("[OK] TOTP verified.")

        if not args.start_backend:
            _grant_restart_approval()

    if args.start_backend:
        _run_auto_pin_sync()
        _start_legacy_backend_background()
        return _start_backend()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
