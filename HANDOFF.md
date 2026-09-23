# Handoff: ADB Server (APK Pusher) — v1.0.0 shipped

**Goal:** v1.0.0 is released. Next work is whatever the user asks for
(post-release follow-ups below).

**Current state (2026-09-23, end of session 3):**
- `main` is the default branch, at `41b4647` (PR #5), two work-commit PRs past
  the release. Tag `v1.0.0` → `8cc08ec` (PR #1). Release run succeeded; GitHub release published;
  `ghcr.io/darthrater78/adb-server/{app,adb-server,mdns}:1.0.0` pullable.
  Older tags `v0.1.0` (3c600ae), `v0.2.0` (03c2235) exist.
- Local checkout is on `main`, in sync. Uncommitted, on purpose:
  `.claude/dev-skills-gates.md` (carries the v1.0.0 SHIP ✅ line; folds into
  the next release PR — never a bookkeeping-only PR).
- Unreleased on `main` (CHANGELOG `## Unreleased`): **breaking** — data moved
  from named volumes to bind mounts under `/opt/docker/adb-server`
  (`adbkeys`, `appdata`, `mdns`), owned by uid 10001, `adbkeys`/`appdata`
  chmod 700. Compose comments now sit in a Notes block after the services.
  The next release needs a version decision (breaking → major by the commit
  signal; the user decides).
- 319 tests pass. No system pytest: make a venv, `pip install -r
  requirements-dev.txt` (now includes `mdns/requirements.txt`), then
  `bash scripts/test.sh`.
- Private vulnerability reporting is enabled on the repo (README points at it).

**Gate status:** v1.0.0 complete. Session 3 was work commits only (PR #4, #5
merged; RELEASE/SHIP ➖ N/A). 🔒 0 open. A new change starts a fresh track.

**Mode:** this session ran semi-autonomous. The next session must ask again.

**Done in session 3:**
- PR #4: bind mounts + compose comments moved below the block; README
  quickstart creates/chowns the dirs; CHANGELOG entry with migration commands.
  Tested with a throwaway compose project using `!override` volumes pointing at
  scratch dirs chowned to 10001.
- PR #5: `tests/test_upload.py` `_apk_bytes()` used a wall-clock zip
  timestamp, so the duplicate-upload test flaked across a 2 s tick → fixed
  `ZipInfo.date_time`.

**Done this session:**
- Full top-to-bottom security review: 5 Low fixed with regression tests (TOTP
  race → atomic `db.increment_meta`/`advance_meta`; legacy re-pair clears
  trust; legacy record never merges into another registered phone;
  `pushes.run_push` re-checks trust; mdns listener evicts oldest when full),
  plus the dev-server `.env` 0664 → 0600.
- README rewritten: architecture, every feature, detailed Security section,
  15 screenshots in `docs/screenshots/` (mock data only).
- Header links: version → `…/releases/tag/v<VERSION>`, plus a GitHub link
  (`main.REPO_URL`, `RELEASE_NOTES_URL`). `_read_version` falls back to the
  repo-root `VERSION` outside the image.

**Waiting on the user:**
1. Production (a different server, no access from here): pulling current
   `main` needs the data copied out of the named volumes first, or the adb key
   (every pairing) and the DB are lost — commands in CHANGELOG `Unreleased`
   and the PR #4 description. Then `docker compose up -d`.
2. Test stack `adbtest` (port 18080, the user's phone is paired to it) is
   still running — ask before `docker compose -p adbtest down -v`.
   `logtest-logger-1` is not ours; leave it.

**Open questions (not blocking):**
- Encrypt the TOTP secret / notification URLs at rest (key from `SECRET_KEY`,
  would add `cryptography`)?
- Use mDNS connect announcements in `discovery.ensure_connected` before
  port-scanning (faster Find)?
- CI step running `adb` inside the app image (catches the read-only
  `~/.android` regression)?

**Lessons:**
- The auto-mode permission check blocks creating `main` / changing the default
  branch from here — hand those to the user.
- Screenshots: throwaway compose project `adbshots` with `env_file: !reset []`
  (never the real `.env`), fresh volumes, seeded by piping a script into
  `docker compose exec -T app python -`; capture with
  `mcr.microsoft.com/playwright/python:v1.62.0-jammy` (`--network host`).
  Rewrite `172.*` client IPs in the DB before shooting Audit/Settings.
  Take them once, after all UI changes.
- The app container is `read_only`; `adb` needs the tmpfs `~/.android`.
- `request_body_guard` in `main.py` caps every POST before parsing — keep it.
- `adb connect` returning "connected" ≠ ready; wait for `get-state`.
- mDNS needs host networking; the adb server must never have it.
- In tests, `client` and `authed` are the same TestClient.
- Test zips must use a fixed `ZipInfo.date_time`, or hashes change per tick.
- `gh pr merge` can fail with a transient GraphQL error: check the PR state
  before retrying.
- Compose merges `volumes`/`ports`/`env_file` in overrides by appending; use
  `!override` in a throwaway override file to swap them for tests.

**Decisions made:** no client-side JS (CSP `script-src 'none'`); code-paired
devices start untrusted, QR-paired are trusted; re-pairing a legacy record
clears trust; debug-signed APKs refused from the poller, allowed and flagged
on upload; MFA lockout is global; plain HTTP on LAN is an accepted, documented
default.

**Shell environment:** Linux Terminal (bash).

**Next step:** ask the user what's next (a release for the bind-mount change,
production migration, `adbtest` teardown, or one of the open questions).
