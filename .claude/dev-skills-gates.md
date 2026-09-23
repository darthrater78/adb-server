# Dev Skills gate state
Track: release sequence → v3.1.0 (simpler flow: Status · Sources · Devices · Install · Settings; quickstart stack/data split)
Mode: manual (chosen 2026-09-23, session 6)
Model: Opus 5.5 approved by user for v3.1.0 redesign ("opus, 3.1.0")
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest documented (README Data at rest) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5 (pinned ghcr 3.0.0, bottom comments, compose.yaml quickstart)
Version: 3.0.0
Updated: 2026-09-23 (session 6)

## Current: v3.1.0 (feat/simpler-flow, branch not yet created)
🔢 VERSION    ✅ 3.1.0 (user: "opus, 3.1.0"); VERSION, compose.yaml images + README block/links all 3.1.0
              prior tag v3.0.0 on origin
🔨 BUILD      ✅ throwaway stack shots31 built from the working tree (compose.build.yaml), app healthy
              every page checked in a browser (desktop + phone, Dark): status, sources, devices, install,
              install status, installs, audit, settings; no horizontal overflow; UI shows v3.1.0
              321 pytest passed (py3.13 container), incl. 17 new in tests/test_flow.py; stack + data removed
  test artifact: ⬜ rebuild from the exact PR head commit before merge
🔒 SECURITY   ✅ 0 open. bandit: only the 6 known B608 in db.py (file unchanged) — withdrawn as before
              pip-audit --strict clean; Dependabot 0 open; every new form has csrf_token;
              push-latest `back` allow-listed {/status,/install}; old-URL redirects fixed-path
📄 DOCS       ✅ CHANGELOG 3.1.0; README quickstart (stack dir vs data dir, tool-neutral), features in flow order,
              upgrade/1.x notes removed (user), 12 screenshots retaken in Dark; HANDOFF lessons
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Previous: docs/compose-quickstart — PR #10 MERGED (work commit, docs only)
🔢 VERSION    ➖ N/A — no bump; compose image tag pinned to released 3.0.0 (matches VERSION)
🔨 BUILD      ✅ throwaway stack `qs` from compose.yaml on published ghcr app/adb-server:3.0.0
              both healthy; Host=<IP>/<hostname> 200, unlisted host 400; plain-HTTP sign-in → /status 200
              .env block run in scratch (password with $ # space survives); stack + files removed
              compose.yaml + compose.build.yaml pass `docker compose config`
🔒 SECURITY   ✅ 0 open. Hardening unchanged (cap_drop, no-new-privileges, read_only, no host net)
              fixed: 💡 Low — quickstart password on command line/history → read -s prompt, no cat of .env
📄 DOCS       ✅ CHANGELOG Unreleased; README quickstart/upgrade/Configuration/Development
📦 RELEASE    ➖ N/A — no version/artifact/tag involved
🚀 SHIP       ➖ N/A — no version/artifact/tag involved

## Previous: v3.0.0
🔢 VERSION    ✅ 3.0.0 — MAJOR (feature + published mdns image removed); user said "continue" to 3.0.0
              VERSION is the only version source (UI reads it); README top now links repo + releases/tag/v3.0.0
              prior tag v2.0.0 on origin
🔨 BUILD      ✅ compose stack rel300 from the working tree; handoff offered
              app healthy, adb-server answers; both on rel300_internal, no host networking
              sign-in → status/devices/repos/staged/settings/audit/installs 200; /devices/qr/* 404; UI shows v3.0.0
              304 pytest passed (py3.13); tree unchanged during the run
  test artifact: local images adb-server-test/app:3f8de27 (2144ea10f376), adb-server-test/adb-server:3f8de27 (1ed7f792ce4b) @ 3f8de27 (PR #9 head)
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              Diff is removal-only (mdns sidecar, QR pairing, auto-trust); no new logic
              deps: pip-audit --strict clean (requirements-dev.txt); Dependabot alerts: 0 open
              withdrawn: bandit B608 ×6 in db.py (unchanged since v2.0.0) — constant fragments / schema column names
📄 DOCS       ✅ CHANGELOG 3.0.0 (breaking + upgrade steps); README: QR/mdns removed, 2-container
              architecture, "Upgrading from 2.x" block, security notes; HANDOFF: no-host-networking rule
📦 RELEASE    ✅ PR #9 open, CI green on 3f8de27; notes approved by user ("good notes")
🚀 SHIP       ✅ v3.0.0 shipped 2026-09-23
              PR #9 MERGED → 332b0a3; CI + Lint green on merge commit; tag v3.0.0 → 332b0a3 (user-driven)
              release run 35880704430 success; release v3.0.0 published, Latest, notes from CHANGELOG
              ghcr app:3.0.0 + adb-server:3.0.0 pullable, app:latest = 3.0.0 digest; no mdns:3.0.0
              (held in working tree — folds into the next release PR)
