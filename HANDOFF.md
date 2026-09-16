# Handoff: adb-server (APK Pusher)

**Goal:** Self-hosted system that watches GitHub repos for new APK releases,
verifies them, and pushes them to wireless-ADB-paired Android devices.

**Current state:** v0.2.0. Full scaffold running as two Docker containers
(`adb-server` for the ADB protocol, `app` for FastAPI + SQLite + poller),
live-tested end-to-end including a real push to a real paired device
("Dad"). Stack is up at `http://10.0.0.252:8080` (LAN) and
`http://127.0.0.1:8080`.

Since the 0.1.0 scaffold, this version added:
- Push is now a background job. Submitting a push redirects to
  `/installs/<id>`, a no-JS status page (`<meta http-equiv="refresh">`) that
  shows an animated CSS progress bar through pending → installing →
  success/failed. A crash mid-install always resolves the install row
  (`main.py::_run_push` catches broad `Exception`, not just `AdbError`) so a
  push can't get stuck "installing" forever.
- Staged APKs capture and display each release's GitHub release notes
  (`staged_apks.release_notes`, auto-migrated — see `db.py::_migrate`).
  Existing rows staged before this feature have `NULL` notes; only new
  releases populate it. Not backfilled.
- Repos page got a visual pass: status badges, tag pills, a
  watched/error-count summary line. Same table layout, not a redesign.
- The running version shows in the page header, read from `VERSION` at
  container build time.
- Theme switcher: Flashbang (light) / Dark / OLED black, chosen via a
  CSRF-protected `POST /theme` form, stored as a plain (non-httponly) cookie.
  No cookie set → falls back to the OS `prefers-color-scheme`, same as
  before. No client-side JS anywhere — CSP is still `script-src 'none'`.

**Gate status (dev-skills, `.claude/dev-skills-gates.md`):**
```
🔢 VERSION    ✅ 0.2.0 (prior-version-tag check failed — v0.1.0 was never
              tagged, because nothing has ever been pushed to origin, not
              even a branch. Accepted explicitly: user is staying in dev.)
🔨 BUILD      ✅ container rebuilt and smoke-tested after every change
🔒 SECURITY   ✅ 0 Critical, 0 High across all diffs this session
📄 DOCS       ✅ CHANGELOG has a 0.2.0 entry; README's stale LAN-binding
              claim was fixed to match docker-compose.yml (bound 0.0.0.0,
              plain HTTP, deliberately)
📦 RELEASE    🚫 blocked — no main branch on GitHub, nothing pushed to
              origin at all yet
🚀 SHIP       🚫 blocked — depends on RELEASE
```

**Key files:**
- `app/main.py::_run_push` — background push job (Starlette threadpool via
  `BackgroundTasks`, keeps the single-worker event loop free for the
  poller); `_tctx` / `_get_theme` — theme cookie resolution;
  `KNOWN_NAV_PATHS` — exact allow-list guarding the `/theme` redirect
  against open-redirect via the `next` field.
- `app/db.py::_migrate` — the pattern for adding a column to an
  already-deployed SQLite DB (`CREATE TABLE IF NOT EXISTS` never adds
  columns to an existing table).
- `app/templates/install_status.html` — the no-JS auto-refresh + progress
  bar pattern, reusable for any future long-running job.
- `app/static/style.css` — `:root[data-theme="..."]` blocks are more
  specific than the plain `:root` / `prefers-color-scheme` blocks they
  override, so explicit choice always wins over OS default.
- `app/Dockerfile`, `docker-compose.yml` — `app` build context is now the
  repo root (`context: ., dockerfile: app/Dockerfile`), not `./app`, so the
  image can `COPY VERSION .`. `.dockerignore` already excludes `.git`,
  `.env`, `*.md` from that wider context.

**Decisions made:**
- No main branch on GitHub yet, nothing pushed to origin at all — user is
  staying in dev; default to the work-commit track, don't push toward
  release/PR/merge/tag unless asked.
- CSP stays `script-src 'none'` — every feature this session (progress bar,
  theme switcher) was deliberately built without any client-side JS to
  preserve that.
- LAN exposure over plain HTTP is an explicit, accepted trade-off, not an
  oversight — see the `docker-compose.yml` comment and the README security
  note.
- Single-worker uvicorn is required (APScheduler runs in-process); the new
  background push job relies on Starlette running sync callables in a
  threadpool rather than on the event loop, so it doesn't reintroduce a
  blocking-call regression.

**Shell environment:** Linux Terminal (bash/zsh)

**Next step:** No specific next task queued. If picking this up cold: check
`.claude/dev-skills-gates.md` for current gate state, and `git status` /
`git log` since this file can go stale the moment more work happens.
