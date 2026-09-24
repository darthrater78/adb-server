# Changelog

## 3.4.0 — 2026-09-24

- Added: **a warning when the two containers are from different releases.**
  The adb-server image now writes its version into a new `adbinfo`
  directory that the app mounts read-only. The app also compares the adb
  server's protocol version with its own adb client's. Either mismatch, or a
  missing `adbinfo` mount, shows a warning with the fix on every page and
  sends the new **version mismatch** notification once. Settings → General
  shows both versions. Checked at start and every 5 minutes.
- Upgrading: create `/opt/docker/adb-server/adbinfo` (owned by 10001, like
  the other two) and add its two `volumes:` lines from this release's
  `compose.yaml`. Until then, the app warns that it can't tell which version
  adb-server runs.
- Build: the adb-server image is built from the repo root, like the app, so
  both carry the same `VERSION`.

## 3.3.0 — 2026-09-24

- Added: **stage a test build straight from a watched repo's workflow
  runs.** **Artifacts** on a repo lists the builds its recent runs uploaded;
  **Stage** unwraps the zip and checks the APK like an upload, recording the
  run, branch and commit it came from. Its **build notes** on Install take
  the place of release notes: the run, the pull request's description if it
  was built for one, and the full commit message. Artifacts from pull requests opened
  from forks are never offered. Needs `GITHUB_TOKEN` with Actions: read.
- Changed: **Install groups its cards** into Releases, Test builds from
  workflow artifacts, and Uploads. A test build is badged **Artifact · test
  build** and shows its branch and commit, linked to the run.
- Changed: **devices are named by model** where they have no nickname
  (e.g. "Google Pixel 8 · …005KT") instead of a bare serial, in the device
  picker, on Status and on Devices. The model is read over adb alongside the
  CPU type.
- Added: **Builds** on a watched repo (was Artifacts) lists its past
  releases to stage an older one, through every release check, and its test
  builds grouped by commit with each commit's message. A release's own build
  is no longer offered as a test build. **Refresh from GitHub** fetches both
  fresh.
- Changed: **"latest" means the newest release by release date**, not the
  most recent download, so staging an older release never makes auto-update
  or Push latest send it.
- Added: **only repos with APKs can be added**: a release asset matching the
  glob, or (with a token) an artifact whose zip lists an `.apk`, checked from
  the zip's file list without downloading it.
- Added: **unsigned builds, by opt-in**: per repo (on Builds) or per upload,
  with an explanation, the server signs an unsigned build and marks it
  **signed by this server**. Each source gets its own key (one per watched
  repo, kept by its GitHub ID, and one for uploads), so one source's build
  can never pass as an update to another source's app. Settings → Security
  lists each key's fingerprint. Without the opt-in an unsigned build is
  refused with the reason. Adds `zipalign` to the image.
- Changed: **Check now** reloads the page until the check is done and says
  what it found; a repo with no release yet is "no release yet", not an
  error.
- Changed: on Sources, uploads and test builds are one table, each row
  badged **Artifact** (with branch and commit) or **Upload**.
- Security: the HTTP client no longer logs request URLs, which for a
  download include a signed storage link that works as a short-lived read
  token.
- Added: **signing badges** on every staged build: **signed**, **debug
  build** or **signed by this server**. A debug-signed test build whose commit
  also built something else is flagged **Better not install this debug
  build**, with a button to stage the other one.
- Added: **Settings → General** with the time zone and a 12-hour (default)
  or 24-hour clock for every date and time shown (stored in UTC; `TZ` from
  the environment is the default zone).
- Added: on Builds, each test build is badged by how it's signed: **signed ·
  same key as releases**, **signed · different key**, **debug build** or
  **unsigned**, with a note on which to pick. **Check signing** downloads a
  build to find out, then deletes it; staging records it too.
- Docs: every screenshot retaken from invented data with GitHub mocked, plus
  new ones for the repo review and the Builds page. `scripts/screenshots/`
  regenerates them.
- Added: **a repo is reviewed before it's watched.** Adding one shows what
  GitHub says it is (owner, created date, stars, ID) and warns about the
  signs of a look-alike: a fork, archived, under 30 days old, no release.
- Added: **watched repos are pinned by GitHub ID and owner.** If the name
  later points at a different repo, the repo is transferred, or it's
  renamed, nothing more is staged from it and you're notified. Existing
  repos are pinned on their first poll after upgrading.
- Added: **release assets must be uploaded by the repo's owner or its
  workflows** (`github-actions[bot]`); for an organization's repo, by its
  workflows only. A release with an asset from anyone else is rejected before
  it's downloaded.
- Changed: **Settings is split into pages.** `/settings` is now an overview
  with one card per area and its current state. Security, Notifications,
  GitHub and Appearance each have their own page. The notification add-forms
  fold away until needed.
- Added: **Settings → GitHub.** **Create a token on GitHub** opens GitHub's
  token page pre-filled with read-only Contents, Actions and Metadata and a
  90-day expiry; paste the token back and it's checked, saved encrypted and
  never shown again. It takes precedence over `GITHUB_TOKEN` in `.env`. The
  page shows when it expires (with a new **token expiring** notification a
  week before) and checks each watched repo for real: can the token read it,
  and download its artifacts. The artifact list can hide Docker build records
  and filter by name.
- Security: **secrets in the database are now encrypted at rest**: the
  GitHub token, the TOTP secret and saved notification URLs, with a key
  derived from `SECRET_KEY`. Existing values are encrypted on first start.
  Changing `SECRET_KEY` now also means entering the token and notification
  services again and resetting two-factor (`mfa_admin.py reset`). Adds the
  `cryptography` dependency.
