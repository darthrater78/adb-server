# Dev Skills gate state
Track: release sequence → v3.4.0 (warn when adb-server and the web app are from different releases)
Mode: semi-autonomous (approved 2026-09-24, session 8) — commits and the tag still require the user's approval
Model: Opus 5.5 approved by user (session 8, current task)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ (+ version_mismatch event) · compose ✅ §10.5 (adbinfo bind mount in setup line + bottom comments)
Version: 3.4.0
Updated: 2026-09-24 (session 8: v3.3.0 shipped; v3.4.0 version check built on feat/adb-version-check)

## Current: v3.4.0 (feat/adb-version-check) — adb-server/app release mismatch warning
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
  test artifact: ⬜ build from the exact PR head commit before merge
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
📦 RELEASE    ⏳ commit + release notes approved (same message); notes = CHANGELOG 3.4.0; next: commit, push, PR
🚀 SHIP       ⬜

## Previous: v3.3.0 (feat/repo-legitimacy) — source verification, workflow artifacts, secrets at rest, audit fixes
🔢 VERSION    ✅ 3.3.0 MINOR (user: "commit here", 2026-09-24); prior tag v3.2.0 → 1ddd87f
🔨 BUILD      ✅ 520 pytest passed; full smoke in stack t33a (see commit 59713f0 message); handoff offered
  test artifact: adb-server-test/app:59713f0 (7538569eeb7a), adb-server-test/adb-server:59713f0 (75a2eb3ae657)
              @ 59713f0 (PR #13 head, git archive): smoke 21/21 pass
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — full audit, 12 found, 12 fixed (M1 per-source signing keys, H1 no host networking, L1–L7, Q1–Q2, W1)
📄 DOCS       ✅ CHANGELOG 3.3.0, README, .env.example, HANDOFF, screenshots
📦 RELEASE    ✅ commit + release notes approved (user: "yes, commit and ship it", 2026-09-24)
🚀 SHIP       ✅ v3.3.0 shipped 2026-09-24 — PR #13 MERGED → 28f3d33 (tree == tested 59713f0); CI success on merge
              tag v3.3.0 → 28f3d33 (user-driven, ls-remote); release run 36050159229 success; release published, Latest
              ghcr app + adb-server :3.3.0 pullable; app:latest = 3.3.0 digest (d059bf4c)
              branch feat/repo-legitimacy deleted (user-driven, ls-remote empty)
