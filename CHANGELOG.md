# Changelog

## Unreleased

- Manual APK upload: stage a build directly from the Staged page without it
  having to be a GitHub release. The signature must still verify and the
  signer fingerprint is recorded; uploads are stored by content hash, so
  re-uploading the same file is refused rather than duplicated.
- Debug-signed APKs are now accepted on the upload path — staging a dev build
  is the point of uploading by hand — and are flagged `Debug` in the Staged
  list, with a warning shown when one is staged. A *polled* release that is
  debug-signed is still refused: nothing is watching when the poller runs. An uploaded APK belongs to no
  watched repo and so neither sets nor is checked against a repo's
  package/signer pin — `staged_apks.repo_id` is now nullable, migrated
  automatically for existing databases.
- Security and quality audit fixes: the upstream release tag no longer reaches
  a filesystem path (a tag containing `/` previously stopped the poll loop for
  every repo after it), APK verification now covers every signer rather than
  only the first, the login rate limiter no longer grows without bound,
  `/login` gets an Origin check, base images are pinned by digest, the app
  container runs read-only, IPv6 device addresses parse, and the database has
  indexes on the columns it joins and sorts by.

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
