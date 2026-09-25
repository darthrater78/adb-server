# Dev Skills gate state
Track: release sequence → v3.6.0 = B/C redesign on feat/redesign (building; confirmation mockups approved session 11)
Mode: semi-autonomous (approved 2026-09-24, session 11) — commits and the tag still require the user's approval
Skill: v2.28.0 ⚠️ outdated (latest v2.38.0); user chose to continue (session 11)
Model: Opus 5.5 (session 11, semi-autonomous approved)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ (+ version_mismatch event) · compose ✅ §10.5 (adbinfo bind mount in setup line + bottom comments)
Version: 3.5.0 (next 3.6.0)
Updated: 2026-09-24 (session 10: v3.5.0 shipped and verified; handed off)

## Current: v3.6.0 — B/C redesign
🔢 VERSION    ✅ 3.6.0 MINOR (UI redesign, no breaking change; routes/forms unchanged, new optional ?show=); bump to be
              confirmed with the commit approval. VERSION, compose.yaml images + comment, README link + compose block
              + upgrade note, CHANGELOG
🔨 BUILD      ✅ working tree: 580 pytest passed (6 new: filter pills, target state, summary card; markup asserts
              updated for new classes/labels); screenshot stack (image from the tree, seeded, GitHub mocked) on
              10.0.0.252:18190: all pages retaken at 3.6.0, no sideways scroll; checked by eye: Status + Install
              desktop dark and light, phone Status + Install dark and light, Devices, Sources, Settings, login
              (light review shots were temporary, removed); container removed
              ⬜ test artifact from the PR head (git archive) still to do after the commit
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              pip-audit --require-hashes clean (no dependency change); bandit -ll clean
              CSP unchanged (fonts self-hosted under /static/fonts, OFL licenses beside them; no external origin)
              review: ?show= only accepted from the kinds present; ?to= still only among trusted serials, urlencoded
              in links; the one new `| safe` is the nav's constant SVG paths from the template, no user data;
              every push/follow form keeps its CSRF token; confirm dialogs unchanged
📄 DOCS       ✅ CHANGELOG 3.6.0; README Status (fold, phone summary/switcher), Install (rows, target state, filter
              pills, Test build chip), Appearance (themes, fonts + license); all screenshots retaken at 3.6.0;
              phone shots now one screenful (full-page drew the fixed tab bar mid-image)
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Previous: v3.5.0 (feat/install-tracking) — install tracking + collapsible Install page
🔢 VERSION    ✅ 3.5.0 MINOR (feat, no breaking change); bump confirmed with the commit approval
              VERSION, compose.yaml images + comment, README link + compose block + upgrade note, CHANGELOG
              prior tag v3.4.0 → a7df688 on origin (ls-remote)
🔨 BUILD      ✅ working tree: 574 pytest passed (28 new: test_install_tracking, test_install_page); handoff offered
              screenshot stack (image from the tree, seeded, GitHub mocked) on 10.0.0.252:18190, adbinfo = VERSION:
              Status origin chips (Release / Test build + run + Debug / likely / Not from this server),
              Install collapsed cards + Pushing-to picker, phone Install + Status checked by eye; container removed
  test artifact: adb-server-test/app:73b6405 (ded0fb5d47e9), adb-server-test/adb-server:73b6405 (90f7cb1ac61e)
              @ 73b6405 (PR #15 head, git archive): stack t350 on 10.0.0.252:18187, login ok, 7 pages 200,
              no version banner, 3.5.0/3.5.0, adb devices ok, new columns + backfill marker present;
              upgrade: 3.4.0-image DB → 3.5.0: prior push = likely/release v2, never-pushed = other; torn down
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              pip-audit --require-hashes clean; bandit -ll clean; Dependabot alerts on, 0 open; actionlint clean
              (no local shellcheck: CI lint covers run: blocks)
              review: ?to= only selects among trusted serials; origin text autoescaped, run link fixed github.com
              prefix; SQL parameterized; lastUpdateTime strict regex; CSRF on every push form; trust rechecked at push
📄 DOCS       ✅ CHANGELOG 3.5.0; README Status (where it came from) + Install (collapsed cards, picker), phone-install
              shot; screenshots retaken at 3.5.0 (harness now mounts adbinfo, no version banner)
📦 RELEASE    ✅ commit + release notes approved (user: "commit", 2026-09-24)
              73b6405 pushed; PR #15 open, 0 behind main, all checks green (push + PR runs)
              (this row and the test artifact are local only: added after the commit; they ship with v3.6.0's PR)
🚀 SHIP       ✅ v3.5.0 shipped 2026-09-24 — PR #15 MERGED (user-driven) → 66ff77a; tree == tested 73b6405
              tag v3.5.0 → 66ff77a (user-driven, ls-remote peeled); release run 36074671662 success, single run
              release published, Latest; ghcr app + adb-server :3.5.0 pullable; app:latest = 3.5.0 digest (27508449)
              branch feat/install-tracking deleted (user-driven, ls-remote empty)

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
