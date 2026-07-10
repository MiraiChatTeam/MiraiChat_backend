import os
from urllib.parse import urlparse


def _read_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _clean_env_secret(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        value = ""
    cleaned = str(value).strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()
    return cleaned


SERVER_IDENTITY_SALT = os.getenv("SERVER_IDENTITY_SALT", "REPLACE_THIS_WITH_A_SECURE_LONG_RANDOM_STRING_FOR_YOUR_SERVER")
REGISTRATION_KEY = os.getenv("REGISTRATION_KEY") if os.getenv("REGISTRATION_KEY") != "" else None

IS_PUBLIC_HUB = _read_bool("IS_PUBLIC_HUB", "True")
PUBLIC_HUB_URL = os.getenv("PUBLIC_HUB_URL", "https://public.miraichat.net")

API_PUBLIC_BASE_URL = os.getenv("API_PUBLIC_BASE_URL", PUBLIC_HUB_URL)
WS_CONNECTION_MODE = os.getenv("WS_CONNECTION_MODE", "tunnel").strip().lower()
if WS_CONNECTION_MODE not in ("tunnel", "direct"):
    WS_CONNECTION_MODE = "tunnel"

WS_TUNNEL_BASE_URL = os.getenv("WS_TUNNEL_BASE_URL", "").strip()
WS_DIRECT_PUBLIC_URL = os.getenv("WS_DIRECT_PUBLIC_URL", "").strip()

TRANSPORT_PIN_HINTS = {
    "public.miraichat.net": [],
    "ws.miraichat.net": [
        "sha256/A125D53E66FD1E52797F1FC221789E6A630D1E5B322EC5245D466F1B387D928B"
    ],
}

ALLOWED_WS_HOSTS = {
    "ws.miraichat.net",
    "public.miraichat.net",
}

ALLOWED_WS_ORIGINS = {
    "https://miraichat.net",
    "https://public.miraichat.net",
}


def _normalize_ws_host(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    if "://" in raw:
        parsed = urlparse(raw)
        raw = (parsed.netloc or parsed.path or "").lower()

    raw = raw.split("/", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        return raw[1:raw.index("]")].strip()

    return raw.split(":", 1)[0].strip()


def _normalize_ws_origin(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc.lower()}".rstrip("/")

    return raw.rstrip("/")


def _build_allowed_ws_hosts() -> set[str]:
    hosts = {_normalize_ws_host(host) for host in ALLOWED_WS_HOSTS if _normalize_ws_host(host)}
    for candidate in (WS_DIRECT_PUBLIC_URL, WS_TUNNEL_BASE_URL, API_PUBLIC_BASE_URL, PUBLIC_HUB_URL):
        host = _normalize_ws_host(candidate)
        if host:
            hosts.add(host)
    return hosts


def _build_allowed_ws_origins() -> set[str]:
    origins = {
        _normalize_ws_origin(origin)
        for origin in ALLOWED_WS_ORIGINS
        if _normalize_ws_origin(origin)
    }

    for candidate in (API_PUBLIC_BASE_URL, PUBLIC_HUB_URL):
        origin = _normalize_ws_origin(candidate)
        if origin:
            origins.add(origin)

    direct_host = _normalize_ws_host(WS_DIRECT_PUBLIC_URL)
    if direct_host:
        origins.add(f"https://{direct_host}")

    return origins


REGION_POLICY_MODE = os.getenv("REGION_POLICY_MODE", "auto").strip().lower()
if REGION_POLICY_MODE not in ("auto", "manual"):
    REGION_POLICY_MODE = "auto"

DEFAULT_REGION = os.getenv("DEFAULT_REGION", "global").strip().lower()
if DEFAULT_REGION not in ("global", "china", "unknown"):
    DEFAULT_REGION = "global"

CHINA_FCM_POLICY = os.getenv("CHINA_FCM_POLICY", "disable").strip().lower()
if CHINA_FCM_POLICY not in ("disable", "best_effort"):
    CHINA_FCM_POLICY = "disable"

HUB_LICENSE_KEY = os.getenv("HUB_LICENSE_KEY", "")
CENTRAL_SERVER_TOKEN = os.getenv("CENTRAL_SERVER_TOKEN", "")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_MERCHANT_COUNTRY = os.getenv("STRIPE_MERCHANT_COUNTRY", "US")
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "hybrid").strip().lower()
if PAYMENT_MODE not in ("iap_only", "hybrid", "external_only"):
    PAYMENT_MODE = "hybrid"

ALLOW_EXTERNAL_DONATIONS = PAYMENT_MODE in ("hybrid", "external_only")

APPLE_IAP_SHARED_SECRET = os.getenv("APPLE_IAP_SHARED_SECRET", "").strip()
APPLE_IAP_BUNDLE_ID = os.getenv("APPLE_IAP_BUNDLE_ID", "").strip()
GOOGLE_PLAY_PACKAGE_NAME = os.getenv("GOOGLE_PLAY_PACKAGE_NAME", "").strip()
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "").strip()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "stored_files")
FILE_RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", "3"))
FILE_TOKEN_TTL_MINUTES = int(os.getenv("FILE_TOKEN_TTL_MINUTES", "60"))
OFFLINE_MSG_RETENTION_DAYS = int(os.getenv("OFFLINE_MSG_RETENTION_DAYS", "7"))
PENDING_LEASE_SECONDS = int(os.getenv("PENDING_LEASE_SECONDS", "20"))
DEFAULT_STORAGE_LIMIT = int(os.getenv("DEFAULT_STORAGE_LIMIT", str(100 * 1024 * 1024)))
PRIVATE_SERVER_STORAGE_LIMIT_MB = int(
    os.getenv("PRIVATE_SERVER_STORAGE_LIMIT_MB", str(max(DEFAULT_STORAGE_LIMIT // (1024 * 1024), 1)))
)
PRIVATE_SERVER_STORAGE_LIMIT = max(PRIVATE_SERVER_STORAGE_LIMIT_MB, 1) * 1024 * 1024
DONATION_MONTHLY_STORAGE_LIMIT_MB = int(os.getenv("DONATION_MONTHLY_STORAGE_LIMIT_MB", "200"))
DONATION_MONTHLY_STORAGE_LIMIT = max(DONATION_MONTHLY_STORAGE_LIMIT_MB, 1) * 1024 * 1024
DONATION_MONTHLY_PRIORITY_USERS = max(0, int(os.getenv("DONATION_MONTHLY_PRIORITY_USERS", "20")))
DONATION_BADGE_DAYS = max(1, int(os.getenv("DONATION_BADGE_DAYS", "30")))
SESSION_EXPIRY_DAYS = int(os.getenv("SESSION_EXPIRY_DAYS", "30"))
SESSION_CACHE_TTL = int(os.getenv("SESSION_CACHE_TTL", "30"))

SIGNING_KEY_FILE = os.getenv("SIGNING_KEY_FILE", "server_signing_key.pem")
IDENTITY_KEY_FILE = os.getenv("IDENTITY_KEY_FILE", SIGNING_KEY_FILE)
IDENTITY_PUBLIC_KEY_FILE = os.getenv("IDENTITY_PUBLIC_KEY_FILE", "server_identity_public_key.txt")
OLD_SIGNING_KEY_FILE = os.getenv("OLD_SIGNING_KEY_FILE", "server_signing_key_old.pem")

REGISTER_RATE_LIMIT_MAX = int(os.getenv("REGISTER_RATE_LIMIT_MAX", "10"))
REGISTER_RATE_LIMIT_WINDOW = int(os.getenv("REGISTER_RATE_LIMIT_WINDOW", "3600"))
WELCOME_MESSAGES_FILE = os.getenv("WELCOME_MESSAGES_FILE", "tools/.welcome_messages.json")

# Client IPs affect rate limits and the admin IP allowlist, so forwarding
# headers are trusted only after the socket peer matches an explicit boundary.
#
# cloudflare_tunnel (default): trust CF-Connecting-IP only from
#                    TRUSTED_PROXY_CIDRS. The default CIDRs cover a local
#                    cloudflared process; direct peers still cannot spoof it.
# direct:            trust no forwarding header.
# cloudflare_edge:   trust CF-Connecting-IP only from Cloudflare edge CIDRs.
# xforwarded:        trust the final X-Forwarded-For value only from
#                    TRUSTED_PROXY_CIDRS (the proxy must append/overwrite it).
TRUSTED_PROXY_MODE = os.getenv("TRUSTED_PROXY_MODE", "cloudflare_tunnel").strip().lower()
if TRUSTED_PROXY_MODE not in ("direct", "cloudflare_tunnel", "cloudflare_edge", "xforwarded"):
    TRUSTED_PROXY_MODE = "cloudflare_tunnel"

_DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.0/8,::1/128"
TRUSTED_PROXY_CIDRS = [
    cidr.strip()
    for cidr in os.getenv("TRUSTED_PROXY_CIDRS", _DEFAULT_TRUSTED_PROXY_CIDRS).split(",")
    if cidr.strip()
]

# Cloudflare's published edge ranges, used only by cloudflare_edge mode.
_DEFAULT_CLOUDFLARE_IP_RANGES = (
    "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,"
    "141.101.64.0/18,108.162.192.0/18,190.93.240.0/20,188.114.96.0/20,"
    "197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
    "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22,"
    "2400:cb00::/32,2606:4700::/32,2803:f800::/32,2405:b500::/32,"
    "2405:8100::/32,2a06:98c0::/29,2c0f:f248::/32"
)
CLOUDFLARE_IP_RANGES = [
    cidr.strip()
    for cidr in os.getenv("CLOUDFLARE_IP_RANGES", _DEFAULT_CLOUDFLARE_IP_RANGES).split(",")
    if cidr.strip()
]


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_DOC_USER = _clean_env_secret("ADMIN_DOC_USER", "admin")
# Hash-only admin credential. GUI config must store ADMIN_PASS_HASH, never ADMIN_DOC_PASS.
ADMIN_PASS_HASH = _clean_env_secret("ADMIN_PASS_HASH")
ADMIN_TOTP_SECRET = _clean_env_secret("ADMIN_TOTP_SECRET")
PIN_SYNC_AUTO_DEFAULT = _read_bool("PIN_SYNC_AUTO_DEFAULT", "true")
PIN_SYNC_FORCE_MANUAL = _read_bool("PIN_SYNC_FORCE_MANUAL", "false")
PRESENCE_BACKEND = os.getenv("PRESENCE_BACKEND", "memory").strip().lower()
if PRESENCE_BACKEND not in ("memory", "redis"):
    PRESENCE_BACKEND = "memory"
PRESENCE_REDIS_URL = os.getenv("PRESENCE_REDIS_URL", os.getenv("REDIS_URL", "")).strip()
PRESENCE_TTL_SECONDS = max(30, int(os.getenv("PRESENCE_TTL_SECONDS", "180")))

FANOUT_BACKEND = os.getenv("FANOUT_BACKEND", "memory").strip().lower()
if FANOUT_BACKEND not in ("memory", "redis"):
    FANOUT_BACKEND = "memory"
FANOUT_REDIS_URL = os.getenv("FANOUT_REDIS_URL", os.getenv("REDIS_URL", "")).strip()
FANOUT_CHANNEL = os.getenv("FANOUT_CHANNEL", "chat:fanout").strip() or "chat:fanout"
FANOUT_NODE_ID = os.getenv("FANOUT_NODE_ID", "").strip() or os.getenv("HOSTNAME", "node")

REACTION_IDEMPOTENCY_BACKEND = os.getenv("REACTION_IDEMPOTENCY_BACKEND", "auto").strip().lower()
if REACTION_IDEMPOTENCY_BACKEND not in ("auto", "redis", "db", "memory"):
    REACTION_IDEMPOTENCY_BACKEND = "auto"
REACTION_IDEMPOTENCY_REDIS_URL = os.getenv(
    "REACTION_IDEMPOTENCY_REDIS_URL",
    FANOUT_REDIS_URL or PRESENCE_REDIS_URL or os.getenv("REDIS_URL", ""),
).strip()
REACTION_IDEMPOTENCY_TTL_SECONDS = max(60, int(os.getenv("REACTION_IDEMPOTENCY_TTL_SECONDS", "300")))

ROTATION_INTERVAL_DAYS = max(1, int(os.getenv("ROTATION_INTERVAL_DAYS", "90")))
ROTATION_GRACE_PERIOD_DAYS = max(1, int(os.getenv("ROTATION_GRACE_PERIOD_DAYS", "30")))
ADMIN_IP_ALLOWLIST = [ip.strip() for ip in _clean_env_secret("ADMIN_IP_ALLOWLIST").split(",") if ip.strip()]
