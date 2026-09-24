# Dev Skills gate state
Track: release sequence → v3.5.0 (install tracking on Status + collapsible Install page); v3.6.0 = B/C redesign (user: two releases)
Mode: semi-autonomous (approved 2026-09-24, session 10: "opus, auto") — commits and the tag still require the user's approval
Skill: v2.28.0 ⚠️ outdated (latest v2.38.0); user chose to continue (session 10)
Model: Opus 5.5 approved by user (session 9; re-approved session 10, v3.5.0 install tracking)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ (+ version_mismatch event) · compose ✅ §10.5 (adbinfo bind mount in setup line + bottom comments)
Version: 3.5.0 (bumped, uncommitted)
Updated: 2026-09-24 (session 10: gates 1-4 run; awaiting commit approval)

## Current: v3.5.0 — install tracking + collapsible Install page
🔢 VERSION    ✅ 3.5.0 MINOR (feat, no breaking change); bump confirmed with the commit approval
              VERSION, compose.yaml images + comment, README link + compose block + upgrade note, CHANGELOG
              prior tag v3.4.0 → a7df688 on origin (ls-remote)
🔨 BUILD      ✅ working tree: 574 pytest passed (28 new: test_install_tracking, test_install_page); handoff offered
              screenshot stack (image from the tree, seeded, GitHub mocked) on 10.0.0.252:18190, adbinfo = VERSION:
              Status origin chips (Release / Test build + run + Debug / likely / Not from this server),
              Install collapsed cards + Pushing-to picker, phone Install + Status checked by eye; container removed
              PR-head images on 10.0.0.252: ⬜ after push (Gate 5)
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              pip-audit --require-hashes clean; bandit -ll clean; Dependabot alerts on, 0 open; actionlint clean
              (no local shellcheck: CI lint covers run: blocks)
              review: ?to= only selects among trusted serials; origin text autoescaped, run link fixed github.com
              prefix; SQL parameterized; lastUpdateTime strict regex; CSRF on every push form; trust rechecked at push
📄 DOCS       ✅ CHANGELOG 3.5.0; README Status (where it came from) + Install (collapsed cards, picker), phone-install
              shot; screenshots retaken at 3.5.0 (harness now mounts adbinfo, no version banner)
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Previous: v3.4.0 (feat/adb-version-check) — adb-server/app release mismatch warning
🔢 VERSION    ✅ 3.4.0 MINOR (user: "yes update the quickstart/readme with the new directory", 2026-09-24)
              VERSION, compose.yaml images + comment, README release link + compose block, CHANGELOG all 3.4.0;
              prior tag v3.3.0 → 28f3d33 on origin
🔨 BUILD      ✅ working tree: 546 pytest passed (25 new in test_versions.py), tree unchanged during run; handoff offered
              images 340-wip built from the tree (adb-server now from repo root, copies VERSION)
              stack t340 on 10.0.0.252:18186, adbinfo shared (rw adb-server, ro app):
              A both 3.4.0 → no banner, Settings 3.4.0/3.4.0, protocol 41/41; app can't write adbinfo
              B1 released adb-server:3.3.0 + mount → "hasn't published its version, older than 3.4.0" banner
              B2 adbinfo says 3.3.0 → "runs 3.3.0, web app is 3.4.0" banner
              C no mount → "Can't tell… isn't mounted" banner; adb-server logs it and still serves `adb devices`
              (first B1/C run had a broken helper, adb-server never started; re-run correctly, results above)
  test artifact: adb-server-test/app:22e362f (bc0bfdc6a09b), adb-server-test/adb-server:22e362f (0f087ea3353d)
              @ 22e362f (PR #14 head, git archive): stack t340 on 10.0.0.252:18186, no banner, 3.4.0/3.4.0, protocol 41/41
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              versions.py: file read capped at 65 bytes, strict version regex, rendered escaped; adb socket internal,
              3s timeout; checks run in the scheduler thread, never in a request; no new dependency
              ✅ fixed: 💡 entrypoint cp followed a symlink at .version.tmp (needs host write) → rm first; mv replaces links
              adbinfo holds only a version, app mounts it :ro (verified); pip-audit --require-hashes clean; bandit -ll clean
📄 DOCS       ✅ CHANGELOG 3.4.0 (+ upgrade step: create adbinfo, add 2 volume lines); README: quickstart setup line,
              data-directory table, compose block + comments, "Both containers on one release" section, data-at-rest row,
              notifications + NOTIFY_EVENTS; .env.example events
              SECRET_KEY 32 → 64 requested then withdrawn by user ("disregard the 32 to 64"): reverted, no diff left
              + README quickstart "Upgrading from 3.3.0 or earlier" step for adbinfo (user asked with the approval)
📦 RELEASE    ✅ commit + release notes approved (same message); notes = CHANGELOG 3.4.0
              branch synced (0 behind origin/main); 22e362f pushed; PR #14 open, all checks green
🚀 SHIP       ✅ v3.4.0 shipped 2026-09-24 — PR #14 MERGED → a7df688 (tree == tested 22e362f); CI success on merge
              tag v3.4.0 → a7df688 (user-driven, ls-remote); release run 36059676301 success; release published, Latest
              ghcr app + adb-server :3.4.0 pullable; app:latest = 3.4.0 digest (ec01add7)
              duplicate run 36059677677 (same push second) failed only at "Create GitHub release": 422 tag_name exists
              → fix in v3.5.0: concurrency group + idempotent release step
              branch feat/adb-version-check deleted (user-driven)
