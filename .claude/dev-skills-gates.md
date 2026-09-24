# Dev Skills gate state
Track: release sequence → v3.2.0 (zip upload + aapt2)
Mode: semi-autonomous (approved 2026-09-24, session 7) — commits and the tag still require the user's approval
Model: Opus 5.5 approved by user for the zip-upload feature + v3.2.0 (session 7)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest documented (README Data at rest) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.1.0
Updated: 2026-09-24 (session 7)

## Current: v3.2.0 (feat/zip-apk-upload) — upload an artifact zip holding one APK; aapt → aapt2
🔢 VERSION    ✅ 3.2.0 MINOR (user: "2" = release as v3.2.0); VERSION, compose.yaml images + comment,
              README links/compose block all 3.2.0; prior tag v3.1.0 → e9da85a on origin
🔨 BUILD      ✅ final tree: 337 pytest passed (py3.13), tree unchanged during run
              app image from working tree (aapt2: ldd resolves, 2.20-15087165), throwaway zipg2-app, gen creds:
              artifact.zip staged "from artifact.zip"; bare dup refused; unsigned-in-zip, 2-APK, no-APK refused;
              blind.zip (was aapt failure) staged; staged dir + spool clean after each; UI v3.2.0. Removed
  test artifact: ⬜ rebuild from the exact PR head commit before merge
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              zip: never extracted by name, one APK only, no recursion, 1000-entry cap, declared + metered
              inflate cap, encrypted refused, zip deleted by os.replace before APK checks
              ✅ fixed: 💡 Low — corrupt deflate stream → 500; regression test fails without fix (verified)
              ✅ fixed: 📝 quality (pre-existing) — legacy aapt failed on current-SDK APKs → aapt2 (same r37 zip)
              bandit: only the 6 known B608 in db.py (unchanged) — withdrawn as before; pip-audit --strict clean
📄 DOCS       ✅ CHANGELOG 3.2.0; README Sources upload + Manual uploads (zip rules), aapt2 refs; form hint
              sources.png screenshot predates the one-sentence hint change (not a behavior claim)
📦 RELEASE    ✅ commit + release notes approved (user: "yes", 2026-09-24); notes = CHANGELOG 3.2.0
              branch synced (0 behind origin/main); PR opened from this commit
🚀 SHIP       ⏳ plan: test images from PR head → CI green → merge → user pushes tag v3.2.0 → verify release

## Previous: v3.1.0 (feat/simpler-flow, PR #11)
🔢 VERSION    ✅ 3.1.0 (user: "opus, 3.1.0"); VERSION, compose.yaml images + README block/links all 3.1.0
              prior tag v3.0.0 on origin
🔨 BUILD      ✅ throwaway stack shots31 built from the working tree (compose.build.yaml), app healthy
              every page checked in a browser (desktop + phone, Dark): status, sources, devices, install,
              install status, installs, audit, settings; no horizontal overflow; UI shows v3.1.0
              321 pytest passed (py3.13 container), incl. 17 new in tests/test_flow.py; stack + data removed
  test artifact: adb-server-test/app:0e3e682 (d9b70203a5ab), adb-server-test/adb-server:0e3e682 (af63c19435b9)
              @ 0e3e682 (PR #11 head, git archive): stack rel310 healthy, sign-in, 7 pages 200, old URLs redirect,
              setup checklist + v3.1.0 shown, app → adb-server `adb devices` OK
🔒 SECURITY   ✅ 0 open. bandit: only the 6 known B608 in db.py (file unchanged) — withdrawn as before
              pip-audit --strict clean; Dependabot 0 open; every new form has csrf_token;
              push-latest `back` allow-listed {/status,/install}; old-URL redirects fixed-path
📄 DOCS       ✅ CHANGELOG 3.1.0; README quickstart (stack dir vs data dir, tool-neutral), features in flow order,
              upgrade/1.x notes removed (user), 12 screenshots retaken in Dark; HANDOFF lessons
📦 RELEASE    ✅ PR #11, CI green on 0e3e682; notes = CHANGELOG 3.1.0 (user merged after the approval ask: "done")
🚀 SHIP       ✅ v3.1.0 shipped 2026-09-23 — PR #11 MERGED → e9da85a (user-driven); CI green on merge commit
              tag v3.1.0 → e9da85a (user-driven, verified ls-remote ^{}); release run success; release published
              old branches deleted (user-driven); dependabot/python-3.14 kept (PR #2 open)
