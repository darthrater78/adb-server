# Handoff: ADB Server (APK Pusher) — v2.0.0 shipped

**Goal:** v2.0.0 is released. Next work is whatever the user asks for
(follow-ups below).

**Current state (2026-09-23, end of session 3):**
- `main` is the default branch, at merge commit `be123aa` (PR #7). Tag
  `v2.0.0` → `be123aa`; release run 35875285979 succeeded; GitHub release
  published and marked Latest; `ghcr.io/darthrater78/adb-server/{app,adb-server,mdns}:2.0.0`
  pullable, `:latest` moved to 2.0.0. Older tags: `v1.0.0` (8cc08ec),
  `v0.2.0` (03c2235), `v0.1.0` (3c600ae).
- 2.0.0 is **breaking**: data moved from named volumes to bind mounts under
  `/opt/docker/adb-server` (`adbkeys`, `appdata`, `mdns`), owned by uid 10001,
  `adbkeys`/`appdata` chmod 700. Compose comments sit in a Notes block after
  the services. The README "Upgrading from 1.x" block has the migration.
- Local checkout is on `main`, in sync. Uncommitted, on purpose:
  `.claude/dev-skills-gates.md` (carries the v2.0.0 SHIP ✅ line; folds into
  the next release PR — never a bookkeeping-only PR).
- 319 tests pass. No system pytest: run them in a container (`python:3.13-slim`,
  `pip install -r requirements-dev.txt`, `bash scripts/test.sh`) or a venv.
- Private vulnerability reporting is enabled on the repo (README points at it).

**Gate status:** 🔢 ✅ 🔨 ✅ 🔒 ✅ 0 open 📄 ✅ 📦 ✅ 🚀 ✅ — v2.0.0 complete.
A new change starts a fresh track (work commit or release sequence).

**Mode:** this session ran semi-autonomous (on Opus, user-approved). The next
session must ask again.

**Done in session 3:**
- PR #4: named volumes → bind mounts; compose comments moved below the block;
  README quickstart creates/chowns the dirs.
- PR #5: flaky duplicate-upload test fixed (fixed `ZipInfo.date_time`).
- PR #6: handoff update. PR #7: release 2.0.0 (VERSION, dated CHANGELOG,
  README "Upgrading from 1.x").

**Waiting on the user:**
1. Delete merged branches `release/v2.0.0` and `docs/handoff-session-3` on
   origin (ref deletions are the user's).
2. Production (a different server, no access from here): follow the README
   "Upgrading from 1.x" block before `docker compose up` on 2.0.0, or the adb
   key (every pairing) and the DB are lost.
3. Test stack `adbtest` (port 18080, the user's phone is paired to it) still
   runs on the old named volumes. Recreating it from current `main` without
   migrating loses its pairing. Ask before `docker compose -p adbtest down -v`.
   `logtest-logger-1` is not ours; leave it.
4. Dependabot PRs #2/#3 (base images python 3.13 → 3.14) are open. Not
   security fixes; CI pins 3.13 to match the images, so bump both together.

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

**Next step:** ask the user what's next (production migration, the Python
3.14 Dependabot PRs, `adbtest` teardown, or one of the open questions).
