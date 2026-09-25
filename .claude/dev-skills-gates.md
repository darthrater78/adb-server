# Dev Skills gate state
Track: release sequence → v3.7.1
Mode: semi-autonomous (approved 2026-09-25, session 14) — commits and the tag still require the user's approval
Skill: v2.39.0 ⚠️ outdated (latest v2.39.1; user chose to continue)
Model: Opus 5.5 (session 14, user approved: "Stay on Opus")
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.7.1
Updated: 2026-09-25 (session 14: gate 1 passed)

## Current: v3.7.1 — Builds opens on the newest; dev builds report their full version
Branch fix/builds-newest-first (from main e4087c9)
🔢 VERSION    ✅ 3.7.1 PATCH (two fixes, no feat, no breaking change); prior tag v3.7.0 on origin (ls-remote)
              VERSION, compose.yaml images + comment, README link + compose block + update note, CHANGELOG all 3.7.1
🔨 BUILD      ⏳ 630 pytest pass (py3.13, requirements-dev hashes), tree unchanged during run
              mock-GitHub stack (weather-app: 4 releases, 2 commits) shown to user at http://10.0.0.252:18090; torn down
              release.yml stamp step dry-run: v3.7.1-dev.1 → 3.7.1-dev.1, v3.7.1 → 3.7.1
  test artifact: ⬜ local images built from the release commit, before merge
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              bandit -ll app: 0 results; pip-audit --strict --require-hashes: clean; Dependabot 0 open
              release notes rendered autoescaped in <pre>; tag in href urlencoded under a fixed https://github.com base;
              release.yml TAG passed via env, written with printf '%s' (no shell splice); no new routes
📄 DOCS       ✅ CHANGELOG 3.7.1; README Builds (opens on newest, older folds), version check (dev builds report
              their full version), dev builds paragraph
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Shipped: v3.7.0 — SHIP ✅ 2026-09-25: tag v3.7.0 → e4087c9 (user-driven), release run 36174930358 success,
## GitHub release published = latest, ghcr app/adb-server 3.7.0 = 3.7 = latest. Pending (user): delete merged
## branch claude/dev-skills-workflow-hf1ddt
## Previous: v3.7.0-dev.1 — dev images from 92069da (tag user-driven); ghcr app/adb-server:3.7.0-dev.1
