# Dev Skills gate state
Track: release sequence → v3.7.0 (user: "its good tear down the container and lets merge to main for the next version")
Mode: semi-autonomous (approved 2026-09-25, session 13) — commits and the tag still require the user's approval
Skill: v2.39.0 (current)
Model: Opus 5.5 (session 13, user approved: "Stay on Opus")
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.7.0
Updated: 2026-09-25 (session 13: gates 1–4 passed on the final tree; awaiting commit + release-notes approval)

## Current: v3.7.0 — one push flow, Update all, trust after pairing, folded cards, one accent, restage fix
Session 12 (92069da, 3241cfd on the branch) + session 13 (uncommitted: folded-by-default, card-per-item
Sources/Devices, Install summary cards, one accent colour, content-hashed style.css, restage fix, docs)
🔢 VERSION    ✅ 3.7.0 MINOR (feat + fix, no breaking change); prior tag v3.6.0 on origin (ls-remote)
              VERSION, compose.yaml images + comment, README link + compose block, CHANGELOG all 3.7.0
🔨 BUILD      ✅ all pytest pass (py3.13, requirements-dev hashes); handoff offered, user tried it ("its good")
              test stack dev-skills-test-adbs from the working tree at http://10.0.0.252:18090 (torn down);
              headless Chromium/Firefox/WebKit: accent applies; desk 1400 + phone 390, Dark + Flashbang: nothing
              open on load outside Settings, 0 page overflow, Sources/Devices 0 inner scrollbars
  test artifact: ⬜ local images built from the release commit, before merge
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              bandit -ll clean; pip-audit --strict --require-hashes clean; Dependabot 0 open
              restage upsert + has_staged_release parameterized; revive only WHERE pruned_at IS NOT NULL (a live
              duplicate is still refused, tested); colours still normalize()d to #rrggbb before any CSS (injection
              test kept); no new routes; session-12 review (back allowlist, update-all, trust offer) unchanged
📄 DOCS       ✅ CHANGELOG 3.7.0: folded-by-default, cards not tables, Install summaries, one accent, restage fix,
              stylesheet caching fix; README: Features folding, Sources cards + Check now restage, Devices cards +
              Find, Install summary cards, Appearance one accent; all 17 screenshots retaken (run.sh, first card
              opened); compose quickstart unchanged and pinned 3.7.0
📦 RELEASE    ✅ commit + release notes (CHANGELOG 3.7.0) approved (user: "yes", 2026-09-25); branch 0 behind main;
              PR #17 open (draft → ready after this push)
🚀 SHIP       ⏳ plan: CI green on the release commit → test artifact built → merge PR #17 (no --delete-branch)
              → user pushes tag v3.7.0 → release run → verify images + :latest + GitHub release

## Previous: v3.7.0-dev.1 — dev images from 92069da (tag user-driven); ghcr app/adb-server:3.7.0-dev.1
## Previous: v3.6.0 (feat/redesign) — shipped: PR #16 merged → 7b2c42d, tag v3.6.0 on origin (ls-remote)
