# Non-GUI Backend Setup

This guide shows how to run the backend directly with `new_main.py` and environment variables, without using the GUI in `tools/`.

Push note:

- Self-hosted `custom_backend` no longer manages device-token registration, relay signing, or push entitlement.
- Official clients must use the trusted central backend for push registration and authorization.

## Prerequisites

- Python installed on the host machine.
- Dependencies installed from `tools/requirements.txt`.
- A shell in the repository root.

Example install:

```powershell
python -m pip install -r tools/requirements.txt
```

Start the backend from the repository root with:

```powershell
python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto
```

`new_main.py` is a compatibility shim that re-exports the migration entrypoint from `chat_backend/`.

## HTTPS Through Cloudflare Tunnel

### 1. Create a tunnel

Install `cloudflared`, sign in to Cloudflare, and create a named tunnel.

Example:

```powershell
cloudflared tunnel login
cloudflared tunnel create miraichat-backend
```

### 2. Route your domain to the tunnel

Create a DNS route so your hostname points at the tunnel.

Example:

```powershell
cloudflared tunnel route dns miraichat-backend chat.example.com
```

### 3. Configure the local tunnel ingress

Create a `config.yml` for `cloudflared` so HTTPS requests are forwarded to the local backend.

Example:

```yaml
tunnel: miraichat-backend
credentials-file: C:\cloudflared\miraichat-backend.json

ingress:
  - hostname: chat.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Start the tunnel:

```powershell
cloudflared tunnel run miraichat-backend
```

### 4. Configure backend environment variables for HTTPS and tunneled WS

When both HTTPS and WebSocket go through the same tunnel domain, keep the backend in tunnel mode.

PowerShell example:

```powershell
$env:PUBLIC_HUB_URL = "https://chat.example.com"
$env:API_PUBLIC_BASE_URL = "https://chat.example.com"
$env:WS_CONNECTION_MODE = "tunnel"
$env:WS_TUNNEL_BASE_URL = "wss://chat.example.com"
$env:DEFAULT_STORAGE_LIMIT = "104857600"
```

Optional settings that are commonly paired with a private deployment:

```powershell
$env:REGISTER_RATE_LIMIT_MAX = "10"
$env:REGISTER_RATE_LIMIT_WINDOW = "3600"
$env:FILE_RETENTION_DAYS = "3"
```

Admin endpoints use hash-based Basic auth plus optional TOTP. Store only the bcrypt hash:

```powershell
$env:ADMIN_DOC_USER = "admin"
$env:ADMIN_PASS_HASH = "<bcrypt_hash>"
$env:ADMIN_TOTP_SECRET = "<authenticator_secret>"
```

Do not store `ADMIN_DOC_PASS` in backend config. When a non-GUI tool must call an admin endpoint, provide the plaintext password only for that one runtime invocation.

Then launch:

```powershell
python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto
```

## WS Exposed Directly

Use this layout when HTTPS stays behind a normal public hostname, but WebSocket is exposed on a dedicated direct endpoint such as `wss://ws.example.com`.

### 1. Expose the WebSocket port publicly

You can publish the same backend port through a reverse proxy that forwards `/ws` and `/ws/`, or expose a dedicated public listener that forwards only WebSocket traffic.

Recommended public flow:

- `https://api.example.com` for REST and regular HTTPS traffic.
- `wss://ws.example.com` for direct WebSocket traffic.

### 2. Recommended firewall rules

- Allow inbound TCP `443` from the public internet.
- Allow inbound TCP `80` only if you need ACME or HTTP-to-HTTPS redirects.
- Do not expose the raw backend port `8000` publicly unless you have a specific reason.
- Restrict SSH or RDP management ports to trusted admin IPs.

### 3. Recommended reverse proxy config

Example Nginx config for a dedicated WebSocket hostname:

```nginx
server {
    listen 443 ssl http2;
    server_name ws.example.com;

    ssl_certificate     /etc/letsencrypt/live/ws.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ws.example.com/privkey.pem;

    location /ws {
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:8000;
    }

    location /ws/ {
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

### 4. Configure backend environment variables for direct WS

Set the public HTTPS base separately from the public WebSocket URL.

```powershell
$env:PUBLIC_HUB_URL = "https://api.example.com"
$env:API_PUBLIC_BASE_URL = "https://api.example.com"
$env:WS_CONNECTION_MODE = "direct"
$env:WS_DIRECT_PUBLIC_URL = "wss://ws.example.com"
$env:DEFAULT_STORAGE_LIMIT = "104857600"
```

If you use Redis-backed presence or fanout for a larger deployment, add those too:

```powershell
$env:PRESENCE_BACKEND = "redis"
$env:PRESENCE_REDIS_URL = "redis://127.0.0.1:6379/0"
$env:FANOUT_BACKEND = "redis"
$env:FANOUT_REDIS_URL = "redis://127.0.0.1:6379/1"
```

Then launch:

```powershell
python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto
```

## Minimal Steps For Advanced Users

This is the shortest non-GUI path.

1. Install dependencies with `python -m pip install -r tools/requirements.txt`.
2. Open a shell in the repository root.
3. Set the transport variables for either tunnel mode or direct mode.
4. Set any storage, retention, rate-limit, or Redis variables you need.
5. Run `python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto`.
6. Verify the advertised transport with `http://127.0.0.1:8000/chat/transport_descriptor`.

Example quick start for tunnel mode:

```powershell
$env:PUBLIC_HUB_URL = "https://chat.example.com"
$env:API_PUBLIC_BASE_URL = "https://chat.example.com"
$env:WS_CONNECTION_MODE = "tunnel"
$env:WS_TUNNEL_BASE_URL = "wss://chat.example.com"
python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto
```

Example quick start for direct mode:

```powershell
$env:PUBLIC_HUB_URL = "https://api.example.com"
$env:API_PUBLIC_BASE_URL = "https://api.example.com"
$env:WS_CONNECTION_MODE = "direct"
$env:WS_DIRECT_PUBLIC_URL = "wss://ws.example.com"
python -m uvicorn new_main:app --host 0.0.0.0 --port 8000 --ws wsproto
```

## Notes

- This workflow does not require any changes to `new_main.py`.
- This workflow does not require any changes inside `chat_backend/`.
- The backend already exposes `/chat/transport_descriptor`, which is the fastest way to confirm the advertised API and WebSocket endpoints after startup.
