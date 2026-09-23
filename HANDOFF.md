# Handoff: ADB Server (APK Pusher) — v1.0.0, scope grown, re-verify then ship

**Goal:** Ship v1.0.0 on a new `main` branch. Self-hosted app that watches
GitHub repos for APK releases, verifies them, and pushes them to trusted
wireless-ADB devices.

**Current state (2026-09-23, end of session):**
- Branch `feat/merge-17th-work` (local only). **Nothing committed** — all work
  is staged in the index. If anything changed, re-stage with
  `git add -A -- . ':!.claude/staged-apks-feedback-handoff.md'`.
- `VERSION` = 1.0.0; `CHANGELOG.md` `## 1.0.0 — 2026-09-23` is the release notes
  (already includes everything below).
- 319 tests pass. There's no system pytest: create a venv and
  `pip install -r requirements-dev.txt`, then `bash scripts/test.sh`.
- actionlint clean. bandit: only the previously withdrawn B608/B603/B607.
- **Gates:** BUILD and SECURITY need re-running for today's new scope (see
  `.claude/dev-skills-gates.md`, the source of truth). DOCS ✅. VERSION still
  blocked on the old tags.

**Added this session (after the previous handoff):**
1. **Device identity fix.** `adb get-serialno` returns `ip:port` for wifi
   devices, so Find/pushes never matched a phone after its port changed ("not
   found"). Now `adb_client.get_serialno` reads `getprop ro.serialno` (falls
   back to `ro.boot.serialno`). `install`/`installed_version`/`device_abis`
   take the **address**, not the serial. `discovery.ensure_connected` returns
   `(serial, addr)`. Legacy rows keyed by `ip:port` are re-keyed in place
   (`db.rename_device`, keeps nickname/trust/follows/history) on Find, push,
   Reconnect or re-pair from the same IP.
2. **Phone label.** `hostname: adbserver` on the adb-server service → phones
   list the server as `@adbserver` (adb builds `LOGNAME@HOSTNAME`; the "@" is
   unavoidable). Only visible after re-pairing.
3. **Desktop layout (>960px).** One line per table row, columns sized to
   content (`.table-wrap > table { width: auto }`), long names cut with `.clip`
   + title tooltip, page-level forms single-line. Tables scroll sideways in
   `.table-wrap` only below ~1100px. Phone card layout unchanged. The mid-width
   block (961–1180px) must stay *after* the desktop block in `style.css`.
4. **Saved colour pairs.** Settings → Appearance: name + "Save as preset",
   Saved row with apply and ×. Table `saved_colours`; swatches are served from
   `/accent.css` (the CSP forbids inline styles). `_redirect` now keeps `#fragment`.
5. **QR pairing.** New **`mdns` container** (`mdns/listener.py`, zeroconf) on
   `network_mode: host`. It only listens for `_adb-tls-pairing` and
   `_adb-tls-connect`, writes `/mdns/services.json`, and the app mounts it
   read-only. The user chose this over typing the phone's IP. `app/qr_pairing.py`:
   in-memory single-use sessions (3 min), `WIFI:T:ADB;S:adbserver-…;P:…;;`
   QR from segno. `/devices/qr/{token}` meta-refreshes every 2s and pairs when
   the name appears. **QR-paired phones are auto-trusted** (user asked for this;
   called out on the QR page and in the audit log). Code pairing still starts
   untrusted.
   - Bug found live and fixed: after pairing, the phone drops adb's old
     connection and adb's reconnect races ours. `adb_client.connect` now waits
     for `get-state == device` (10s). The QR path disconnects stale addresses
     first and retries the announced port once.
   - Verified live: the user's Pixel 10 paired by QR (before the auto-trust
     change, so it's currently untrusted and has no nickname; "Dad" was lost
     when it was Forgotten).
