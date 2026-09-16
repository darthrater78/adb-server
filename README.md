# adb-server

Self-hosted system that watches GitHub repos for new APK releases, verifies
them, stages them, and pushes them to wireless-ADB-paired Android devices
you've explicitly trusted.

Two containers over a private Docker network:

- **adb-server** — runs `adb server`, holds the paired-device keys. Not
  reachable from outside the compose network.
- **app** — FastAPI + SQLite. Polls GitHub, verifies and stages releases,
  serves the web UI, and talks to `adb-server` to pair/connect/install.

## Quickstart

```bash
cp .env.example .env
# edit .env: set SECRET_KEY (openssl rand -hex 32), APP_USERNAME, APP_PASSWORD
docker compose up -d --build
```

The UI is bound to `127.0.0.1:8080` only. To reach it from elsewhere on your
LAN, put a reverse proxy with TLS in front (Tailscale serve, Caddy, etc.) —
don't change the port binding to expose it directly; see the comment in
`docker-compose.yml`.

## Using it

1. **Repos** — add a GitHub `owner/repo` to watch and an asset glob (default
   `*.apk`). It's polled every `POLL_INTERVAL_MINUTES` (default 10), or check
   immediately with "Check now".
2. **Devices** — on the phone: Settings → Developer options → Wireless
   debugging → "Pair device with pairing code" gives a pairing address + code.
   The main Wireless debugging screen separately shows a connect address.
   Enter both on the Devices page to pair. A newly paired device is **not
   trusted** by default — trust it explicitly before it can receive pushes.
3. **Staged** — every verified release lands here. Push it to any trusted
   device.
4. **Installs** — history and logs of every push attempt.

## Release verification

The first release ever staged for a repo pins that repo's package name and
signer certificate SHA-256. Every later release must match both, or it's
rejected and surfaced as an error on the Repos page — it is never silently
skipped and never auto-trusted. This is what stops a compromised upstream
account, or a malicious asset uploaded to someone else's release, from
reaching your devices unnoticed.

Debug-signed and unsigned APKs are always rejected, pin or no pin.

## Security notes

- Single-operator app: one username/password, no multi-user accounts.
- Session cookies are signed, `HttpOnly`, `SameSite=Strict`; all
  state-changing requests require a CSRF token in the form body.
- `adb-server`'s port is never published to the host or LAN — only reachable
  from `app`, over the internal compose network. Do not switch either service
  to `network_mode: host`.
- `adb`, `apksigner`, and `aapt` are built from Google's official releases
  with a pinned URL and a verified SHA-1 checksum in each Dockerfile, not
  from a third-party pre-built image.
- No signature verification bypass, no `install -g/-d/-t` flags — installs
  are always `install -r` only.

## Non-goals (v1)

- No multi-user accounts.
- No push-to-all — one device per push.
