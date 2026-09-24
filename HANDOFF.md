# Handoff: ADB Server (APK Pusher) — v3.3.0 shipped, v3.4.0 in progress

**Goal:** ship v3.4.0: warn when the adb-server container and the web app are
from different releases.

**Current state:**
- v3.3.0 is released (tag → 28f3d33, images on ghcr, GitHub release Latest).
- v3.4.0 on `feat/adb-version-check`, uncommitted: `app/versions.py` (image
  version from the `adbinfo` bind mount + adb protocol check, run every 5 min
  by the scheduler, banner on every page, `version_mismatch` notification,
  Settings → General "Versions"), `adb-server/entrypoint.sh` (publishes
  VERSION into /adbinfo), adb-server built from the repo root. 546 tests pass;
  checked in a real stack for match, old image, published-older and no-mount.

**Gate status:** `.claude/dev-skills-gates.md` — VERSION ⏳ (3.4.0 awaiting the
commit approval), BUILD ✅ (PR-head test artifact still owed), SECURITY ✅,
DOCS ✅, RELEASE ⬜, SHIP ⬜.

**Mode:** this session ran semi-autonomous on Opus 5.5 (user-approved). The
next session asks again.

**Next step:** show the user the v3.4.0 commit checkpoint (diff, message,
version 3.4.0, release notes = CHANGELOG 3.4.0); on yes: commit, push, PR,
PR-head test images, merge on green CI, then the tag block for the user.

**Also open:** test stacks t33b (18184) and t340 (18186) still run; tear down
when the user says. Dependabot PRs #2/#3 (python 3.14 base images).

**Open questions (not blocking):**
- CI step running `adb` inside the app image (catches the read-only
  `~/.android` regression)?
- GitHub build attestations (strongest proof an APK came from the repo's own
  CI): proposed, not built.

**Lessons:**
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

**Next step:** ask the user for the go-ahead on the v3.3.0 release sequence
(Gate 5: push `feat/repo-legitimacy`, open the PR, then the PR-head test
images on `10.0.0.252`).
