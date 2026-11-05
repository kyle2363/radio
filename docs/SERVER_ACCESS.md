# Server access

The backend listens on `127.0.0.1` by default. This keeps the service available to the local Electron and browser clients without exposing it to the network.

## Android USB pairing and proposed remote access

Checkpoints 1A–1B implement an explicitly enabled USB development gateway on `127.0.0.1:18766`, with signed server identity, one-time pairing and mandatory scoped device authentication for capabilities and prepared catalog/vector reads. It starts only through the owned Electron development process and is off at startup. No admin/legacy/profile-write or source-audio route is exposed. Follow [MOBILE_PAIRING.md](MOBILE_PAIRING.md) for installation, ADB forwarding, pairing, catalog preparation/import, revocation and recovery.

Catalog preparation is an explicit local parent-authorized action. It serves immutable published data and checks API-owned ingest activity/epochs before publication; independent maintenance scripts remain outside that tracker, so do not run them concurrently with preparation. SQLite and Chroma still do not share a transaction. Preparation failures retain the last prepared revision, and phone reads never start ingestion, export or models. A preparation deadline prevents late publication but cannot forcibly interrupt a blocked storage call.

Checkpoint 3A implements explicitly enabled Cloudflare Quick Tunnel hosting on `codex/android-3a-remote-hosting`; see [setup and acceptance](REMOTE_HOSTING.md). It targets only the separate mobile gateway, never the full desktop FastAPI application. Exact public Host admission, a short owner lease and scoped device bearer validation apply even though cloudflared connects from loopback. Firebase endpoint discovery remains checkpoint 3B.

The user deferred direct LAN hosting. Debug USB/`adb reverse` remains available, while verified remote updates require an exact signed HTTPS Quick Tunnel origin. Release clients reject cleartext USB transport. The existing desktop configuration remains unchanged. The plain-LAN opt-in below is an existing desktop deployment option, not the phone integration path or permission to enable it on shared Wi-Fi.

## Configuration

Set configuration in your own environment or a private `.env` file. Do not commit real passwords, authentication secrets, certificates, or private origins.

```dotenv
RADIO_BIND_HOST=127.0.0.1
RADIO_PASSWORD=replace-with-a-private-password
RADIO_AUTH_SECRET=replace-with-at-least-32-random-bytes
RADIO_ALLOWED_HOSTS=localhost,127.0.0.1
RADIO_TRUSTED_ORIGINS=http://localhost:5173
```

Use a password manager to create the password and a separate secret of at least 32 random bytes. Both `RADIO_PASSWORD` and `RADIO_AUTH_SECRET` are required for authenticated remote or proxy access. `RADIO_ALLOWED_HOSTS` is a comma-separated host allowlist; `RADIO_TRUSTED_ORIGINS` is a comma-separated browser origin allowlist. A null browser origin is rejected.

The login page and its static assets are public so a user can sign in. API, media, and WebSocket routes require authentication. Session expiry is enforced when a WebSocket next receives activity. Logging out closes the authenticated session; restarting the server invalidates existing auth cookies.

## Private LAN access

For plain HTTP on a trusted private LAN, explicitly opt in:

```dotenv
RADIO_BIND_HOST=0.0.0.0
RADIO_ALLOW_INSECURE_LAN=1
RADIO_PASSWORD=replace-with-a-private-password
RADIO_AUTH_SECRET=replace-with-at-least-32-random-bytes
RADIO_ALLOWED_HOSTS=radio-hostname,192.0.2.10
RADIO_TRUSTED_ORIGINS=http://radio-hostname:8765
```

Use the server's real private hostname/IP in the allowlists. Keep this traffic on a trusted LAN or VPN. Do not forward the service port from your router to the public internet.

## HTTPS reverse proxy

For access through a reverse proxy, prefer HTTPS and set the public origin to the fixed HTTPS URL. The backend does not trust forwarded headers to infer the public scheme or host.

```dotenv
RADIO_BIND_HOST=127.0.0.1
RADIO_PUBLIC_ORIGIN=https://radio.example.invalid
RADIO_ALLOWED_HOSTS=radio.example.invalid,localhost,127.0.0.1
RADIO_TRUSTED_ORIGINS=https://radio.example.invalid
RADIO_PASSWORD=replace-with-a-private-password
RADIO_AUTH_SECRET=replace-with-at-least-32-random-bytes
```

Terminate TLS at the reverse proxy, keep the backend bound to loopback, and forward only the needed HTTP and WebSocket traffic. The application uses `proxy_headers=False`; configure the public origin explicitly. Alternatively, the backend can terminate TLS with `RADIO_TLS_CERT` and `RADIO_TLS_KEY` set together to private certificate/key file paths. Never expose the server remotely without credentials and TLS, unless you deliberately use the explicit plain-LAN opt-in above.

## Isolated instances

For an isolated development or test instance, point `RADIO_DATA_DIR`, `RADIO_MUSIC_DIR`, and `RADIO_METADATA_DIR` at dedicated directories. Use `RADIO_TTS_ENABLED` and `RADIO_PROC_MGR_ENABLED` to disable optional speech or managed subprocess startup. Do not point an experiment at your production data directories if it may write to them. Keep private backups inside the instance's data directory and outside source control.

Run a single API worker. The playback coordinator, queue, and SQLite ownership are process-local; multiple API workers do not share these in-memory locks or state.
