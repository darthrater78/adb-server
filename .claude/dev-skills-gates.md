# Dev Skills gate state
Track: release sequence → v2.0.0 on main (branch release/v2.0.0)
Mode: semi-autonomous (approved 2026-09-23, session 3) — commits and the tag still require the user's approval
Model: Opus 5.5 approved by user for this session's tasks (above Sonnet ceiling)
Origin: darthrater78/adb-server (not a fork)
Version: 2.0.0
Updated: 2026-09-23 (session 3)

🔢 VERSION    ✅ 2.0.0 — MAJOR chosen by user (954a7b5 chore(compose)! breaking: named volumes → bind mounts)
              VERSION is the only version source; UI reads it (header shows v2.0.0 + releases/tag/v2.0.0 link)
              prior tag v1.0.0 on origin → 8cc08ec
🔨 BUILD      ✅ compose stack rel200 from the release tree (VERSION 2.0.0), bind mounts on scratch dirs owned 10001:
              3/3 healthy; adbkey, app.db, services.json written; adb via adb-server OK; apksigner 0.9, aapt OK;
              sign-in → status/devices/repos/staged/settings/audit all 200; 319 pytest passed (py3.13, tree unchanged
              during run); handoff offered
              test artifact: local compose images (rel200, 127.0.0.1:18081) 27d1852828e1 2be62838d7d2 61add17f1795 @ release tree
              test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              Diff since v1.0.0: compose (bind mounts, comments moved), README, CHANGELOG, HANDOFF, one test helper.
              No app code changed. Container hardening unchanged (uid 10001, cap_drop ALL, no-new-privileges,
              read_only, adb-server unpublished, mdns :ro in app); key/DB dirs chmod 700 in docs.
              deps: pip-audit --strict clean (app + mdns + dev); Dependabot alerts: 0 open.
              ✅ fixed L (PR #5): flaky duplicate-upload test — wall-clock zip timestamp → fixed ZipInfo.date_time
              Note: Dependabot PRs #2/#3 (python 3.13 → 3.14 base images) open; version bumps, no advisory.
📄 DOCS       ✅ CHANGELOG 2.0.0 — 2026-09-23 (breaking + migration, comments move, test fix);
              README quickstart bind-mount setup + "Upgrading from 1.x" block; data-at-rest/backup paths;
              HANDOFF updated. Release-notes awk from release.yml extracts the 2.0.0 section.
📦 RELEASE    ⏳ awaiting the single commit approval (version + notes)
🚀 SHIP       ⏳ plan: PR → CI green → merge (no --delete-branch) → CI green on merge commit → version guard →
              user pushes tag v2.0.0 → watch release.yml → verify release page + ghcr 2.0.0 images

Carried (v1.0.0 SHIP, folded in here): PR #1 merged 8cc08ec; tag v1.0.0^{}=8cc08ec; release run 35869574512
  success; ghcr app/adb-server/mdns:1.0.0 pullable.
Session 3 work commits: PR #4 (bind mounts), #5 (flaky test), #6 (handoff) merged → main 90fbe82.
