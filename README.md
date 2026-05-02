# custom_backend

MiraiChat `custom_backend` is the open-source, self-hosted backend for encrypted message delivery, designed for personal deployments and small private servers.

## Scope

This package keeps the parts of the system that a self-hosted deployment can safely own:

- local session authentication,
- messaging APIs,
- WebSocket delivery,
- offline message storage,
- unread-state tracking,
- local admin and operator tooling,
- presence and fanout backends,
- encrypted chat payload transport.

This package does not include any central authority logic, push delivery, license enforcement, or global identity services.

## Push Boundary

Push is intentionally excluded from this package to preserve the trust boundary.

- Self-hosted deployments must not register device tokens.
- Self-hosted deployments must not assert license status to any upstream service.
- Self-hosted deployments must not relay push requests to a central hub.

The trusted central backend is expected to provide future APIs for:

- device-token registration,
- push authorization,
- server identity enforcement,
- license validation,
- quota governance.

TODO markers in `chat_backend/legacy_app.py` indicate where old self-hosted push routes were removed and where official clients will eventually switch to trusted central APIs.

## Entry Points

- `new_main.py`: migration/backend compatibility entry point.
- `chat_backend/migration_app.py`: migration service.
- `chat_backend/legacy_app.py`: local messaging and transport service.

## Operator Notes

- Use `tools/start_backend_gui.py` for the desktop launcher.
- Use `tools/NON_GUI_SETUP.md` for shell-based startup.
- Do not look for `HUB_LICENSE_KEY`, `HUB_LICENSE_SECRET`, `HUB_SESSION_TOKEN`, or push queue settings. Those were removed as part of the v4 trust-boundary cleanup.

This backend is intended for personal users, small groups, and developers who want to run MiraiChat locally or on their own server.

## Licensing

For personal and non-commercial use, this backend is provided under AGPLv3 terms.

Commercial users must obtain the developer's consent before using the backend: <miraichat@hotmail.com>

For full text and policy details:

- AGPLv3: see LICENSE_AGPLv3.md
- Commercial policy: see LICENSE_COMMERCIAL.md

## Support Us
### Card Donation
![Card Donation](tools/assets/donation.png)

### Crypto Donation
#### USDT_TRC20: TEt8ww5Z76EmbRriLc6aNWwWFsjGFmgrLm
#### USDT_SPL: 7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q
#### USDC_TRC20: TEt8ww5Z76EmbRriLc6aNWwWFsjGFmgrLm
#### USDC_SPL: 7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q
#### BTC: bc1qjfevw4v005yzxtncaeh4z2us2p7jnpz3kz4qqe
#### ETH_ERC20: 0xf1f8177fA841D38d086ddba78A930C655eC76792
#### SOL: 7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q

## Thank you for your support.