6. **TOTP MFA** (`app/mfa.py`, stdlib TOTP, RFC 6238 vector tested).
   - Settings → Security: setup with QR/key, then 10 recovery codes (hashed).
     Codes are single-use (replay-protected by `mfa_last_step`).
   - "Trust this browser for 30 days": signed cookie plus a hashed row in
     `mfa_trusted_browsers`, revocable one/all.
   - 5 wrong codes → global 15-min lock.
   - `docker compose exec app python mfa_admin.py status|unlock|reset`.
   - Enabling MFA signs out other sessions. Disabling or new recovery codes
     need a current code.
   - Tested end to end in the browser. The test stack's MFA was reset
     afterwards (it's **off** there).
7. CI/release/Dependabot now include the `mdns` image; pip-audit also reads
   `mdns/requirements.txt`. New pins: `segno==1.6.6` (app),
   `zeroconf==0.151.3` (mdns).

**Waiting on the user, in order:**
1. **Push the retroactive tags** (the version gate hard-blocks until v0.2.0 exists):
   ```
   git fetch origin
   git tag -a v0.1.0 3c600ae -m "v0.1.0"
   git tag -a v0.2.0 03c2235 -m "v0.2.0"
   git push origin v0.1.0 v0.2.0
   ```
   Verify with `git ls-remote --tags origin`.
2. ~~Re-run BUILD + SECURITY~~ **done 2026-09-23 (session 2)**: full top-to-bottom
   review, 5 Low found and fixed (see gate file), README rewritten with a
   Security section + mock-data screenshots in `docs/screenshots/`.
3. **Approve the release.** Re-present it with the current `git diff --cached
   --stat` and the CHANGELOG notes; scope has moved a lot.
4. Then, per the agreed plan: two commits
   (`ci: add tag-triggered release workflow and static checks` = `.github/` only;
   `feat: settings page, manual upload, mobile layout, branding and 1.0 security hardening`
   = the rest; consider retitling to mention MFA and QR pairing). Push the
   branch, create `main` on origin at `ef7bd7b` (`feature/apk-pusher-scaffold`
   head) and make it the default branch, open PR `feat/merge-17th-work` →
   `main`, wait for CI green.
5. **⛔ HOLD before merging:** the user asked to review the Apprise
   configuration first (Settings → Notifications).
6. After merge: pre-tag report + hand the user the `v1.0.0` tag block (tag
   pushes are always the user's). SHIP ✅ only once the tag is on the remote.

**Open questions for the user (not blocking):**
- Encrypt notification URLs / the TOTP secret at rest with a key derived from
  `SECRET_KEY` (would add `cryptography`)? Nothing is encrypted at rest today.
- Use mDNS connect announcements in `ensure_connected` before port scanning
  (faster Find)? Not done — offered only implicitly.
- CI step running `adb` inside the app image (catches the read-only
  `~/.android` regression)? Offered, not done.

**Lessons:**
- The app container is `read_only`; `adb` needs the tmpfs `/home/appuser/.android`.
- FastAPI parses the body before auth deps → `request_body_guard` in `main.py`
  caps every POST. Don't remove it.
- `adb connect` returning "connected" ≠ ready. Always wait for `get-state`.
- mDNS works only with host networking. Multicast never crosses the bridge.
- Checking out a branch that tracks `.claude/dev-skills-gates.md` overwrites
  the local (gitignored) copy.
- Never use the user's real Apprise address/key as example text (placeholder:
  `http://apprise.local:8000/notify/my-key`).
- In tests, `client` and `authed` are the **same** TestClient. Use a second
  `TestClient(main.app)` when simulating another browser.

**Key files:**
- `app/main.py` — routes: body guard, login + `/login/mfa`, devices incl. QR,
  settings (notify, appearance, MFA), `/accent.css`.
- `app/auth.py` — session/pending-MFA/trust cookies. `meta.session_epoch`
  revokes everything.
- `app/mfa.py`, `app/mfa_admin.py`, `app/qr_pairing.py`, `mdns/listener.py`.
- `app/adb_client.py`, `app/discovery.py`, `app/pushes.py`, `app/db.py`.
- `app/static/style.css` — tokens; desktop block, then mid-width, then phone ≤960px.

**Decisions made:**
- Name "ADB Server", subtitle "APK Pusher" (login page only).
- Debug-signed APKs: refused from the poller, allowed + flagged on manual upload.
- No client-side JS (CSP `script-src 'none'`): CSS `:target` dialogs,
  `formaction`, meta refresh for live pages.
- No box/button ever wraps its text. Desktop rows are single-line; stacking is
  phone-only.
- New devices start untrusted, **except** QR-paired ones.
- MFA lockout is global (not per IP); the recovery path is recovery codes,
  then the server-shell CLI.

**Environment:**
- Local session on `dev-server` (10.0.0.252). Production runs on a different
  server with no access from here. Back up its `appdata` volume before
  deploying 1.0 (the DB migrates on first start). Production also needs the new
  `mdns` container (`docker compose up -d` builds it).
- **Test stack running:** compose project `adbtest`, port 18080, volumes
  `adbtest_*` (incl. new `adbtest_mdns`). Start/rebuild with
  `docker compose -p adbtest -f docker-compose.yml -f <override> up -d --build`.
  The override (test login `tester`, port 18080) was at
  `/tmp/claude-1000/-home-serveradmin-adb-server/8c51ca2e-a785-443f-b8d2-709a04d94977/scratchpad/test-stack.yml`.
  Recreate it if it's gone (`APP_USERNAME`/`APP_PASSWORD` + `ports: !override ["18080:8080"]`).
  The user's phone is paired to it. Remove before ending the release work:
  `docker compose -p adbtest down -v`.
- README screenshots: throwaway `adbshots` compose project (fresh volumes, no .env, seeded
  with invented data), captured with `mcr.microsoft.com/playwright/python:v1.62.0-jammy`
  (`--network host`, `pip install playwright==1.62.0` inside).
- `.claude/staged-apks-feedback-handoff.md` (untracked) is fully superseded;
  ask before deleting.
- Mode: semi-autonomous this session; the next session must ask again.

**Next step:** confirm the v0.1.0/v0.2.0 tags on origin, then re-present the
release approval. Private vulnerability reporting: enabled 2026-09-23 (verified).
