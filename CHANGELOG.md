# Changelog

## 1.0.0 — 2026-09-23

First stable release.

- Added: Settings page. **Notifications** — point ADB Server at an Apprise
  API server (its notify URL or address + config key, with optional tags), or
  add individual Apprise services, alongside any in `.env`; choose which
  events notify; send a test to one service or all, with a delivered/failed
  result for each, or test a server or URL before saving it; a URL guide for common services; tokens are always masked
  and never logged. **Appearance** — an app-wide two-tone colour scheme
  (primary and secondary), from preset pairs or two colour pickers, with a
  live preview, adjusted per theme to stay readable. Custom pairs can be
  saved under a name, re-applied and deleted.
- Added: two-factor sign-in (TOTP, any authenticator app) in Settings →
  Security, with 10 single-use recovery codes, an option to trust a browser
  for 30 days (revocable from Settings), a 15-minute lockout after 5 wrong
  codes, and `mfa_admin.py unlock|reset` for getting back in from the server.
- Added: pairing with a QR code, as in Android Studio: scan the code on the
  Devices page and the phone is paired, added and trusted by itself (the QR
  page says so; pairing with a code still leaves trusting to you). Needs the new
  `mdns` container, which listens on the host's network for the phone's
  announcement (multicast can't reach the bridge network) and shares only a
  read-only file with the app; the adb server stays off the LAN.
- Fixed: "Find" and pushes never recognised a phone after its wireless
  debugging port changed, so they reported it "not found". Devices are now
  identified by their hardware serial rather than adb's ip:port name, and
  ones paired before this are upgraded in place, keeping their nickname,
  trust and history. Re-pairing one keeps its nickname and history but asks
  you to trust it again: the same IP doesn't prove it's the same phone.
- Changed: paired phones list this server as `@adbserver` instead of a
  random container ID (re-pair to see it).
- Changed: on desktop every table row is one line, sized to its contents;
  long names are shortened with a tooltip. Stacked cards are for phones only.
- Security: request bodies are limited before they're read. Previously any
  POST — including the unauthenticated login form — accepted an unbounded
  multipart upload, which FastAPI spooled to disk before checking the
  session, so anyone who could reach the port could fill the data volume.
  Forms are now capped at 64 KB, only the upload route takes files, and only
  from a signed-in session.
- Security: logging out invalidates every issued session cookie, not just
  the browser's copy.
- Security: a push re-checks the device's trust when it runs, not only when
  it's queued, and never reaches a different paired phone that has taken over
  an old device's IP.
- Security: two-factor codes are checked atomically — a burst of parallel
  sign-in attempts can neither spend one code twice nor get more than five
  guesses before the lockout.
- Security: the `mdns` listener drops its oldest entry when full, so a host
  flooding the LAN with announcements can't block QR pairing.
- Added: a detailed Security section and screenshots in the README.
- Added: the header links to this version's release notes and to the GitHub
  repository.
- Fixed: a non-ASCII username, password or CSRF token caused a server error
  instead of a normal rejection.
- Fixed: a push interrupted by a restart stayed "installing" forever; it's
  now marked failed at startup.
- Fixed: network errors talking to GitHub left partial downloads behind and
  weren't shown on the Repos page.
- Changed: the GitHub token is only ever sent to `api.github.com`.
- Changed: Android tool downloads are pinned by SHA-256 rather than SHA-1.
- Added: manual APK upload on the Staged page, for builds not published as a
  GitHub release. Verified like a polled release and stored by content hash;
  debug-signed builds are allowed on this path only, and marked.
- Added: `release.yml` — pushing a `v*` tag publishes all three images to GHCR and
  creates the GitHub release, after checking the tag is on the default
  branch, matches `VERSION`, and CI passed for that commit.
- Added: CI checks that `VERSION` matches `CHANGELOG.md`, that the compose
  file is valid, and runs `pip-audit` on the Python dependencies.
- Changed: release notes on the Staged page open in a full-width panel, and
  the Status page links to them.
- Changed: phone layout — the nav wraps into tap-sized links with the current
  page highlighted, and every table becomes a stack of labelled cards (up to
  960px wide, so tablets get it too). Buttons never wrap their text. Uploads
  have their own page and "Upload" nav link.
- Added: Update/Install on the Status page asks for confirmation first,
  showing the version and device.
- Changed: the app has its own identity — "ADB Server" name ("APK Pusher" subtitle) and logo (also
  the browser-tab icon), a teal palette across all three themes, and a
  redesigned sign-in page. Secondary and destructive buttons are outlined so
  each row has one primary action. Timestamps read as `Sep 23, 11:25 UTC`,
  with the exact value on hover.
- Fixed: only an APK's first signer was checked for the debug certificate and
  covered by the pin; every signer is now. The fingerprint is read from the
  certificate digest line only, never the public-key digest.
- Fixed: the login rate limiter kept every IP that ever failed a login in
  memory forever; expired entries are swept and the table is capped.
- Fixed: `POST /login` now refuses a cross-origin form post.
- Fixed: an asset download follows a redirect only over HTTPS, and only to
  GitHub's own hosts.
- Fixed: bracketed IPv6 device addresses (`[fd00::5]:5555`) are parsed
  instead of split at the wrong colon.
- Changed: the `app` container runs with a read-only root filesystem.
- Changed: indexes on the columns the listings join and sort on; the app logs
  its accepted `ALLOWED_HOSTS` at startup.
- Fixed: a release that failed signature verification or the pin check was
  re-downloaded (up to 500 MB) on every poll. It is now checked once and
  skipped until a newer release appears; the error stays visible.
- Fixed: APK verification (`apksigner`/`aapt`) no longer blocks the web UI
  while it runs.
- Fixed: a release tag containing `/` crashed its repo's check, and any
  unexpected error in one repo stopped every repo after it from being polled.
- Fixed: removing a repo now deletes its staged APK files from disk.
- Fixed: error messages sent to the Staged page (e.g. "Device is not
  trusted") were never displayed.
- New: staged APK retention — only the newest `KEEP_RELEASES_PER_REPO`
  (default 3) files per repo are kept; install history is preserved. Staged
  files can also be deleted by hand.
- New: Status page (now the home page) — each watched app's installed
  version on every trusted device next to the latest staged version, with a
  one-click Update/Install.
- New: pushes find a device again after its wireless debugging port changes,
  by scanning its last known IP and confirming its serial; "Find" on the
  Devices page does it on demand.
- New: releases with several APKs (per-ABI builds) stage every variant; the
  Status page picks the right one for each device's CPU, and a push of an
  incompatible APK is refused. Existing databases are migrated automatically.
- New: signing-key rotation review — a pin mismatch shows both certificates
  and whether the new APK proves the rotation (APK Signature Scheme v3
  lineage); the new certificate can be accepted as the pin.
- New: notifications through Apprise (`APPRISE_URLS`) for staged releases,
  rejected releases and push results.
- New: per-device auto-update — a device following an app gets each new
  release pushed to it automatically.
- New: per-repo "Include pre-releases" option.
- New: audit log page recording logins, trust changes, re-pins, repo and
  device changes and pushes.
- New: `/healthz` and Docker health checks for both containers.
- Changed: "Check now" and accepting a new signer run in the background
  instead of holding the page open during the download; a repo is never
  checked twice at once.
- Changed: GitHub polls use conditional requests (ETags), so unchanged repos
  don't count against the API rate limit.
- New: automated test suite (`pytest`, run with `scripts/test.sh`).
- New: CI builds both images and runs the tests on every push and PR;
  actionlint checks workflow edits; Dependabot watches pip, Docker base images
  and Actions. Base images are now pinned by digest.

## 0.2.0 — 2026-09-16

- Push is now a background job: submitting a push redirects immediately to a
  status page (`/installs/<id>`) that auto-refreshes (no JavaScript — CSP
  stays `script-src 'none'`) and shows an animated progress bar through
  pending → installing → success/failed. A crash mid-install now always
  resolves the install row instead of leaving it stuck "installing" forever.
- Staged APKs now capture and display each release's GitHub release notes
  (new `release_notes` column, auto-migrated for existing databases).
- Repos page gets a visual pass: status badges, tag pills, and a
  watched/error-count summary line.
- The running app version now shows in the page header, read from `VERSION`
  at container build time.
- Theme switcher: Flashbang (light), Dark, and OLED black, chosen via a
  CSRF-protected form and stored as a plain cookie — no client-side JS. With
  no preference set, the app still follows the OS `prefers-color-scheme` as
  before.

## 0.1.0 — 2026-09-16

Initial scaffold: two-container system (`adb-server` for the ADB protocol,
`app` for the FastAPI web UI + poller) that watches GitHub repos for new APK
releases and pushes verified builds to trusted, wireless-paired Android
devices.

- GitHub release polling with signer-certificate and package-name pinning on
  the first release seen per repo; every later release must match both or is
  rejected and surfaced, never silently skipped.
- Device pairing/connect/trust flow over wireless ADB, with device identity
  re-confirmed immediately before every push (address drift protection).
- Session-cookie auth (signed, `HttpOnly`, `SameSite=Strict`) with CSRF
  tokens on every state-changing request, login rate limiting, and
  `TrustedHostMiddleware` + CSP/security headers.
- `adb`, `apksigner`, and `aapt` built into the images from Google's official
  releases with pinned URLs and verified SHA-1 checksums, not a third-party
  pre-built image.
- `adb-server`'s port is confined to the internal Docker network — never
  published to the host or LAN.
