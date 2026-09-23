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

The UI is bound to all interfaces (`0.0.0.0:8080`), reachable from your LAN
over plain HTTP by default — a deliberate trade-off for a trusted home
network, not an oversight (see the comment in `docker-compose.yml`). The
login password and session cookie travel in cleartext to anyone else on that
network. If that stops being acceptable, put a reverse proxy with TLS in
front (Tailscale serve, Caddy, etc.) and rebind this to `127.0.0.1:8080`.

## Using it

0. **Status** (the home page) — every watched app against every trusted
   device: installed version, latest staged version, and an Update/Install
   button that picks the right APK for that device's CPU. "Refresh installed
   versions" asks each device what it currently has; versions also refresh
   after every push.
   **Auto-update** per app and device: when on, each newly staged release is
   pushed to that device automatically (trusted devices only; skipped if the
   device already has that version or newer).
1. **Repos** — add a GitHub `owner/repo` to watch and an asset glob (default
   `*.apk`). It's polled every `POLL_INTERVAL_MINUTES` (default 10), or check
   immediately with "Check now" (runs in the background). Pre-releases are
   ignored unless you tick "Include pre-releases" for that repo. Polls use
   conditional requests, so an unchanged repo doesn't use up GitHub's rate
   limit.
2. **Devices** — on the phone: Settings → Developer options → Wireless
   debugging → "Pair device with pairing code" gives a pairing address + code.
   The main Wireless debugging screen separately shows a connect address.
   Enter both on the Devices page to pair. A newly paired device is **not
   trusted** by default — trust it explicitly before it can receive pushes.
   The connect port changes whenever wireless debugging restarts. A push
   that finds the stored port dead scans the device's last known IP
   (`ADB_SCAN_PORTS`, default 30000-49999) and accepts a port only if the
   device there reports the same serial; "Find" does this on demand. mDNS
   discovery isn't used because multicast doesn't cross Docker's bridge
   network. If the phone's IP itself changes, use Reconnect.
3. **Staged** — every verified release lands here, with the release notes
   GitHub reports for it (if any). Push it to any trusted device. When a
   release has several APKs matching the asset glob (per-ABI builds such as
   `arm64-v8a` / `armeabi-v7a` / universal), all of them are staged (up to
   6), and a push refuses an APK the device's CPU can't run. Only the
   newest `KEEP_RELEASES_PER_REPO` (default 3) files per repo are kept on
   disk; older ones are deleted automatically, and you can delete any file by
   hand. Install history is kept either way.
4. **Installs** — history and logs of every push attempt. A push runs in the
   background; submitting one takes you to a live status page that updates
   until the install finishes.
5. **Audit** — logins (including failed ones), trust changes, signer
   re-pins, repo and device changes, auto-update toggles and pushes, with the
   client IP. The newest 5000 entries are kept.

## Notifications

Set `APPRISE_URLS` in `.env` to one or more [Apprise](https://github.com/caronc/apprise/wiki)
URLs — ntfy, Gotify, Home Assistant, Discord, email, a plain JSON webhook and
many more. You'll be told when a release is staged, when one is rejected
(pin mismatch or failed verification), and when a push succeeds or fails.
`NOTIFY_EVENTS` narrows that list. The URLs contain credentials: keep them in
`.env` only.

## Health checks

Both containers have Docker health checks: `app` checks `/healthz`
(unauthenticated; returns only ok/error), `adb-server` checks that the adb
server answers. `127.0.0.1` is always accepted as a host so the check works
whatever `ALLOWED_HOSTS` is set to.

Pick a color theme (Flashbang / Dark / OLED) from the header — it's saved as
a cookie and otherwise follows your OS's light/dark preference.

## Release verification

The first release ever staged for a repo pins that repo's package name and
signer certificate SHA-256. Every later release must match both, or it's
rejected and surfaced as an error on the Repos page — it is never silently
skipped and never auto-trusted. This is what stops a compromised upstream
account, or a malicious asset uploaded to someone else's release, from
reaching your devices unnoticed.

Debug-signed and unsigned APKs are always rejected, pin or no pin.

All APKs in one release must share the same package and signer, or the
release is rejected.

**Signing-key changes.** When a release is signed by a different certificate
than the pinned one, the Repos page shows both certificates and whether the
new APK carries an APK Signature Scheme v3 rotation proof linking it to the
pinned key. You can then accept the new certificate as the pin (after
confirming it's genuine), which re-checks and stages that release.

A rejected release is downloaded and checked once. The poller skips that tag
afterwards (the error stays visible) until a newer release appears.

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

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
bash scripts/test.sh
```

CI (`.github/workflows/ci.yml`) runs the same script and builds both images
on every push and PR. Dependabot keeps pip packages, GitHub Actions and the
digest-pinned base images current.

The tests stub out GitHub, `adb`, `apksigner` and `aapt`, so they need none of
the Android tooling or a device.

## Non-goals (v1)

- No multi-user accounts.
- No push-to-all — one device per push.
