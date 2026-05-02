import argparse
import sys

try:
    import pyotp
except Exception:
    print("ERROR: pyotp is not installed. Install with: pip install pyotp")
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate admin TOTP setup values (secret, URI, current code)."
    )
    parser.add_argument(
        "--account",
        default="admin@chatapp-server",
        help="Account label shown in authenticator app",
    )
    parser.add_argument(
        "--issuer",
        default="ChatApp Admin",
        help="Issuer name shown in authenticator app",
    )
    args = parser.parse_args()

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=args.account, issuer_name=args.issuer)
    current_code = totp.now()

    print("=== Admin TOTP Setup ===")
    print(f"SECRET={secret}")
    print(f"PROVISIONING_URI={uri}")
    print(f"CURRENT_CODE={current_code}")
    print()
    print("Next steps:")
    print("1) Add SECRET or PROVISIONING_URI to your authenticator app.")
    print("2) Set ADMIN_TOTP_SECRET to SECRET in server environment.")
    print("3) Restart server.")
    print("4) Send x-admin-totp header with current app code for admin endpoints.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
