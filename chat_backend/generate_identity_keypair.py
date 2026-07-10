import argparse
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

try:
    from chat_backend.settings import IDENTITY_KEY_FILE, IDENTITY_PUBLIC_KEY_FILE
except Exception:
    IDENTITY_KEY_FILE = os.getenv("IDENTITY_KEY_FILE", "server_signing_key.pem")
    IDENTITY_PUBLIC_KEY_FILE = os.getenv(
        "IDENTITY_PUBLIC_KEY_FILE",
        "server_identity_public_key.txt",
    )


def _public_key_b64(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def _fingerprint(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(raw).digest()
    return ":".join(f"{b:02X}" for b in digest)


def _write_private_key(path: str, private_key) -> None:
    with open(path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or display the backend Ed25519 server identity key.",
    )
    parser.add_argument("--private-key", default=IDENTITY_KEY_FILE)
    parser.add_argument("--public-metadata", default=IDENTITY_PUBLIC_KEY_FILE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if os.path.exists(args.private_key) and not args.force:
        with open(args.private_key, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise SystemExit("Existing identity key is not an Ed25519 private key")
    else:
        private_key = ed25519.Ed25519PrivateKey.generate()
        _write_private_key(args.private_key, private_key)

    payload = {
        "identity_public_key": _public_key_b64(private_key),
        "identity_fingerprint": _fingerprint(private_key),
        "identity_key_version": 1,
    }
    with open(args.public_metadata, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True)
        f.write("\n")

    print(json.dumps(payload, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
