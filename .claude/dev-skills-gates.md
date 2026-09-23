# Dev Skills gate state
Track: release sequence → v3.0.0 (remove host networking: mdns sidecar + QR pairing)
Mode: manual (chosen 2026-09-23, session 5)
Model: Opus 5.5 approved by user for this task (above Sonnet ceiling)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest documented (README Data at rest) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ bind mounts
Version: 3.0.0
Updated: 2026-09-23 (session 5)

🔢 VERSION    ✅ 3.0.0 — MAJOR (feature + published mdns image removed); user said "continue" to 3.0.0
              VERSION is the only version source (UI reads it); README top now links repo + releases/tag/v3.0.0
              prior tag v2.0.0 on origin
🔨 BUILD      ✅ compose stack rel300 from the working tree; handoff offered
              app healthy, adb-server answers; both on rel300_internal, no host networking
              sign-in → status/devices/repos/staged/settings/audit/installs 200; /devices/qr/* 404; UI shows v3.0.0
              304 pytest passed (py3.13); tree unchanged during the run
  test artifact: local compose images rel300-app d1f1ac05fc36, rel300-adb-server 4cd259d92519 @ working tree on 9982db1
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              Diff is removal-only (mdns sidecar, QR pairing, auto-trust); no new logic
              deps: pip-audit --strict clean (requirements-dev.txt); Dependabot alerts: 0 open
              withdrawn: bandit B608 ×6 in db.py (unchanged since v2.0.0) — constant fragments / schema column names
📄 DOCS       ✅ CHANGELOG 3.0.0 (breaking + upgrade steps); README: QR/mdns removed, 2-container
              architecture, "Upgrading from 2.x" block, security notes; HANDOFF: no-host-networking rule
📦 RELEASE    ⏳ awaiting commit approval
🚀 SHIP       ⬜