- Docs: `GITHUB_TOKEN` waives the rate limit for unchanged repos only when
  set; the README said this happened without a token too.
- Security: **failed sign-ins from IPv6 are counted per /64**, so rotating
  through one network's addresses no longer buys more guesses. Behind a
  reverse proxy, the new `FORWARDED_ALLOW_IPS` setting names the proxy, so
  each browser is counted by its own address: before, every sign-in seemed to
  come from the proxy, and a few wrong passwords from anyone locked everyone
  out for 5 minutes.
- Security: a `SECRET_KEY` under 32 characters or an `APP_PASSWORD` under 12
  is now logged at startup and warned about on every page. The app still
  starts with them.
- Fixed: the audit log dropped "trusted for 30 days" from a sign-in with a
  recovery code.
- Security: Python dependencies are installed from a lockfile that pins every
  package, direct and transitive, with its hashes (`app/requirements.in` →
  `app/requirements.txt`), so the image holds exactly the tree CI audited.
  CI also runs `bandit` now.
- Changed: both images build on Debian 13 (trixie); the adb server image and
  the tool-fetching stage were still on Debian 12.
- Docs: `.env.example` pointed at Settings → Artifacts (now Settings →
  GitHub) and left `token_expiring` out of the notification events; the
  README still said the TOTP secret and notification URLs weren't encrypted.
- Internal: `main.py` is split into one module per area
  (`routes_*.py`), with shared page helpers in `web.py` and upload staging in
  `uploads.py`. No route changed.

## 3.2.0 — 2026-09-24

- Added: **upload a zip holding an APK**, such as the artifact a GitHub
  Actions run hands back, without unzipping it first. The zip must hold
  exactly one APK; anything else in it (`output-metadata.json`, a mapping
  file) is ignored. The APK inside goes through every check a bare upload
  does (signature, package, debug flag), and the zip is deleted as soon as
  the APK is out of it. The same APK uploaded bare and zipped counts as one.
- Fixed: APKs built against current Android SDKs could not be staged at all,
  uploaded or polled. The legacy `aapt` failed reading them ("ERROR getting
  'android:icon'"); package info now comes from `aapt2`, from the same pinned
  build-tools release.

## 3.1.0 — 2026-09-23

- Changed: a simpler flow. The top bar is now **Status · Sources · Devices ·
  Install · Settings**, in the order you set things up, down from eight tabs.
  - **Sources** replaces Repos and Upload: watch a GitHub repo and upload an
    APK on the same page, with one list of repos and one of uploads.
  - **Install** replaces Staged: one card per app with its latest version and
    a **Push** button that picks the right build for the device's CPU. Older
    versions and individual files are one click down.
  - **Status** is one card per device: every app on it (installed against
    latest, **Update** / **Install**, auto-update) plus uploads pushed to it,
    and its last few installs. Until everything is set up it shows a
    checklist: add a source, pair and trust a device, install an app.
  - **Devices** explains pairing step by step, and trust is a plain
    **Trust** / **Revoke** button.
  - Install history and the audit log moved to **Settings → Activity**.
  - The old addresses (`/repos`, `/upload`, `/staged`) redirect to the new pages.
- Changed: `docker-compose.yml` is now `compose.yaml`. It pulls the published
  images, pinned to the release, sets fixed container names, and keeps one
  comment line per setting at the bottom. Building from a checkout moved to
  `compose.build.yaml`
  (`docker compose -f compose.yaml -f compose.build.yaml up -d --build`).
- Docs: the quickstart no longer needs a clone. It treats the stack
  directory (`compose.yaml` and `.env`) and the data directory as separate
  places, which they often are. It generates `.env` from a copy-paste block
  with the server's IP and hostname in `ALLOWED_HOSTS` (the cause of "Invalid
  host header"), and explains each setting. Screenshots retaken in the Dark
  theme.

## 3.0.0 — 2026-09-23

- **Breaking:** removed QR-code pairing and the `mdns` container it needed.
  That container ran on the host network (mDNS multicast can't cross Docker's
  bridge), and no container in this stack may. Nothing runs on host
  networking any more. Pair with a pairing code instead. Finding a phone again
  after its port changes still works, by scanning its last known IP.
  To upgrade: pull the new compose file, run
  `docker compose up -d --build --remove-orphans`, then
  `sudo rm -rf /opt/docker/adb-server/mdns`. Paired phones and the database
  are untouched.
- Removed: automatic trust for QR-paired phones. Every newly paired device
  now starts untrusted, until you trust it on the Devices page.
- Removed: the `ghcr.io/darthrater78/adb-server/mdns` image is no longer
  built or published.
- Docs: the README now links to the repo and this version's release notes at
  the top.

## 2.0.0 — 2026-09-23

- **Breaking:** data now lives in bind mounts under `/opt/docker/adb-server`
  (`adbkeys`, `appdata`, `mdns`) instead of named volumes. Before recreating
  the stack, create the directories and hand them to uid 10001 (see the README
  quickstart), then copy the old volumes across, or the adb key (and with it
  every phone pairing) and the database are lost:
  `docker run --rm -v <project>_adbkeys:/src -v /opt/docker/adb-server/adbkeys:/dst alpine cp -a /src/. /dst/`,
  and the same for `appdata`. `mdns` needs no copy.
- Changed: the explanatory comments in `docker-compose.yml` moved to a Notes
  block after the services, so the compose block itself is clean.
- Fixed: a test that could fail at random (its sample APK carried the current
  time, so two copies made a second apart were not identical).

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
