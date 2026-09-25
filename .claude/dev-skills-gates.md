# Dev Skills gate state
Track: release sequence → dev build v3.7.0-dev.1 from claude/dev-skills-workflow-hf1ddt (user: "dev release on this branch. Build a container and publish but no release"); v3.7.0 itself later, via PR
Mode: semi-autonomous (approved 2026-09-25, session 12) — commits and the tag still require the user's approval
Skill: v2.39.0 (current)
Model: Opus 5.5 (session 12, user approved for this task)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.7.0
Updated: 2026-09-25 (session 12: built, gates 1–4 passed, awaiting commit approval)

## Current: v3.7.0 — one push flow, Update all, trust after pairing, every block folds
🔢 VERSION    ✅ 3.7.0 MINOR (feat, no breaking change); bump to be confirmed with the commit approval
              VERSION, compose.yaml images + comment, README link + compose block + upgrade note, CHANGELOG
              prior tag v3.6.0 on origin (ls-remote)
🔨 BUILD      ✅ working tree: 616 pytest passed (35 new in test_workflow.py), tree unchanged during run
              env: docker info → daemon unreachable in this container; app run from the tree instead
              (mock_github + seed, uvicorn, Chromium 1194): Status, Update all dialog, Install + confirm,
              Devices trust offer, Sources, Builds, Settings, progress page, desktop + phone checked by eye,
              no sideways scroll, a fold closes on click; all README screenshots retaken at 3.7.0
              ⬜ test artifact from the PR head still owed before merge (no Docker here)
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              pip-audit --require-hashes --strict clean (no dependency change); bandit -ll clean
              review: `back` rebuilt server-side (path allowlist + known serial), never echoed; update-all
              CSRF-checked, each app through create_install (trust + CPU), audited per push; trust offer only
              for an existing untrusted device, nickname capped 100 + autoescaped; APK version/label in the
              push button escaped (test fails with `| safe`, passes without); batch ids capped at 50
              release.yml: a -suffix tag may build from any branch; publishes only :<full version> (no :latest,
              no :X.Y: metadata-action skips both for pre-releases), no GitHub release; still needs CI passed
              on the commit and VERSION = the tag's base; actionlint 1.7.12 clean
📄 DOCS       ✅ CHANGELOG 3.7.0 (+ dev builds); README: folding, one push flow, Update all, trust after pairing,
              Sources points at Install, screenshot captions, Development: dev builds; HANDOFF.md rewritten
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Previous: v3.6.0 (feat/redesign) — shipped: PR #16 merged → 7b2c42d, tag v3.6.0 on origin (ls-remote)
