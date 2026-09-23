# Handoff: adb-server (APK Pusher)

**Goal:** Self-hosted system that watches GitHub repos for new APK releases,
verifies them, and pushes them to wireless-ADB-paired Android devices.

**Current state:** `VERSION` is still 0.2.0; everything below sits in
`CHANGELOG.md` under **Unreleased** on branch
`claude/load-dev-skills-sl9dm3` (4 commits on top of
`feature/apk-pusher-scaffold`, pushed, **no PR yet**). CI is green on every
commit (tests + both image builds). 121 tests pass locally.

What those commits did:

| Commit | Content |
|---|---|
| `d3b783c` | Fixes: rejected release re-downloaded every poll (F1); apksigner/aapt blocked the event loop (F2); repo delete left staged files (F3); tag with `/` crashed polling for every later repo (F4). Staging retention (`KEEP_RELEASES_PER_REPO`). pytest suite. |
| `efb307a` | CI (`ci.yml`: tests + both image builds on every push/PR), checksum-verified actionlint, Dependabot (actions, pip ×2, docker), base images pinned by digest. |
| `092f12e` | Status page (now home), wireless-port rediscovery by scanning the device's last IP, per-ABI APK variants, signing-key rotation review. |
| `8dc47cb` | Apprise notifications, per-device auto-update, background Check now, pre-release opt-in, ETag polling, audit log, `/healthz` + Docker healthchecks. |

**Gate status (dev-skills, `.claude/dev-skills-gates.md`):**
```
Track: work commit · all four commits approved by the user
🔢 VERSION    ⬜ not owed — user staying in dev, no tags
🔨 BUILD      ⬜ not owed — CI builds both images; no Docker daemon in the web container
🔒 SECURITY   ✅ 1 open — 0 Critical, 0 High
              📝 open: Dependabot alerts repo setting unverified (no credential
                 reaches the endpoint) — blocks any release until confirmed/waived
📄 DOCS       ⬜ not owed — README, .env.example, CHANGELOG kept current
📦 RELEASE    ⬜
🚀 SHIP       ⬜
```
The next session must re-ask the dev-skills mode question; this session ran
semi-autonomous.

**Not verified — do this first on the real stack:**
- Nothing has run against a real phone since these changes (container had no
  Docker daemon or device). Smoke test: pair, toggle wireless debugging
  off/on then push (port rediscovery), Refresh on Status, stage a multi-APK
  release, turn on auto-update.
- `apksigner.signing_lineage` parses `apksigner lineage --in <apk>
  --print-certs` output that was never seen from the real tool. Wrong format
  fails closed ("no proof"), but confirm against a genuinely rotated APK.
- First start migrates the DB (rebuilds `staged_apks`). Tested against a
  0.2.0-shaped DB, but back up the `appdata` volume first.

**Key files:**
- `app/poller.py` — per-repo lock → `_check_repo`: download+verify each
  variant (`_download_and_verify`), consistency + pin check (`_check_pin`),
  `_stage`, prune, notify, spawn auto-update. `_Rejected.permanent=False`
  keeps download failures retryable.
- `app/pushes.py` — the only push path: `create_install` (trust, pruned, ABI
  checks) and `run_push`; `auto_push_targets` for auto-update.
- `app/discovery.py` — `ensure_connected`: stored addr → identity check →
  port scan of the same private IP (`ADB_SCAN_PORTS`). Never scans past a
  serial mismatch.
- `app/selection.py` — pure logic: `pick_variant`, `compatible`,
  `update_state`.
- `app/db.py::_rebuild_staged_apks_unique` — SQLite table rebuild with FKs
  off in the documented create-copy-drop-rename order (renaming the old table
  first would repoint `installs`' FK). `_ADDED_COLUMNS` is the pattern for
  new columns.
- `app/notify.py` — Apprise, best effort, never logs URLs (they hold tokens).
- `app/staging.py` — every delete re-checked to sit inside `STAGING_ROOT`.
- `tests/` — `conftest.py` sets env before import and chdirs into `app/`;
  GitHub/adb/apksigner are all stubbed.

**Decisions made:**
- mDNS discovery rejected: multicast doesn't cross the Docker bridge, and host
  networking would expose adb's unauthenticated port. Port scan chosen
  instead (user approved).
- Split `.apks` bundles not supported — would need bundletool as a new
  dependency; not added without asking.
- Notifications via Apprise (user's choice), pinned `apprise==1.13.1`.
- CI runs on every branch push while there's no `main`; narrow the triggers
  once `main` exists.
- `127.0.0.1` is always an allowed host (for the healthcheck); a literal
  loopback IP can't be used for DNS rebinding.
- Unchanged from before: CSP `script-src 'none'` (no client JS anywhere),
  LAN over plain HTTP is an accepted trade-off, single uvicorn worker is
  required (in-process scheduler; auto-update tasks live on that loop).

**Shell environment:** Linux Terminal (bash/zsh)

**Next step:** Smoke-test the branch on the real stack, then open a PR from
`claude/load-dev-skills-sl9dm3` into `feature/apk-pusher-scaffold`. For a
release: turn on Dependabot alerts at
https://github.com/darthrater78/adb-server/settings/security_analysis, bump
to 0.3.0, and run the release gates.
