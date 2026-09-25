# Handoff: ADB Server (APK Pusher) — v3.5.0 shipped; next: v3.6.0 redesign

**Goal:** v3.6.0 (branch `feat/redesign`): the B/C redesign below ("looks
like just another vibe app", mobile subpar). v3.5.0 shipped: Status shows
where each installed version came from (Release / Test build / Upload / Not
from this server, Debug, "likely" for pre-3.5.0 pushes); Install cards
collapsed with Details; one "Pushing to ▾" picker (links, `?to=<serial>`,
default = most recently paired trusted device); idempotent release step.

**How tracking works:** `device_packages.origin` (JSON copy of the pushed
staged APK: kind, ref, repo, run, debug, version, `at`, the device's
`lastUpdateTime`) is written by `db.set_package_origin` after each successful
push (`pushes.run_push`), cleared when the device reports the package
uninstalled, and judged by `selection.origin()`: version or lastUpdateTime
differ → "other". `_backfill_origins` runs once (meta `origins_backfilled`).

**Design decided by the user (2026-09-24), from the mockup canvas
https://claude.ai/artifact/MKzyu4SLxeUaUWKDUK7Tj5 (private, user's):**
- **Desktop: direction B, "Refined cards"**: Figtree + JetBrains Mono, warm
  neutral ground (#f6f5f2), white cards with soft 1px borders (#e4e2dc) and
  14px radius, no colored stripes, deep teal primary (#0f766e, white text),
  one soft pill style for source chips (Release teal, Test build blue, Upload
  and "Not from this server" neutral, Debug amber), app rows as grid lines
  inside a device card. See artboards `B-Status-Desktop`, `B-Install-Phone`.
- **B also needs a dark theme** (user: "we also need a dark theme"). Not
  mocked yet. Keep the app's existing Flashbang / Dark / OLED switch; OLED =
  the dark palette on #000.
- **Phones: direction C's layout** (user: "C is perfect for mobile"): bottom
  tab bar (Status, Sources, Devices, Install, Settings with stroke icons),
  big 44px+ touch rows with a letter avatar, a summary card on Status
  ("1 update ready" + Update), device pill switcher, sticky "Pushing to ▾"
  picker and filter pills on Install, expanded row inline. Artboards
  `C-Status-Phone`, `C-Install-Phone`. C was mocked in its own fonts and
  colors (Sora/Nunito, orange); the intent is **C's layout in B's type and
  palette**, so the app is one design across breakpoints.
- Still no client-side JS (CSP `script-src 'none'`): expand/collapse with
  `<details>`, theme via the existing cookie form. Directions A (quiet flat
  rows) was not chosen.

**Before building v3.6.0:** check the canvas for four confirmation mockups
(B dark Status + Install desktop with one row expanded; C's phone layout in
B tokens, Status light, Install dark). Session 9 had not written them; make
them if missing and get the user's OK first.

**Current state:**
- v3.5.0 shipped and verified 2026-09-24: PR #15 merged → 66ff77a, tag
  v3.5.0 → 66ff77a, one release run (success), release Latest, ghcr
  `:3.5.0` both images, `app:latest` = 3.5.0. `feat/install-tracking` deleted.
- `feat/redesign` is branched from main (66ff77a) with this file and the gate
  file (v3.5.0 RELEASE/SHIP records) ahead of it; they ship in v3.6.0's PR.
- Screenshot shots now include `phone-install`; the redesign must retake all.
- The user's own server still needs the v3.4.0 adbinfo step if not done:
  create `/opt/docker/adb-server/adbinfo` (10001, 700), add the two volume lines.
- No test containers are running.

**Gate status:** `.claude/dev-skills-gates.md` — v3.5.0 all ✅. v3.6.0 not
started (all ⬜). Skill copy v2.28.0 is behind upstream v2.38.0 (user chose
to continue in session 10; offer the update again).

**Mode:** session 10 ran semi-autonomous on Opus 5.5 (user-approved). The
next session asks again (mode, model). In session 10 the auto-mode
permission check denied `gh pr merge` ("Merge Without Review"): the user
merges, or adds a permission rule for it.

**Next step:** confirmation mockups for v3.6.0 (see above), then build.

**Also open:** Dependabot PR #2 (python 3.14 app base image).

**Open questions (not blocking):**
- CI step running `adb` inside the app image (catches the read-only
  `~/.android` regression)?
- GitHub build attestations (strongest proof an APK came from the repo's own
  CI): proposed, not built.

**Lessons:**
- After a reboot `/tmp` is empty: the test venv goes in the scratchpad,
  `python3 -m venv <dir> && <dir>/bin/pip install -r requirements-dev.txt`,
  then `PATH=<dir>/bin:$PATH bash scripts/test.sh`.
- The screenshot harness mounts an `adbinfo` holding `VERSION`; without it
  every shot carries the version-mismatch banner. Retake after a VERSION bump
  (the header shows the version).
- SQL comments inside the `staged_apks` CREATE statement must not contain an
  apostrophe or a `;`: `_rebuild_staged_apks_unique` re-runs that statement
  and chokes on them ("incomplete input"). Other tables are fine.
- Azure blob storage (artifact downloads) ignores suffix ranges (`bytes=-N`)
  and sends the whole file. Ask `bytes=0-0` for the size, then explicit ranges.
- httpx logs every request URL at INFO, and download redirects are signed
  URLs that work as tokens. `main.py` keeps the `httpx` logger at WARNING.
- "Latest" for a repo is by `released_at` (release publish date), falling
  back to `downloaded_at`: staging a past release must never make it latest.
- Adding a `NOT NULL` column needs a default, and every new staged_apks column
  goes in both the schema and `_ADDED_COLUMNS`.
- A throwaway adb-server container needs its key dir owned by the image's
  user: `--tmpfs /home/adb/.android:uid=10001,gid=10001,mode=0700`. With the
  wrong uid adb still answers `adb devices` but never makes a key, and
  crashes on the first connect. Check `ls /home/adb/.android` shows `adbkey`.
- Test containers bind to the box's IP `10.0.0.252` with it in
  `ALLOWED_HOSTS` (user rule), never localhost.
- Anonymous GitHub requests count against the 60/hr limit even when they
  return 304; only authenticated conditional requests are free. Keep per-poll
  API calls to one for an unchanged repo.
- The auto-mode permission check blocks creating `main` / changing the default
  branch from here — hand those to the user.
- Screenshots: `bash scripts/screenshots/run.sh 10.0.0.252` retakes all of
  them from invented data (`seed.py`) with GitHub mocked (`mock_github.py`),
  Dark theme. Never shoot a stack with real repos, tokens or devices. Add a
  page or feature there and in `shoot.py` when the UI changes.
- The app container is `read_only`; `adb` needs the tmpfs `~/.android`.
- `request_body_guard` in `main.py` caps every POST before parsing — keep it.
- The dev-skills enforcement hook reads command *text*: a heredoc that merely
  mentions a container command gets blocked. Edit such files with the Edit tool.
- Gate-file changes go through the Edit/Write tools, never the shell.
- `adb connect` returning "connected" ≠ ready; wait for `get-state`.
- **No container ever runs on host networking** (user rule). mDNS/QR pairing
  was removed in 3.0.0 for that reason; don't bring back anything that needs it.
- In tests, `client` and `authed` are the same TestClient.
- Test zips must use a fixed `ZipInfo.date_time`, or hashes change per tick.
- `gh pr merge` can fail with a transient GraphQL error: check the PR state
  before retrying.
- Compose merges `volumes`/`ports`/`env_file` in overrides by appending; use
  `!override` in a throwaway override file to swap them for tests.

**Decisions made (all by the user, this session and before):**
- No client-side JS (CSP `script-src 'none'`): the Check now auto-refresh is a
  `<meta http-equiv="refresh">`.
- Artifact zips must hold exactly one APK; the zip is deleted as soon as the
  APK is out of it, before any check.
- Repos: reviewed before watching; pinned by GitHub repo ID + owner ID; release
  assets only from the owner or `github-actions[bot]`, and **organization
  repos only from `github-actions[bot]`**.
- Repos with no APK (release asset or artifact) can't be added.
- Debug-signed: allowed (flagged) for uploads and artifacts, **refused for
  releases**. Unsigned: refused unless opted in **per source** (repo switch on
  Builds, checkbox on upload), then signed with a key kept for that source
  alone (`signing/github-<repo ID>.p12`, `signing/uploads.p12`), never one
  shared key.
- Secrets in the DB are encrypted (secretbox): TOTP, notification URLs, GitHub
  token, signing-key password.
- Token: fine-grained, read-only, created via GitHub's template URL; saved in
  Settings → GitHub (takes precedence over `GITHUB_TOKEN`).
- Time: Settings → General, 12-hour default; timezone not under Appearance.
- Paired devices start untrusted; MFA lockout is global; plain HTTP on the LAN
  is an accepted, documented default.

**Shell environment:** Linux Terminal (bash).