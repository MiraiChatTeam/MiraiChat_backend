import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import pyotp
except Exception:
    print("ERROR: pyotp is not installed. Install with: pip install pyotp")
    raise SystemExit(1)


def _prompt_text(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _prompt_yes_no(prompt: str) -> bool:
    answer = _prompt_text(prompt).lower()
    return answer in {"y", "yes"}


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


def _maybe_save_png_qr(data: str, default_path: Path) -> None:
    if not _prompt_yes_no("Save PNG QR file for phone scan? [y/N]: "):
        return

    try:
        import qrcode
    except Exception:
        print("[WARN] qrcode package is not installed, cannot save PNG.")
        print("[INFO] Install with: pip install qrcode[pil]")
        return

    path_input = _prompt_text(f"PNG output path [{default_path}]: ")
    output_path = Path(path_input or str(default_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        image = qrcode.make(data)
        image.save(output_path)
        print(f"[OK] QR PNG saved to: {output_path}")
    except Exception as exc:
        print(f"[WARN] Could not save PNG QR: {exc}")


def _bootstrap_or_verify(secret_file: Path, account: str, issuer: str) -> Optional[str]:
    secret = _load_secret(secret_file)
    if secret:
        print("=== Admin TOTP Verification Required ===")
        code = _prompt_text("Enter current TOTP code to start backend: ")
        if not _validate_code(secret, code):
            print("[ERROR] Invalid TOTP code. Startup blocked.")
            return None
        print("[OK] TOTP verified.")
        return secret

    print("=== Admin TOTP Bootstrap ===")
    has_auth_app = _prompt_yes_no("Have you already set an authenticator app? [y/N]: ")

    if has_auth_app:
        existing_secret = _prompt_text("Enter your existing TOTP secret: ").strip()
        if not existing_secret:
            print("[ERROR] Missing secret. Startup blocked.")
            return None
        code = _prompt_text("Enter current TOTP code: ")
        if not _validate_code(existing_secret, code):
            print("[ERROR] Invalid code for provided secret. Startup blocked.")
            return None
        _save_secret(secret_file, existing_secret)
        print(f"[OK] Existing authenticator verified. Secret saved to {secret_file}")
        return existing_secret

    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)
    default_png = secret_file.parent / "admin_totp_qr.png"

    print("=== First-time Admin TOTP Setup ===")
    print(f"SECRET={secret}")
    print(f"PROVISIONING_URI={uri}")
    _print_ascii_qr(uri)
    _maybe_save_png_qr(uri, default_png)
    print("Add this to your authenticator app, then enter the current 6-digit code.")

    code = _prompt_text("Enter TOTP code to finish setup: ")
    if not _validate_code(secret, code):
        print("[ERROR] Invalid code. Setup not completed; startup blocked.")
        return None

    _save_secret(secret_file, secret)
    print(f"[OK] TOTP setup completed. Secret saved to {secret_file}")
    return secret


def _start_backend(project_dir: Path, host: str, port: int, reload_enabled: bool) -> int:
    main_py = project_dir / "new_main.py"
    if not main_py.exists():
        print(f"[ERROR] new_main.py not found in {project_dir}")
        return 1

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "new_main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload_enabled:
        command.append("--reload")

    print(f"[INFO] Starting backend in {project_dir}")
    print("[INFO] Command:", " ".join(command))

    try:
        result = subprocess.run(command, cwd=str(project_dir), check=False)
        return int(result.returncode)
    except KeyboardInterrupt:
        print("\n[INFO] Backend stopped by user.")
        return 0


def main() -> int:
    default_project_dir = Path(__file__).resolve().parent.parent
    default_secret_file = default_project_dir / ".admin_totp_secret_windows"

    parser = argparse.ArgumentParser(
        description="Windows interactive TOTP gate + backend starter for chat_app.",
    )
    parser.add_argument("--project-dir", default=str(default_project_dir), help="Path to chat_app project root")
    parser.add_argument("--secret-file", default=str(default_secret_file), help="Path to stored admin TOTP secret")
    parser.add_argument("--account", default="admin@chatapp-windows", help="Authenticator account label")
    parser.add_argument("--issuer", default="ChatApp Admin", help="Authenticator issuer label")
    parser.add_argument("--host", default="0.0.0.0", help="Uvicorn host")
    parser.add_argument("--port", type=int, default=8000, help="Uvicorn port")
    parser.add_argument("--no-reload", action="store_true", help="Disable uvicorn --reload")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    secret_file = Path(args.secret_file).resolve()

    secret = _bootstrap_or_verify(secret_file=secret_file, account=args.account, issuer=args.issuer)
    if not secret:
        return 1

    return _start_backend(
        project_dir=project_dir,
        host=args.host,
        port=args.port,
        reload_enabled=not args.no_reload,
    )


if __name__ == "__main__":
    raise SystemExit(main())
