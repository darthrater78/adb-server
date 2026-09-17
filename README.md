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
# if you'll open the UI at anything other than localhost — a LAN IP, a
# Tailscale name — add it to ALLOWED_HOSTS in the same file, or the app
# answers 400 to every request for an unlisted host
docker compose up -d --build
```

The UI is bound to all interfaces (`0.0.0.0:8080`), reachable from your LAN
over plain HTTP by default — a deliberate trade-off for a trusted home
network, not an oversight (see the comment in `docker-compose.yml`). The
login password and session cookie travel in cleartext to anyone else on that
network. If that stops being acceptable, put a reverse proxy with TLS in
front (Tailscale serve, Caddy, etc.) and rebind this to `127.0.0.1:8080`.

## Using it

1. **Repos** — add a GitHub `owner/repo` to watch and an asset glob (default
   `*.apk`). It's polled every `POLL_INTERVAL_MINUTES` (default 10), or check
   immediately with "Check now".
2. **Devices** — on the phone: Settings → Developer options → Wireless
   debugging → "Pair device with pairing code" gives a pairing address + code.
   The main Wireless debugging screen separately shows a connect address.
   Enter both on the Devices page to pair. A newly paired device is **not
   trusted** by default — trust it explicitly before it can receive pushes.
3. **Staged** — every verified release lands here, with the release notes
   GitHub reports for it (if any). Push it to any trusted device.
   You can also **upload an APK directly** from this page, for a build that
   isn't published as a GitHub release. An upload is verified exactly like a
   polled release — unsigned and debug-signed files are rejected, and the
   signer fingerprint is recorded — but it belongs to no watched repo, so it
   neither sets nor is checked against a repo's pin. The operator who uploaded
   it is its provenance; it is marked "Uploaded" in the list. A debug-signed
   build is accepted on this path and marked "Debug" — see Release
   verification below. Re-uploading a file that is already staged is refused
   rather than duplicated.
4. **Installs** — history and logs of every push attempt. A push runs in the
   background; submitting one takes you to a live status page that updates
   until the install finishes.

Pick a color theme (Flashbang / Dark / OLED) from the header — it's saved as
a cookie and otherwise follows your OS's light/dark preference.

## Release verification

The first release ever staged for a repo pins that repo's package name and
signer certificate SHA-256. Every later release must match both, or it's
rejected and surfaced as an error on the Repos page — it is never silently
skipped and never auto-trusted. This is what stops a compromised upstream
account, or a malicious asset uploaded to someone else's release, from
reaching your devices unnoticed.

Unsigned APKs are always rejected, pin or no pin, and so is a **polled
release** that is debug-signed — nobody is watching when the poller runs, and
the default Android debug certificate is generated locally per machine, so it
identifies nobody.

A **manually uploaded** APK may be debug-signed: staging a dev build is the
point of uploading by hand, and an operator is present to read the warning.
Those are marked `Debug` in the Staged list and stay marked, so what you are
about to push is never ambiguous.

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
