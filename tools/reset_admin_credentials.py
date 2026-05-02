import argparse
import getpass
import os
import shlex
import subprocess
import sys
from pathlib import Path

import bcrypt
import pyotp


def _prompt_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("[WARN] Value cannot be empty.")


def _read_password_interactive() -> str:
    while True:
        password = getpass.getpass("New admin password: ")
        if not password:
            print("[WARN] Password cannot be empty.")
            continue
        confirm = getpass.getpass("Confirm admin password: ")
        if password != confirm:
            print("[WARN] Password mismatch. Try again.")
            continue
        return password


def _load_totp_secret(secret_file: Path) -> str:
    env_secret = os.getenv("ADMIN_TOTP_SECRET", "").strip()
    if env_secret:
        return env_secret

    if secret_file.exists():
        file_secret = secret_file.read_text(encoding="utf-8").strip()
        if file_secret:
            return file_secret

    return ""


def _verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=1))
    except Exception:
        return False


def _upsert_env_key(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            out.append(f"{prefix}{value}")
            replaced = True
        else:
            out.append(line.rstrip("\n"))

    if not replaced:
        out.append(f"{prefix}{value}")

    return out


def _write_env_file(env_file: Path, admin_user: str, admin_hash: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if env_file.exists():
        existing_lines = env_file.read_text(encoding="utf-8").splitlines()

    lines = _upsert_env_key(existing_lines, "ADMIN_DOC_USER", admin_user)
    lines = _upsert_env_key(lines, "ADMIN_PASS_HASH", admin_hash)

    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_cmd(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _run_interactive_cmd(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd, check=False).returncode
    except Exception:
        return 1


def _run_totp_restart_gate(gate_cmd: list[str]) -> int:
    print(f"[INFO] Running restart TOTP gate: {' '.join(gate_cmd)}")
    code = _run_interactive_cmd(gate_cmd)
    if code != 0:
        print(f"[ERROR] TOTP restart gate failed with exit code {code}")
    return code


def _restart_service(service_name: str, gate_cmd: list[str], skip_restart_gate: bool) -> int:
    if not skip_restart_gate:
        gate_code = _run_totp_restart_gate(gate_cmd)
        if gate_code != 0:
            return gate_code

    steps = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", service_name],
        ["systemctl", "status", service_name, "--no-pager", "-l"],
    ]

    for cmd in steps:
        print(f"[INFO] Running: {' '.join(cmd)}")
        code, output = _run_cmd(cmd)
        if output:
            print(output)
        if code != 0:
            print(f"[ERROR] Command failed with exit code {code}: {' '.join(cmd)}")
            return code

    print("[OK] Service reloaded and restarted successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset admin username/password with TOTP verification, then reload/restart backend service.",
    )
    parser.add_argument("--env-file", default=os.getenv("ADMIN_ENV_FILE", "/home/.backend.env"), help="Path to backend env file")
    parser.add_argument("--secret-file", default=os.getenv("ADMIN_TOTP_SECRET_FILE", "/home/.admin_totp_secret"), help="Path to persisted admin TOTP secret")
    parser.add_argument("--service-name", default="backend.service", help="Systemd service name")
    parser.add_argument("--username", default="", help="New admin username")
    parser.add_argument("--password", default="", help="New admin password for this reset only; bcrypt hash is stored (prefer interactive)")
    parser.add_argument("--totp", default="", help="Current TOTP code")
    parser.add_argument("--no-restart", action="store_true", help="Update credentials only, do not restart service")
    parser.add_argument("--skip-restart-gate", action="store_true", help="Skip running the TOTP restart gate command before restart")
    parser.add_argument("--totp-gate-cmd", default="sudo /usr/bin/python3 /home/tools/totp.py", help="Command used to ask TOTP again before backend restart")
    parser.add_argument("--non-interactive", action="store_true", help="Fail if required values are missing")
    args = parser.parse_args()

    env_file = Path(args.env_file).resolve()
    secret_file = Path(args.secret_file).resolve()

    secret = _load_totp_secret(secret_file)
    if not secret:
        print("[ERROR] No TOTP secret found. Set ADMIN_TOTP_SECRET or provide a valid secret file.")
        return 1

    totp_code = (args.totp or "").strip()
    if not totp_code:
        if args.non_interactive:
            print("[ERROR] Missing --totp in non-interactive mode.")
            return 1
        totp_code = _prompt_non_empty("Current TOTP code: ")

    if not _verify_totp(secret, totp_code):
        print("[ERROR] Invalid TOTP code. Aborting reset.")
        return 1

    admin_user = (args.username or "").strip()
    if not admin_user:
        if args.non_interactive:
            print("[ERROR] Missing --username in non-interactive mode.")
            return 1
        admin_user = _prompt_non_empty("New admin username: ")

    admin_password = args.password or ""
    if not admin_password:
        if args.non_interactive:
            print("[ERROR] Missing --password in non-interactive mode.")
            return 1
        admin_password = _read_password_interactive()

    admin_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
    if not bcrypt.checkpw(admin_password.encode(), admin_hash.encode()):
        print("[ERROR] Internal verification failed. Hash does not match password.")
        return 1

    try:
        _write_env_file(env_file, admin_user, admin_hash)
    except Exception as exc:
        print(f"[ERROR] Failed to write env file {env_file}: {exc}")
        return 1

    print(f"[OK] Updated credentials in {env_file}")
    print(f"[INFO] ADMIN_DOC_USER={admin_user}")
    print(f"[INFO] ADMIN_PASS_HASH length={len(admin_hash)}")

    if args.no_restart:
        print("[INFO] Skipping service restart (--no-restart).")
        return 0

    gate_cmd = shlex.split((args.totp_gate_cmd or "").strip())
    if not gate_cmd and not args.skip_restart_gate:
        print("[ERROR] Invalid --totp-gate-cmd")
        return 1

    return _restart_service(args.service_name, gate_cmd, args.skip_restart_gate)


if __name__ == "__main__":
    raise SystemExit(main())
