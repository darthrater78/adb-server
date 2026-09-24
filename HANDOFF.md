# Handoff: ADB Server (APK Pusher) — v3.3.0 committed, not yet shipped

**Goal:** ship v3.3.0: source verification, workflow-artifact test builds,
secrets encrypted at rest, and the Settings/Install/Sources rework. Everything
is built, tested and committed on `feat/repo-legitimacy`; the release
sequence (push, PR, test images, merge, tag) hasn't started.

**Current state (2026-09-24, end of session 7):**
- Shipped this session: **v3.2.0** (artifact-zip upload, `aapt` → `aapt2`),
  tag `v3.2.0` → `1ddd87f`, release published, ghcr images pullable.
- `feat/repo-legitimacy` (local only, **not pushed**) holds one commit with
  all of v3.3.0: VERSION, `compose.yaml`, README and CHANGELOG are at 3.3.0.
  The CHANGELOG 3.3.0 section is the release notes.
- 498 tests pass (`bash scripts/test.sh` in a `python:3.13-slim` container).
  bandit: only the 6 known B608 in `db.py`; `pip-audit --strict` clean.
  New dependency: `cryptography==50.0.1`. The image also gains `zipalign`
  and `lib64/libc++.so`.
- Screenshots: all 16 retaken from invented data with GitHub mocked
  (`scripts/screenshots/run.sh 10.0.0.252`).
- A test stack is running for the user: `t330-app` + `t330-adb` on network
  `zip330_net`, http://10.0.0.252:18183, throwaway creds (in-memory data,
  the user's `gh` token in its env and saved in its Settings). Remove it when
  the user is done: `docker rm -f t330-app t330-adb; docker network rm zip330_net`.

**Gate status (`.claude/dev-skills-gates.md`):** 🔢 ⏳ 3.3.0 set, bump
confirmed by the commit approval · 🔨 ✅ (working tree; the **test artifact
from the PR head is still owed before merge**) · 🔒 ✅ 0 open · 📄 ✅ ·
📦 ⏳ commit approved and made, PR not opened · 🚀 ⬜.

**Mode:** this session ran semi-autonomous (on Opus 5.5, user-approved). The
next session must ask again.

**New in v3.3.0 (where to look):**
- Source verification: `github_client.get_repo_info` / `uploader_allowed`,
  `poller._check_identity` (runs only for a new release or an unpinned repo),
  `routes_sources.review_repo` / `confirm_repo` / `_apk_evidence`.
- Builds page (`/repos/{id}/artifacts`, `routes_builds.py`, `artifacts.html`): past releases
  (`poller.stage_past_release`), test builds grouped by commit with messages,
  signing badges (`artifact_signing` table, Check signing), Refresh.
- Artifacts: `github_client` list/get/download, `artifact_lists_apk` (zip
  file list by range requests), build notes, siblings + debug advice.
- Unsigned opt-in: `app/signing.py` (one key per source: `github-<repo ID>`
  or `uploads`; keytool + zipalign + apksigner, password via env only),
  `apk_verify.is_unsigned`.
- Secrets: `app/secretbox.py`, `db.get_secret` / `set_secret`, startup sealing.
- Settings split: `settings.html` (overview) + `settings_{general,security,
  notifications,github,appearance}.html`; token template URL, expiry warning
  (`poller._warn_token_expiry`), per-repo access check.
- Display: `web.when` (time zone + 12/24-hour), `web.device_name` (model over adb).
- Layout: `main.py` is the app, middleware and scheduler only; pages live in
  `routes_{auth,sources,builds,install,devices,settings}.py`, shared page
  helpers in `web.py`, upload staging in `uploads.py`.

**Waiting on the user:**
1. Go-ahead to run the rest of the release: push, PR, test images from the PR
   head, merge on green CI, then the tag block (the tag push is the user's).
2. Pair the phone again with the test stack if they want to try pushes (its
   data is in memory and was reset on every rebuild).
3. Production (a different server): upgrading to 3.3.0 encrypts existing
   secrets on first start. After that, **changing `SECRET_KEY` means
   re-entering the token and notification services and `mfa_admin.py reset`**.
   Back up `appdata` and `.env` together.
4. Dependabot PRs #2/#3 (python 3.13 → 3.14 base images) are still open.

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
  Builds, checkbox on upload), then signed with the server's own key.
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
